#!/usr/bin/env python3
"""Exact-candidate PostgreSQL bulk and incremental Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import islice
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast

from psycopg import Connection, OperationalError, connect, sql
from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from dander import __version__
from dander.concurrency import FencingToken, TargetFence
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.postgresql.transform import PostgreSQLTransformRunner
from dander.providers.postgresql.writer import PostgreSQLCopyWriter
from dander.qualification import (
    ApprovedCostCeiling,
    ApprovedObjectiveSet,
    BenchmarkClass,
    BenchmarkWorkload,
    ObjectiveResult,
    ObjectiveStatus,
    QualificationContext,
    QualificationReport,
    QualificationStatus,
)
from dander.state import StateRuntime
from dander.telemetry import CostAttribution, PerformanceMeasurement, RunPerformance
from dander.warehouse import RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteMode, WritePattern, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]

_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.postgresql-bulk-incremental/v1"
_CORRECTNESS_CONFIG_SCHEMA = "io.dander.phase8.postgresql-correctness/v1"
_CORRECTNESS_FIXTURE = "postgresql-scd1-normalized-v1"
_CORRECTNESS_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "exact_normalized_output",
    "replay_equal",
    "scd1_copy_completion",
)
_BULK_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "narrow_copy_completion",
    "narrow_throughput_measurement",
    "wide_copy_completion",
    "wide_throughput_measurement",
)
_INCREMENTAL_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "delta_target_ratio",
    "exact_result",
    "incremental_cursor_monotonic",
    "incremental_throughput_measurement",
)
_TRANSFORM_OBJECTIVES = (
    "aggregation_exact",
    "cleanup",
    "cost_ceiling",
    "generic_tests",
    "incremental_merge",
    "join_exact",
    "scan_exact",
)
_FAILURE_OBJECTIVES = (
    "bounded_pool_timeout",
    "cleanup",
    "cost_ceiling",
    "dropped_connection_recovery",
    "state_operation_recovery",
    "warehouse_cancellation_rollback",
)


@dataclass(frozen=True, slots=True)
class Phase8PostgreSQLConfig:
    """Bounded deterministic workload sizes for two required benchmark classes."""

    narrow_rows: int = 500_000
    narrow_payload_bytes: int = 32
    wide_rows: int = 200_000
    wide_payload_bytes: int = 1_024
    incremental_seed_rows: int = 300_000
    incremental_delta_rows: int = 3_000
    incremental_payload_bytes: int = 128
    transform_fact_rows: int = 100_000
    transform_dimension_rows: int = 100
    batch_rows: int = 1_000

    def __post_init__(self) -> None:
        for name in (
            "narrow_rows",
            "narrow_payload_bytes",
            "wide_rows",
            "wide_payload_bytes",
            "incremental_seed_rows",
            "incremental_delta_rows",
            "incremental_payload_bytes",
            "transform_fact_rows",
            "transform_dimension_rows",
            "batch_rows",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.incremental_delta_rows % 2:
            raise ValueError("incremental_delta_rows must be even")
        if self.incremental_delta_rows > self.incremental_seed_rows:
            raise ValueError("incremental_delta_rows must not exceed incremental_seed_rows")
        if self.transform_fact_rows < self.transform_dimension_rows:
            raise ValueError("transform_fact_rows must not be smaller than transform dimensions")
        if self.batch_rows > min(self.narrow_rows, self.wide_rows, self.incremental_seed_rows):
            raise ValueError("batch_rows must not exceed the smallest seeded workload")
        if self.batch_rows * max(self.wide_payload_bytes, self.incremental_payload_bytes) > (
            256 * 1_024 * 1_024
        ):
            raise ValueError("one configured batch must not exceed 256 MiB of payload")

    def workload_payload(self, benchmark_class: BenchmarkClass) -> dict[str, object]:
        """Return the exact approval-bound configuration for one benchmark class."""
        common: dict[str, object] = {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": benchmark_class.value,
            "batch_rows": self.batch_rows,
        }
        if benchmark_class is BenchmarkClass.CORRECTNESS:
            return {
                **common,
                "schema": _CORRECTNESS_CONFIG_SCHEMA,
                "batch_rows": 3,
                "fixture": _CORRECTNESS_FIXTURE,
                "expected_normalized_sha256": _correctness_expected_sha256(),
                "input_rows": 7,
                "output_rows": 3,
            }
        if benchmark_class is BenchmarkClass.BULK_THROUGHPUT:
            return {
                **common,
                "narrow_rows": self.narrow_rows,
                "narrow_payload_bytes": self.narrow_payload_bytes,
                "wide_rows": self.wide_rows,
                "wide_payload_bytes": self.wide_payload_bytes,
            }
        if benchmark_class is BenchmarkClass.INCREMENTAL:
            return {
                **common,
                "seed_rows": self.incremental_seed_rows,
                "delta_rows": self.incremental_delta_rows,
                "payload_bytes": self.incremental_payload_bytes,
            }
        if benchmark_class is BenchmarkClass.TRANSFORM:
            return {
                **common,
                "schema": "io.dander.phase8.postgresql-transform/v1",
                "batch_rows": self.transform_fact_rows,
                "fact_rows": self.transform_fact_rows,
                "dimension_rows": self.transform_dimension_rows,
                "delta_rows": 2,
                "models": ["scan", "join", "aggregation", "incremental"],
                "generic_tests": ["accepted_values", "not_null", "unique"],
            }
        if benchmark_class is BenchmarkClass.FAILURE:
            return {
                **common,
                "schema": "io.dander.phase8.postgresql-failure/v1",
                "batch_rows": 1,
                "probes": [
                    "bounded_pool_timeout",
                    "dropped_connection_recovery",
                    "state_operation_recovery",
                    "warehouse_cancellation_rollback",
                ],
                "pool_timeout_ms": 100,
                "cancellation_sleep_seconds": 30,
            }
        raise ValueError(
            "Phase 8 PostgreSQL harness supports correctness, bulk, incremental, transform, "
            "and failure"
        )

    def configuration_sha256(self, benchmark_class: BenchmarkClass) -> str:
        """Hash the canonical class configuration used by objective approval."""
        encoded = json.dumps(
            self.workload_payload(benchmark_class),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    """Immutable candidate coordinates shared by both reports."""

    release_version: str
    git_commit: str
    image_digest: str
    approval_reference: str
    benchmark_date: date
    launcher: str
    regions: tuple[str, ...]
    secret_provider: str
    provider_job_ids: tuple[str, ...]
    service_shapes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Approval:
    objectives: ApprovedObjectiveSet
    cost_ceiling: ApprovedCostCeiling


@dataclass(frozen=True, slots=True)
class _BulkResult:
    duration_ms: int
    peak_rss_bytes: int
    narrow_duration_ms: int
    narrow_rows: int
    narrow_logical_bytes: int
    wide_duration_ms: int
    wide_rows: int
    wide_logical_bytes: int
    temporary_staging_relations: int
    cleanup_verified: bool


@dataclass(frozen=True, slots=True)
class _CorrectnessResult:
    duration_ms: int
    peak_rss_bytes: int
    input_rows: int
    output_rows: int
    logical_input_bytes: int
    normalized_sha256: str
    temporary_staging_relations: int
    cleanup_verified: bool


@dataclass(frozen=True, slots=True)
class _IncrementalResult:
    duration_ms: int
    peak_rss_bytes: int
    seed_duration_ms: int
    seed_rows: int
    seed_logical_bytes: int
    delta_duration_ms: int
    delta_rows: int
    delta_logical_bytes: int
    final_rows: int
    regression_rows_affected: int
    temporary_staging_relations: int
    cleanup_verified: bool


@dataclass(slots=True)
class _Ownership:
    fence: FencingToken
    verifications: int = 0

    def verify(self) -> None:
        self.verifications += 1


@dataclass(frozen=True, slots=True)
class _TransformResult:
    duration_ms: int
    peak_rss_bytes: int
    input_rows: int
    logical_input_bytes: int
    output_rows: int
    model_count: int
    assertion_count: int
    ownership_verifications: int
    temporary_staging_relations: int
    cleanup_verified: bool


@dataclass(frozen=True, slots=True)
class _FailureResult:
    duration_ms: int
    peak_rss_bytes: int
    probe_count: int
    pool_timeout_duration_ms: int
    connection_recovery_duration_ms: int
    cancellation_duration_ms: int
    temporary_staging_relations: int
    cleanup_verified: bool


def load_approval(
    path: Path,
    *,
    config: Phase8PostgreSQLConfig,
    benchmark_class: BenchmarkClass,
) -> _Approval:
    """Load and validate one pre-mutation objective approval manifest."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective manifest has an incompatible schema")
    expected_workload = config.workload_payload(benchmark_class)
    if payload.get("workload") != expected_workload:
        raise ValueError("objective manifest workload does not match the benchmark configuration")
    raw_cost = payload.get("cost_ceiling")
    raw_objectives = payload.get("approved_objectives")
    if not isinstance(raw_cost, dict) or not isinstance(raw_objectives, dict):
        raise ValueError("objective manifest is incomplete")
    expected_names = {
        BenchmarkClass.CORRECTNESS: _CORRECTNESS_OBJECTIVES,
        BenchmarkClass.BULK_THROUGHPUT: _BULK_OBJECTIVES,
        BenchmarkClass.INCREMENTAL: _INCREMENTAL_OBJECTIVES,
        BenchmarkClass.TRANSFORM: _TRANSFORM_OBJECTIVES,
        BenchmarkClass.FAILURE: _FAILURE_OBJECTIVES,
    }.get(benchmark_class)
    if expected_names is None:
        raise ValueError("objective manifest selects an unsupported benchmark class")
    if tuple(raw_objectives.get("names", ())) != expected_names:
        raise ValueError("objective manifest does not contain the required objective set")
    objectives = ApprovedObjectiveSet(
        names=expected_names,
        benchmark_class=BenchmarkClass(str(raw_objectives.get("benchmark_class"))),
        profile_id=str(raw_objectives.get("profile_id")),
        release_version=str(raw_objectives.get("release_version")),
        git_commit=str(raw_objectives.get("git_commit")),
        image_digest=str(raw_objectives.get("image_digest")),
        configuration_sha256=str(raw_objectives.get("configuration_sha256")),
        approval_reference=str(raw_objectives.get("approval_reference")),
    )
    if objectives.benchmark_class is not benchmark_class:
        raise ValueError("objective manifest benchmark class does not match")
    if objectives.configuration_sha256 != config.configuration_sha256(benchmark_class):
        raise ValueError("objective manifest configuration hash does not match")
    hosted_gke = objectives.profile_id == "gke_standard_postgresql"
    if hosted_gke:
        configuration = _mapping(payload.get("configuration"), "configuration")
        execution = _mapping(configuration.get("execution"), "execution configuration")
        if execution.get("harness_sha256") != _file_sha256(Path(__file__)):
            raise ValueError("objective manifest does not match the protected PostgreSQL harness")
        if execution.get("manual_candidate_executions") != 1:
            raise ValueError("objective manifest must allow exactly one candidate execution")
        if execution.get("automatic_candidate_retry") is not False:
            raise ValueError("objective manifest must disable automatic candidate retry")
        if execution.get("provider_operation_retries") != 0:
            raise ValueError("objective manifest must disable provider-operation retries")
    cost_ceiling = ApprovedCostCeiling(
        amount_usd=Decimal(str(raw_cost.get("amount_usd"))),
        approval_reference=str(raw_cost.get("approval_reference")),
    )
    expected_cost_ceiling = Decimal("0.50") if hosted_gke else Decimal(0)
    if cost_ceiling.amount_usd != expected_cost_ceiling:
        raise ValueError("PostgreSQL cost ceiling does not match its selected profile")
    if cost_ceiling.approval_reference != objectives.approval_reference:
        raise ValueError("cost and objective approvals must use the same reference")
    return _Approval(objectives=objectives, cost_ceiling=cost_ceiling)


def run_phase8_postgresql_qualification(
    dsn: str,
    *,
    config: Phase8PostgreSQLConfig,
    identity: CandidateIdentity,
    correctness_approval: _Approval,
    bulk_approval: _Approval,
    incremental_approval: _Approval,
    transform_approval: _Approval,
    failure_approval: _Approval,
) -> tuple[
    QualificationReport,
    QualificationReport,
    QualificationReport,
    QualificationReport,
    QualificationReport,
]:
    """Execute five PostgreSQL qualification classes in disposable schemas."""
    if not dsn:
        raise ValueError("PostgreSQL qualification requires a non-empty DSN")
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_identity_match(identity, correctness_approval)
    _require_identity_match(identity, bulk_approval)
    _require_identity_match(identity, incremental_approval)
    _require_identity_match(identity, transform_approval)
    _require_identity_match(identity, failure_approval)
    pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=5,
            timeout=10,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=10)
    try:
        with pool.connection() as connection:
            row = connection.execute("SELECT current_database() AS database").fetchone()
        if row is None or not isinstance(row["database"], str):
            raise RuntimeError("PostgreSQL qualification could not read the database name")
        database = row["database"]
        warehouse = _warehouse_runtime(pool, database)
        correctness = _run_correctness(pool, warehouse, database=database)
        bulk = _run_bulk(pool, warehouse, database=database, config=config)
        incremental = _run_incremental(pool, warehouse, database=database, config=config)
        transform = _run_transform(pool, warehouse, database=database, config=config)
        failure = _run_failure(dsn, pool, database=database)
        return (
            _correctness_report(config, identity, correctness_approval, correctness),
            _bulk_report(config, identity, bulk_approval, bulk),
            _incremental_report(config, identity, incremental_approval, incremental),
            _transform_report(config, identity, transform_approval, transform),
            _failure_report(config, identity, failure_approval, failure),
        )
    finally:
        pool.close()


def run_postgresql_correctness_qualification(
    dsn: str,
    *,
    config: Phase8PostgreSQLConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    provider_cost_usd: Decimal | None = None,
) -> QualificationReport:
    """Run only the accepted PostgreSQL correctness cell and emit one normalized report."""
    if not dsn:
        raise ValueError("PostgreSQL correctness qualification requires a non-empty DSN")
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_identity_match(identity, approval)
    pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=5,
            timeout=10,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=10)
    try:
        with pool.connection() as connection:
            row = connection.execute("SELECT current_database() AS database").fetchone()
        if row is None or not isinstance(row["database"], str):
            raise RuntimeError("PostgreSQL correctness could not read the database name")
        database = row["database"]
        result = _run_correctness(
            pool,
            _warehouse_runtime(pool, database),
            database=database,
        )
    finally:
        pool.close()
    return _correctness_report(
        config,
        identity,
        approval,
        result,
        provider_cost_usd=provider_cost_usd,
    )


def run_postgresql_bulk_qualification(
    dsn: str,
    *,
    config: Phase8PostgreSQLConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    provider_cost_usd: Decimal | None = None,
) -> QualificationReport:
    """Run only the accepted PostgreSQL bulk-throughput cell."""
    if not dsn:
        raise ValueError("PostgreSQL bulk qualification requires a non-empty DSN")
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_identity_match(identity, approval)
    pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=5,
            timeout=10,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=10)
    try:
        with pool.connection() as connection:
            row = connection.execute("SELECT current_database() AS database").fetchone()
        if row is None or not isinstance(row["database"], str):
            raise RuntimeError("PostgreSQL bulk qualification could not read the database name")
        database = row["database"]
        result = _run_bulk(
            pool,
            _warehouse_runtime(pool, database),
            database=database,
            config=config,
        )
    finally:
        pool.close()
    return _bulk_report(
        config,
        identity,
        approval,
        result,
        provider_cost_usd=provider_cost_usd,
    )


def _require_identity_match(identity: CandidateIdentity, approval: _Approval) -> None:
    objectives = approval.objectives
    if (
        objectives.release_version != identity.release_version
        or objectives.git_commit != identity.git_commit
        or objectives.image_digest != identity.image_digest
        or objectives.approval_reference != identity.approval_reference
    ):
        raise ValueError("objective approval does not match the exact candidate identity")


def _warehouse_runtime(pool: PostgreSQLPool, database: str) -> WarehouseRuntime:
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {
            "provider": "postgresql",
            "database": database,
            "dsn_env": "DANDER_PHASE8_POSTGRES_DSN",
            "statement_timeout_ms": 300_000,
            "lock_timeout_ms": 30_000,
            "idle_transaction_timeout_ms": 60_000,
        },
    )
    runtime = registry.build(ProviderKind.WAREHOUSE, config, context={"pool": pool})
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("PostgreSQL qualification received an invalid warehouse runtime")
    return runtime


def _failure_state_runtime(pool: PostgreSQLPool, schema: str) -> StateRuntime:
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.STATE,
        {
            "provider": "postgresql",
            "authority_id": f"postgresql:{schema}",
            "dsn_env": "DANDER_PHASE8_POSTGRES_DSN",
            "schema_name": schema,
            "pool_min_size": 1,
            "pool_max_size": 1,
            "pool_timeout_seconds": 0.1,
        },
    )
    runtime = registry.build(
        ProviderKind.STATE,
        config,
        context={"pool": pool, "metadata_enabled": False},
    )
    if not isinstance(runtime, StateRuntime):
        raise TypeError("PostgreSQL failure qualification received an invalid state runtime")
    return runtime


def _run_correctness(
    pool: PostgreSQLPool,
    warehouse: WarehouseRuntime,
    *,
    database: str,
) -> _CorrectnessResult:
    schema = f"dander_phase8_correctness_{uuid.uuid4().hex[:12]}"
    relation = RelationRef(catalog=database, namespace=schema, name="scd1_records")
    publication = warehouse.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="phase8_correctness",
            run_id="correctness-one",
            token=1,
            authority_id="postgresql:phase8-local",
        ),
    )
    writer = warehouse.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=3,
        schema_evolution=SchemaEvolution.STRICT,
    )
    if not isinstance(writer, PostgreSQLCopyWriter):
        raise RuntimeError("PostgreSQL correctness qualification did not select COPY")
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="label", data_type="STRING"),
            WriteField(name="sequence", data_type="INT64", mode="REQUIRED"),
        ),
        publication_fence=publication,
    )
    initial, update, expected = _correctness_fixture()
    encoded_input = json.dumps(
        [*initial, *update, *update], separators=(",", ":"), sort_keys=True
    ).encode()
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        writer.write(initial, target)
        writer.write(update, target)
        before_replay = _read_correctness_rows(pool, schema)
        writer.write(update, target)
        after_replay = _read_correctness_rows(pool, schema)
        if before_replay != expected or after_replay != expected:
            raise RuntimeError("PostgreSQL correctness normalized rows differ from the fixture")
        normalized_sha256 = hashlib.sha256(
            json.dumps(expected, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        if normalized_sha256 != _correctness_expected_sha256():
            raise RuntimeError("PostgreSQL correctness normalized hash differs from approval")
        staging = _temporary_staging_count(pool)
        if staging:
            raise RuntimeError(
                "PostgreSQL correctness qualification left temporary staging relations"
            )
    finally:
        _drop_schema(pool, schema)
    cleanup = not _schema_exists(pool, schema)
    if not cleanup:
        raise RuntimeError(
            "PostgreSQL correctness qualification did not remove its disposable schema"
        )
    return _CorrectnessResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        input_rows=len(initial) + len(update) * 2,
        output_rows=len(expected),
        logical_input_bytes=len(encoded_input),
        normalized_sha256=normalized_sha256,
        temporary_staging_relations=staging,
        cleanup_verified=cleanup,
    )


def _run_bulk(
    pool: PostgreSQLPool,
    warehouse: WarehouseRuntime,
    *,
    database: str,
    config: Phase8PostgreSQLConfig,
) -> _BulkResult:
    schema = f"dander_phase8_bulk_{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        narrow_rows, narrow_ms = _write_table(
            warehouse,
            database=database,
            schema=schema,
            table="narrow_records",
            pipeline_id="phase8_bulk_narrow",
            rows=config.narrow_rows,
            payload_bytes=config.narrow_payload_bytes,
            batch_rows=config.batch_rows,
        )
        wide_rows, wide_ms = _write_table(
            warehouse,
            database=database,
            schema=schema,
            table="wide_records",
            pipeline_id="phase8_bulk_wide",
            rows=config.wide_rows,
            payload_bytes=config.wide_payload_bytes,
            batch_rows=config.batch_rows,
        )
        _require_table_shape(
            pool,
            schema=schema,
            table="narrow_records",
            rows=config.narrow_rows,
            payload_bytes=config.narrow_payload_bytes,
        )
        _require_table_shape(
            pool,
            schema=schema,
            table="wide_records",
            rows=config.wide_rows,
            payload_bytes=config.wide_payload_bytes,
        )
        staging = _temporary_staging_count(pool)
        if staging:
            raise RuntimeError("PostgreSQL bulk qualification left temporary staging relations")
    finally:
        _drop_schema(pool, schema)
    cleanup = not _schema_exists(pool, schema)
    if not cleanup:
        raise RuntimeError("PostgreSQL bulk qualification did not remove its disposable schema")
    return _BulkResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        narrow_duration_ms=narrow_ms,
        narrow_rows=narrow_rows,
        narrow_logical_bytes=narrow_rows * (config.narrow_payload_bytes + 24),
        wide_duration_ms=wide_ms,
        wide_rows=wide_rows,
        wide_logical_bytes=wide_rows * (config.wide_payload_bytes + 24),
        temporary_staging_relations=staging,
        cleanup_verified=cleanup,
    )


def _run_incremental(
    pool: PostgreSQLPool,
    warehouse: WarehouseRuntime,
    *,
    database: str,
    config: Phase8PostgreSQLConfig,
) -> _IncrementalResult:
    schema = f"dander_phase8_incremental_{uuid.uuid4().hex[:12]}"
    relation = RelationRef(catalog=database, namespace=schema, name="incremental_records")
    publication = warehouse.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="phase8_incremental",
            run_id="incremental-one",
            token=1,
            authority_id="postgresql:phase8-local",
        ),
    )
    writer = warehouse.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=config.batch_rows,
        schema_evolution=SchemaEvolution.STRICT,
        mode=WriteMode.INCREMENTAL,
        cursor_field="cursor_value",
    )
    if not isinstance(writer, PostgreSQLCopyWriter):
        raise RuntimeError("PostgreSQL incremental qualification did not select COPY")
    target = _incremental_target(database, schema, publication)
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        seed_started = time.perf_counter()
        seed_rows = _write_batches(
            writer,
            _incremental_seed_records(config),
            target,
            batch_rows=config.batch_rows,
        )
        seed_ms = _elapsed_ms(seed_started)
        delta_started = time.perf_counter()
        delta_rows = _write_batches(
            writer,
            _incremental_delta_records(config),
            target,
            batch_rows=config.batch_rows,
        )
        delta_ms = _elapsed_ms(delta_started)
        regression_rows = writer.write(
            (
                {
                    "id": "000000000000",
                    "payload": "must-not-regress",
                    "cursor_value": 0,
                },
            ),
            target,
        )
        updated = config.incremental_delta_rows // 2
        inserted = config.incremental_delta_rows - updated
        final_rows = config.incremental_seed_rows + inserted
        _require_incremental_result(
            pool,
            schema=schema,
            expected_rows=final_rows,
            expected_updated=updated,
            expected_inserted=inserted,
            seed_rows=config.incremental_seed_rows,
            payload_bytes=config.incremental_payload_bytes,
        )
        if regression_rows != 0:
            raise RuntimeError("PostgreSQL incremental cursor regression changed the target")
        staging = _temporary_staging_count(pool)
        if staging:
            raise RuntimeError(
                "PostgreSQL incremental qualification left temporary staging relations"
            )
    finally:
        _drop_schema(pool, schema)
    cleanup = not _schema_exists(pool, schema)
    if not cleanup:
        raise RuntimeError(
            "PostgreSQL incremental qualification did not remove its disposable schema"
        )
    row_width = config.incremental_payload_bytes + 32
    return _IncrementalResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        seed_duration_ms=seed_ms,
        seed_rows=seed_rows,
        seed_logical_bytes=seed_rows * row_width,
        delta_duration_ms=delta_ms,
        delta_rows=delta_rows,
        delta_logical_bytes=delta_rows * row_width,
        final_rows=final_rows,
        regression_rows_affected=regression_rows,
        temporary_staging_relations=staging,
        cleanup_verified=cleanup,
    )


def _run_transform(
    pool: PostgreSQLPool,
    warehouse: WarehouseRuntime,
    *,
    database: str,
    config: Phase8PostgreSQLConfig,
) -> _TransformResult:
    suffix = uuid.uuid4().hex[:12]
    source_schema = f"dander_phase8_transform_source_{suffix}"
    target_schema = f"dander_phase8_transform_target_{suffix}"
    try:
        _seed_transform_sources(
            pool,
            source_schema=source_schema,
            target_schema=target_schema,
            config=config,
        )
        runner = warehouse.transforms.build_transform_runner(
            graph_plan=None,
            build_models=True,
            raw_namespace=source_schema,
        )
        if not isinstance(runner, PostgreSQLTransformRunner):
            raise RuntimeError(
                "PostgreSQL transform qualification did not select its native runner"
            )
    except Exception:
        _drop_schema(pool, target_schema)
        _drop_schema(pool, source_schema)
        raise
    first_ownership = _transform_ownership(database, run_id="transform-one", token=1)
    second_ownership = _transform_ownership(database, run_id="transform-two", token=2)
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        with TemporaryDirectory(prefix="dander-phase8-transform-") as temporary:
            models = Path(temporary)
            _write_transform_models(models, target_schema=target_schema)
            initial = runner.build(
                models,
                selected=("aggregate_records", "incremental_records"),
                ownership=first_ownership,
            )
            _require_transform_initial(
                pool,
                target_schema=target_schema,
                config=config,
            )
            with pool.connection() as connection:
                connection.execute(
                    sql.SQL("UPDATE {} SET amount = 999, updated_at = 2 WHERE id = 1").format(
                        sql.Identifier(source_schema, "facts")
                    )
                )
                connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (id, dimension_id, amount, updated_at) VALUES (%s, 1, 5, 2)"
                    ).format(sql.Identifier(source_schema, "facts")),
                    (config.transform_fact_rows + 1,),
                )
            replay = runner.build(
                models,
                selected=("incremental_records",),
                ownership=second_ownership,
            )
            _require_transform_incremental(
                pool,
                target_schema=target_schema,
                expected_rows=config.transform_fact_rows + 1,
            )
            tested = runner.test(
                models,
                selected=("aggregate_records", "incremental_records"),
            )
            assertion_count = initial.assertions + replay.assertions + tested.assertions
            model_count = len(initial.models)
        staging = _temporary_staging_count(pool)
        if staging:
            raise RuntimeError(
                "PostgreSQL transform qualification left temporary staging relations"
            )
    finally:
        _drop_schema(pool, target_schema)
        _drop_schema(pool, source_schema)
    cleanup = not _schema_exists(pool, target_schema) and not _schema_exists(pool, source_schema)
    if not cleanup:
        raise RuntimeError(
            "PostgreSQL transform qualification did not remove its disposable schemas"
        )
    input_rows = config.transform_fact_rows + config.transform_dimension_rows
    return _TransformResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        input_rows=input_rows,
        logical_input_bytes=(config.transform_fact_rows * 32)
        + (config.transform_dimension_rows * 24),
        output_rows=config.transform_fact_rows + 1,
        model_count=model_count,
        assertion_count=assertion_count,
        ownership_verifications=first_ownership.verifications + second_ownership.verifications,
        temporary_staging_relations=staging,
        cleanup_verified=cleanup,
    )


def _run_failure(
    dsn: str,
    pool: PostgreSQLPool,
    *,
    database: str,
) -> _FailureResult:
    schema = f"dander_phase8_failure_{uuid.uuid4().hex[:12]}"
    state_pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=1,
            timeout=0.1,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    state_pool.wait(timeout=5)
    runtime = _failure_state_runtime(state_pool, schema)
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        runtime.migrator.migrate()
        timeout_started = time.perf_counter()
        timed_out = False
        with state_pool.connection():
            try:
                runtime.watermarks.get("phase8", "pool-timeout")
            except PoolTimeout:
                timed_out = True
        pool_timeout_duration_ms = _elapsed_ms(timeout_started)
        if not timed_out or pool_timeout_duration_ms > 1_000:
            raise RuntimeError("PostgreSQL pool exhaustion did not fail within its bound")

        recovery_started = time.perf_counter()
        with state_pool.connection() as connection:
            row = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()
        if row is None or not isinstance(row["pid"], int):
            raise RuntimeError("PostgreSQL failure qualification could not identify its backend")
        with connect(dsn, autocommit=True) as killer:
            terminated = killer.execute(
                "SELECT pg_terminate_backend(%s)",
                (row["pid"],),
            ).fetchone()
        if terminated != (True,):
            raise RuntimeError("PostgreSQL failure qualification did not terminate its backend")
        lost_connection_observed = False
        try:
            runtime.watermarks.get("phase8", "lost-connection")
        except OperationalError:
            lost_connection_observed = True
        if not lost_connection_observed:
            raise RuntimeError("PostgreSQL state operation did not observe the lost connection")
        state_pool.wait(timeout=5)
        runtime.watermarks.set("phase8", "lost-connection", "recovered")
        if runtime.watermarks.get("phase8", "lost-connection") != "recovered":
            raise RuntimeError("PostgreSQL state operation did not recover")
        recovery_duration_ms = _elapsed_ms(recovery_started)

        with pool.connection() as connection:
            connection.execute(
                sql.SQL("CREATE TABLE {} (id BIGINT PRIMARY KEY, value BIGINT NOT NULL)").format(
                    sql.Identifier(schema, "cancellation_records")
                )
            )
            connection.execute(
                sql.SQL("INSERT INTO {} (id, value) VALUES (1, 1)").format(
                    sql.Identifier(schema, "cancellation_records")
                )
            )
        cancellation_started = time.perf_counter()
        backend_ids: Queue[int] = Queue(maxsize=1)

        def cancelled_transaction() -> bool:
            try:
                with pool.connection() as connection:
                    connection.execute(
                        sql.SQL("UPDATE {} SET value = 2 WHERE id = 1").format(
                            sql.Identifier(schema, "cancellation_records")
                        )
                    )
                    backend = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()
                    if backend is None or not isinstance(backend["pid"], int):
                        raise RuntimeError("PostgreSQL cancellation probe has no backend")
                    backend_ids.put(backend["pid"], timeout=5)
                    connection.execute("SELECT pg_sleep(30)")
            except QueryCanceled:
                return True
            return False

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(cancelled_transaction)
            backend_id = backend_ids.get(timeout=5)
            with pool.connection() as connection:
                cancelled = connection.execute(
                    "SELECT pg_cancel_backend(%s) AS cancelled",
                    (backend_id,),
                ).fetchone()
            if cancelled != {"cancelled": True} or not future.result(timeout=5):
                raise RuntimeError("PostgreSQL warehouse cancellation was not observed")
        cancellation_duration_ms = _elapsed_ms(cancellation_started)
        with pool.connection() as connection:
            rolled_back = connection.execute(
                sql.SQL("SELECT value FROM {} WHERE id = 1").format(
                    sql.Identifier(schema, "cancellation_records")
                )
            ).fetchone()
        if rolled_back != {"value": 1}:
            raise RuntimeError("PostgreSQL cancellation did not roll back its transaction")
        staging = _temporary_staging_count(pool)
        if staging:
            raise RuntimeError("PostgreSQL failure qualification left staging relations")
    finally:
        state_pool.close()
        _drop_schema(pool, schema)
    cleanup = not _schema_exists(pool, schema)
    if not cleanup:
        raise RuntimeError("PostgreSQL failure qualification did not remove its schema")
    return _FailureResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        probe_count=4,
        pool_timeout_duration_ms=pool_timeout_duration_ms,
        connection_recovery_duration_ms=recovery_duration_ms,
        cancellation_duration_ms=cancellation_duration_ms,
        temporary_staging_relations=staging,
        cleanup_verified=cleanup,
    )


def _transform_ownership(database: str, *, run_id: str, token: int) -> _Ownership:
    return _Ownership(
        FencingToken(
            lease_table=None,
            pipeline_id="phase8_transform",
            run_id=run_id,
            token=token,
            authority_id=f"postgresql:{database}-phase8-local",
        )
    )


def _seed_transform_sources(
    pool: PostgreSQLPool,
    *,
    source_schema: str,
    target_schema: str,
    config: Phase8PostgreSQLConfig,
) -> None:
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA {}; CREATE SCHEMA {}").format(
                sql.Identifier(source_schema),
                sql.Identifier(target_schema),
            )
        )
        connection.execute(
            sql.SQL(
                "CREATE TABLE {} (dimension_id BIGINT PRIMARY KEY, category TEXT NOT NULL)"
            ).format(sql.Identifier(source_schema, "dimensions"))
        )
        connection.execute(
            sql.SQL(
                "INSERT INTO {} (dimension_id, category) "
                "SELECT value, 'category_' || (value %% 10)::text "
                "FROM generate_series(0, %s) AS value"
            ).format(sql.Identifier(source_schema, "dimensions")),
            (config.transform_dimension_rows - 1,),
        )
        connection.execute(
            sql.SQL(
                "CREATE TABLE {} (id BIGINT PRIMARY KEY, dimension_id BIGINT NOT NULL, "
                "amount BIGINT NOT NULL, updated_at BIGINT NOT NULL)"
            ).format(sql.Identifier(source_schema, "facts"))
        )
        connection.execute(
            sql.SQL(
                "INSERT INTO {} (id, dimension_id, amount, updated_at) "
                "SELECT value, value %% %s, value %% 17, 1 "
                "FROM generate_series(1, %s) AS value"
            ).format(sql.Identifier(source_schema, "facts")),
            (config.transform_dimension_rows, config.transform_fact_rows),
        )


def _write_transform_models(root: Path, *, target_schema: str) -> None:
    models = {
        "scan_records": (
            "SELECT id, dimension_id, amount, updated_at FROM {{ ref('raw_facts') }}",
            "table",
            (
                ("id", "INT64"),
                ("dimension_id", "INT64"),
                ("amount", "INT64"),
                ("updated_at", "INT64"),
            ),
            "  - column: id\n    not_null: true\n    unique: true\n",
            "",
        ),
        "joined_records": (
            "SELECT facts.id, dimensions.category, facts.amount, facts.updated_at "
            "FROM {{ ref('scan_records') }} AS facts "
            "JOIN {{ ref('raw_dimensions') }} AS dimensions "
            "ON facts.dimension_id = dimensions.dimension_id",
            "table",
            (
                ("id", "INT64"),
                ("category", "STRING"),
                ("amount", "INT64"),
                ("updated_at", "INT64"),
            ),
            "  - column: category\n"
            "    accepted_values: [category_0, category_1, category_2, category_3, "
            "category_4, category_5, category_6, category_7, category_8, category_9]\n",
            "",
        ),
        "aggregate_records": (
            "SELECT category, SUM(amount) AS total_amount, COUNT(*) AS row_count "
            "FROM {{ ref('joined_records') }} GROUP BY category",
            "table",
            (("category", "STRING"), ("total_amount", "INT64"), ("row_count", "INT64")),
            "  - column: category\n    not_null: true\n    unique: true\n"
            "  - column: row_count\n    not_null: true\n",
            "",
        ),
        "incremental_records": (
            "SELECT id, category, amount, updated_at FROM {{ ref('joined_records') }}",
            "incremental",
            (
                ("id", "INT64"),
                ("category", "STRING"),
                ("amount", "INT64"),
                ("updated_at", "INT64"),
            ),
            "  - column: id\n    not_null: true\n    unique: true\n",
            "unique_key: [id]\nincremental_cursor: updated_at\n",
        ),
    }
    for name, (query, materialization, columns, tests, incremental) in models.items():
        (root / f"{name}.sql").write_text(query, encoding="utf-8")
        column_yaml = "".join(
            f"  - name: {column}\n    type: {data_type}\n    description: Phase 8 {column}.\n"
            for column, data_type in columns
        )
        (root / f"{name}.yml").write_text(
            f"model: {name}\n"
            "description: Phase 8 PostgreSQL transform qualification.\n"
            "owner: data-eng\n"
            "dialect: postgres\n"
            f"materialization: {materialization}\n"
            f"dataset: {target_schema}\n"
            "source_system: phase8_fixture\n"
            "sensitivity: public\n"
            f"{incremental}"
            f"columns:\n{column_yaml}"
            f"tests:\n{tests}",
            encoding="utf-8",
        )


def _require_transform_initial(
    pool: PostgreSQLPool,
    *,
    target_schema: str,
    config: Phase8PostgreSQLConfig,
) -> None:
    expected_amount = sum(value % 17 for value in range(1, config.transform_fact_rows + 1))
    with pool.connection() as connection:
        scan = connection.execute(
            sql.SQL("SELECT COUNT(*) AS rows FROM {}").format(
                sql.Identifier(target_schema, "scan_records")
            )
        ).fetchone()
        joined = connection.execute(
            sql.SQL(
                "SELECT COUNT(*) AS rows, COUNT(DISTINCT category) AS categories FROM {}"
            ).format(sql.Identifier(target_schema, "joined_records"))
        ).fetchone()
        aggregate = connection.execute(
            sql.SQL(
                "SELECT COUNT(*) AS categories, SUM(row_count) AS rows, "
                "SUM(total_amount) AS total_amount FROM {}"
            ).format(sql.Identifier(target_schema, "aggregate_records"))
        ).fetchone()
        incremental = connection.execute(
            sql.SQL("SELECT COUNT(*) AS rows FROM {}").format(
                sql.Identifier(target_schema, "incremental_records")
            )
        ).fetchone()
    if scan != {"rows": config.transform_fact_rows}:
        raise RuntimeError("PostgreSQL transform scan produced unexpected rows")
    if joined != {"rows": config.transform_fact_rows, "categories": 10}:
        raise RuntimeError("PostgreSQL transform join produced unexpected rows")
    if aggregate != {
        "categories": 10,
        "rows": config.transform_fact_rows,
        "total_amount": expected_amount,
    }:
        raise RuntimeError("PostgreSQL transform aggregation produced unexpected rows")
    if incremental != {"rows": config.transform_fact_rows}:
        raise RuntimeError("PostgreSQL transform incremental seed produced unexpected rows")


def _require_transform_incremental(
    pool: PostgreSQLPool,
    *,
    target_schema: str,
    expected_rows: int,
) -> None:
    with pool.connection() as connection:
        result = connection.execute(
            sql.SQL(
                "SELECT COUNT(*) AS rows, "
                "COUNT(*) FILTER (WHERE id = 1 AND amount = 999 AND updated_at = 2) AS updated, "
                "COUNT(*) FILTER (WHERE id = %s AND amount = 5 AND updated_at = 2) AS inserted "
                "FROM {}"
            ).format(sql.Identifier(target_schema, "incremental_records")),
            (expected_rows,),
        ).fetchone()
    if result != {"rows": expected_rows, "updated": 1, "inserted": 1}:
        raise RuntimeError("PostgreSQL transform incremental merge produced unexpected rows")


def _write_table(
    warehouse: WarehouseRuntime,
    *,
    database: str,
    schema: str,
    table: str,
    pipeline_id: str,
    rows: int,
    payload_bytes: int,
    batch_rows: int,
) -> tuple[int, int]:
    relation = RelationRef(catalog=database, namespace=schema, name=table)
    publication = warehouse.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=pipeline_id,
            run_id=f"{pipeline_id}-one",
            token=1,
            authority_id="postgresql:phase8-local",
        ),
    )
    writer = warehouse.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=batch_rows,
        schema_evolution=SchemaEvolution.STRICT,
    )
    if not isinstance(writer, PostgreSQLCopyWriter):
        raise RuntimeError("PostgreSQL bulk qualification did not select COPY")
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="payload", data_type="STRING"),
        ),
        publication_fence=publication,
    )
    started = time.perf_counter()
    affected = _write_batches(
        writer,
        _records(rows, payload_bytes),
        target,
        batch_rows=batch_rows,
    )
    return affected, _elapsed_ms(started)


def _incremental_target(database: str, schema: str, publication: TargetFence) -> WriteTarget:
    return WriteTarget(
        relation=RelationRef(catalog=database, namespace=schema, name="incremental_records"),
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="payload", data_type="STRING"),
            WriteField(name="cursor_value", data_type="INT64", mode="REQUIRED"),
        ),
        publication_fence=publication,
    )


def _write_batches(
    writer: WritePattern,
    records: Iterator[dict[str, object]],
    target: WriteTarget,
    *,
    batch_rows: int,
) -> int:
    affected = 0
    while batch := list(islice(records, batch_rows)):
        affected += writer.write(batch, target)
    return affected


def _records(rows: int, payload_bytes: int) -> Iterator[dict[str, object]]:
    padding = "x" * payload_bytes
    for index in range(rows):
        yield {"id": f"{index:012d}", "payload": padding}


def _incremental_seed_records(config: Phase8PostgreSQLConfig) -> Iterator[dict[str, object]]:
    padding = "s" * config.incremental_payload_bytes
    for index in range(config.incremental_seed_rows):
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 1}


def _incremental_delta_records(config: Phase8PostgreSQLConfig) -> Iterator[dict[str, object]]:
    updated = config.incremental_delta_rows // 2
    payload = "d" * config.incremental_payload_bytes
    for index in range(updated):
        yield {"id": f"{index:012d}", "payload": payload, "cursor_value": 2}
    for offset in range(config.incremental_delta_rows - updated):
        index = config.incremental_seed_rows + offset
        yield {"id": f"{index:012d}", "payload": payload, "cursor_value": 2}


def _correctness_fixture() -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
]:
    initial = (
        {"id": "alpha", "label": "older", "sequence": 1},
        {"id": "beta", "label": "second", "sequence": 1},
        {"id": "alpha", "label": "newer", "sequence": 2},
    )
    update = (
        {"id": "alpha", "label": "café", "sequence": 3},
        {"id": "gamma", "label": "third", "sequence": 1},
    )
    expected = (
        {"id": "alpha", "label": "café", "sequence": 3},
        {"id": "beta", "label": "second", "sequence": 1},
        {"id": "gamma", "label": "third", "sequence": 1},
    )
    return initial, update, expected


def _correctness_expected_sha256() -> str:
    expected = _correctness_fixture()[2]
    return hashlib.sha256(
        json.dumps(expected, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _read_correctness_rows(
    pool: PostgreSQLPool,
    schema: str,
) -> tuple[dict[str, object], ...]:
    with pool.connection() as connection:
        rows = connection.execute(
            sql.SQL("SELECT id, label, sequence FROM {} ORDER BY id").format(
                sql.Identifier(schema, "scd1_records")
            )
        ).fetchall()
    return tuple(dict(row) for row in rows)


def _require_table_shape(
    pool: PostgreSQLPool,
    *,
    schema: str,
    table: str,
    rows: int,
    payload_bytes: int,
) -> None:
    with pool.connection() as connection:
        result = connection.execute(
            sql.SQL(
                "SELECT COUNT(*) AS rows, COALESCE(SUM(octet_length(payload)), 0) AS payload_bytes "
                "FROM {}"
            ).format(sql.Identifier(schema, table))
        ).fetchone()
    if result != {"rows": rows, "payload_bytes": rows * payload_bytes}:
        raise RuntimeError("PostgreSQL bulk qualification produced an unexpected table shape")


def _require_incremental_result(
    pool: PostgreSQLPool,
    *,
    schema: str,
    expected_rows: int,
    expected_updated: int,
    expected_inserted: int,
    seed_rows: int,
    payload_bytes: int,
) -> None:
    with pool.connection() as connection:
        result = connection.execute(
            sql.SQL(
                "SELECT COUNT(*) AS rows, "
                "COUNT(*) FILTER (WHERE cursor_value = 2 AND id < %s) AS updated, "
                "COUNT(*) FILTER (WHERE cursor_value = 2 AND id >= %s) AS inserted, "
                "COUNT(*) FILTER (WHERE cursor_value = 2 AND octet_length(payload) = %s) AS shaped "
                "FROM {}"
            ).format(sql.Identifier(schema, "incremental_records")),
            (f"{expected_updated:012d}", f"{seed_rows:012d}", payload_bytes),
        ).fetchone()
    expected = {
        "rows": expected_rows,
        "updated": expected_updated,
        "inserted": expected_inserted,
        "shaped": expected_updated + expected_inserted,
    }
    if result != expected:
        raise RuntimeError("PostgreSQL incremental qualification produced unexpected rows")


def _correctness_report(
    config: Phase8PostgreSQLConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _CorrectnessResult,
    *,
    provider_cost_usd: Decimal | None = None,
) -> QualificationReport:
    hosted_gke = approval.objectives.profile_id == "gke_standard_postgresql"
    if result.temporary_staging_relations or not result.cleanup_verified:
        raise ValueError("PostgreSQL correctness cleanup is incomplete")
    if provider_cost_usd is not None and (
        provider_cost_usd < 0 or provider_cost_usd > approval.cost_ceiling.amount_usd
    ):
        raise ValueError("provider-measured correctness cost is outside its approved ceiling")
    if not hosted_gke and provider_cost_usd not in (None, Decimal(0)):
        raise ValueError("local PostgreSQL correctness cost must remain zero")
    observed_cost = (
        provider_cost_usd
        if provider_cost_usd is not None
        else (approval.cost_ceiling.amount_usd if hosted_gke else Decimal(0))
    )
    cost_pending = hosted_gke and provider_cost_usd is None
    row_width = max(result.logical_input_bytes // result.input_rows, 1)
    objectives = tuple(
        ObjectiveResult(
            name,
            (
                ObjectiveStatus.NOT_EVALUATED
                if name == "cost_ceiling" and cost_pending
                else ObjectiveStatus.PASSED
            ),
            (
                f"phase8/{'gcp/gke' if hosted_gke else 'postgresql'}/correctness/"
                f"sha256:{result.normalized_sha256}"
                if name == "exact_normalized_output"
                else f"phase8/{'gcp/gke' if hosted_gke else 'postgresql'}/correctness/{name}"
            ),
        )
        for name in approval.objectives.names
    )
    return QualificationReport(
        context=_context(identity, approval),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.CORRECTNESS,
            input_rows=result.input_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=row_width,
            schema_depth=1,
            source_rate_limit=(
                "unlimited_in_cluster_fixture" if hosted_gke else "unlimited_local_fixture"
            ),
            transform_complexity="scd1_replay_normalization",
            concurrency=1,
            batch_rows=3,
            batch_bytes=row_width * 3,
            configuration_sha256=config.configuration_sha256(BenchmarkClass.CORRECTNESS),
        ),
        performance=_performance(
            rows=result.output_rows,
            logical_bytes=result.logical_input_bytes,
            duration_ms=result.duration_ms,
            peak_rss_bytes=result.peak_rss_bytes,
            load_duration_ms=result.duration_ms,
            provider_metrics=tuple(
                sorted(
                    (
                        _measured("normalized_output_rows", "rows", result.output_rows),
                        _measured(
                            "temporary_staging_relations",
                            "count",
                            result.temporary_staging_relations,
                        ),
                        *(
                            (
                                _measured("kubernetes_job_retries", "count", 0),
                                _measured("provider_operation_retries", "count", 0),
                            )
                            if hosted_gke
                            else ()
                        ),
                    ),
                    key=lambda metric: metric.name,
                )
            ),
            costs=(
                CostAttribution(
                    "gcp" if hosted_gke else "local",
                    "gke_standard_zonal" if hosted_gke else "postgresql",
                    observed_cost,
                    estimated=cost_pending,
                ),
            ),
        ),
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=(QualificationStatus.NOT_EVALUATED if cost_pending else QualificationStatus.PASSED),
    )


def _bulk_report(
    config: Phase8PostgreSQLConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _BulkResult,
    *,
    provider_cost_usd: Decimal | None = None,
) -> QualificationReport:
    hosted_gke = approval.objectives.profile_id == "gke_standard_postgresql"
    if result.temporary_staging_relations or not result.cleanup_verified:
        raise ValueError("PostgreSQL bulk cleanup is incomplete")
    if provider_cost_usd is not None and (
        provider_cost_usd < 0 or provider_cost_usd > approval.cost_ceiling.amount_usd
    ):
        raise ValueError("provider-measured bulk cost is outside its approved ceiling")
    if not hosted_gke and provider_cost_usd not in (None, Decimal(0)):
        raise ValueError("local PostgreSQL bulk cost must remain zero")
    observed_cost = (
        provider_cost_usd
        if provider_cost_usd is not None
        else (approval.cost_ceiling.amount_usd if hosted_gke else Decimal(0))
    )
    cost_pending = hosted_gke and provider_cost_usd is None
    rows = result.narrow_rows + result.wide_rows
    logical_bytes = result.narrow_logical_bytes + result.wide_logical_bytes
    metrics = (
        *((_measured("kubernetes_job_retries", "count", 0),) if hosted_gke else ()),
        _measured("narrow_duration_ms", "milliseconds", result.narrow_duration_ms),
        _measured("narrow_logical_bytes", "bytes", result.narrow_logical_bytes),
        _measured("narrow_rows", "rows", result.narrow_rows),
        _measured(
            "narrow_throughput_rows_per_second",
            "rows_per_second",
            _throughput(result.narrow_rows, result.narrow_duration_ms),
        ),
        *((_measured("provider_operation_retries", "count", 0),) if hosted_gke else ()),
        _measured("temporary_staging_relations", "count", result.temporary_staging_relations),
        _measured("wide_duration_ms", "milliseconds", result.wide_duration_ms),
        _measured("wide_logical_bytes", "bytes", result.wide_logical_bytes),
        _measured("wide_rows", "rows", result.wide_rows),
        _measured(
            "wide_throughput_rows_per_second",
            "rows_per_second",
            _throughput(result.wide_rows, result.wide_duration_ms),
        ),
    )
    objectives = tuple(
        ObjectiveResult(
            name,
            (
                ObjectiveStatus.NOT_EVALUATED
                if name == "cost_ceiling" and cost_pending
                else ObjectiveStatus.PASSED
            ),
            f"phase8/{'gcp/gke' if hosted_gke else 'postgresql'}/bulk/{name}",
        )
        for name in approval.objectives.names
    )
    return QualificationReport(
        context=_context(identity, approval),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
            input_rows=rows,
            logical_input_bytes=logical_bytes,
            row_width_bytes=config.wide_payload_bytes + 24,
            schema_depth=1,
            source_rate_limit=(
                "unlimited_in_cluster_generator" if hosted_gke else "unlimited_local_generator"
            ),
            transform_complexity="none_wide_and_narrow",
            concurrency=1,
            batch_rows=config.batch_rows,
            batch_bytes=config.batch_rows * (config.wide_payload_bytes + 24),
            configuration_sha256=config.configuration_sha256(BenchmarkClass.BULK_THROUGHPUT),
        ),
        performance=_performance(
            rows=rows,
            logical_bytes=logical_bytes,
            duration_ms=result.duration_ms,
            peak_rss_bytes=result.peak_rss_bytes,
            load_duration_ms=result.narrow_duration_ms + result.wide_duration_ms,
            provider_metrics=metrics,
            costs=(
                CostAttribution(
                    "gcp" if hosted_gke else "local",
                    "gke_standard_zonal" if hosted_gke else "postgresql",
                    observed_cost,
                    estimated=cost_pending,
                ),
            ),
        ),
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=(QualificationStatus.NOT_EVALUATED if cost_pending else QualificationStatus.PASSED),
    )


def _incremental_report(
    config: Phase8PostgreSQLConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _IncrementalResult,
) -> QualificationReport:
    ratio = Decimal(result.seed_rows) / Decimal(result.delta_rows)
    if ratio < 100:
        raise RuntimeError("PostgreSQL incremental target is less than 100 times its delta")
    metrics = (
        _measured("delta_target_ratio", "ratio", ratio),
        _measured("final_target_rows", "rows", result.final_rows),
        _measured("regression_rows_affected", "rows", result.regression_rows_affected),
        _measured("seed_duration_ms", "milliseconds", result.seed_duration_ms),
        _measured("seed_logical_bytes", "bytes", result.seed_logical_bytes),
        _measured("seed_rows", "rows", result.seed_rows),
        _measured("temporary_staging_relations", "count", result.temporary_staging_relations),
    )
    return QualificationReport(
        context=_context(identity, approval),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.INCREMENTAL,
            input_rows=result.delta_rows,
            logical_input_bytes=result.delta_logical_bytes,
            row_width_bytes=config.incremental_payload_bytes + 32,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="cursor_merge_small_delta",
            concurrency=1,
            batch_rows=config.batch_rows,
            batch_bytes=config.batch_rows * (config.incremental_payload_bytes + 32),
            configuration_sha256=config.configuration_sha256(BenchmarkClass.INCREMENTAL),
        ),
        performance=_performance(
            rows=result.delta_rows,
            logical_bytes=result.delta_logical_bytes,
            duration_ms=result.duration_ms,
            peak_rss_bytes=result.peak_rss_bytes,
            load_duration_ms=result.seed_duration_ms + result.delta_duration_ms,
            provider_metrics=metrics,
            throughput_duration_ms=result.delta_duration_ms,
        ),
        objectives=_passed_objectives(approval.objectives.names, "incremental"),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _transform_report(
    config: Phase8PostgreSQLConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _TransformResult,
) -> QualificationReport:
    return QualificationReport(
        context=_context(identity, approval),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.TRANSFORM,
            input_rows=result.input_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=32,
            schema_depth=4,
            source_rate_limit="unlimited_local_fixture",
            transform_complexity="scan_join_aggregate_incremental_tests",
            concurrency=1,
            batch_rows=config.transform_fact_rows,
            batch_bytes=config.transform_fact_rows * 32,
            configuration_sha256=config.configuration_sha256(BenchmarkClass.TRANSFORM),
        ),
        performance=_performance(
            rows=result.output_rows,
            logical_bytes=result.logical_input_bytes,
            duration_ms=result.duration_ms,
            peak_rss_bytes=result.peak_rss_bytes,
            load_duration_ms=0,
            provider_metrics=(
                _measured("assertion_count", "count", result.assertion_count),
                _measured("model_count", "count", result.model_count),
                _measured(
                    "ownership_verifications",
                    "count",
                    result.ownership_verifications,
                ),
                _measured(
                    "temporary_staging_relations",
                    "count",
                    result.temporary_staging_relations,
                ),
            ),
            transform_duration_ms=result.duration_ms,
        ),
        objectives=_passed_objectives(approval.objectives.names, "transform"),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _failure_report(
    config: Phase8PostgreSQLConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _FailureResult,
) -> QualificationReport:
    return QualificationReport(
        context=_context(identity, approval),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.FAILURE,
            input_rows=result.probe_count,
            logical_input_bytes=result.probe_count,
            row_width_bytes=1,
            schema_depth=1,
            source_rate_limit="controlled_local_failure_injection",
            transform_complexity="state_pool_connection_and_cancellation",
            concurrency=2,
            batch_rows=1,
            batch_bytes=1,
            configuration_sha256=config.configuration_sha256(BenchmarkClass.FAILURE),
        ),
        performance=_performance(
            rows=result.probe_count,
            logical_bytes=result.probe_count,
            duration_ms=result.duration_ms,
            peak_rss_bytes=result.peak_rss_bytes,
            load_duration_ms=0,
            provider_metrics=(
                _measured(
                    "cancellation_duration_ms",
                    "milliseconds",
                    result.cancellation_duration_ms,
                ),
                _measured(
                    "connection_recovery_duration_ms",
                    "milliseconds",
                    result.connection_recovery_duration_ms,
                ),
                _measured(
                    "pool_timeout_duration_ms",
                    "milliseconds",
                    result.pool_timeout_duration_ms,
                ),
                _measured("probe_count", "count", result.probe_count),
                _measured(
                    "temporary_staging_relations",
                    "count",
                    result.temporary_staging_relations,
                ),
            ),
        ),
        objectives=_passed_objectives(approval.objectives.names, "failure"),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _context(identity: CandidateIdentity, approval: _Approval) -> QualificationContext:
    return QualificationContext(
        release_version=identity.release_version,
        git_commit=identity.git_commit,
        image_digest=identity.image_digest,
        benchmark_date=identity.benchmark_date,
        profile_id=approval.objectives.profile_id,
        launcher=identity.launcher,
        warehouse="postgresql",
        state_backend="postgresql",
        catalog="none",
        secret_provider=identity.secret_provider,
        regions=identity.regions,
        service_shapes=identity.service_shapes,
        provider_job_ids=identity.provider_job_ids,
        cost_ceiling=approval.cost_ceiling,
    )


def _performance(
    *,
    rows: int,
    logical_bytes: int,
    duration_ms: int,
    peak_rss_bytes: int,
    load_duration_ms: int,
    provider_metrics: tuple[PerformanceMeasurement, ...],
    throughput_duration_ms: int | None = None,
    transform_duration_ms: int = 0,
    costs: tuple[CostAttribution, ...] | None = None,
) -> RunPerformance:
    measured = PerformanceMeasurement.measured
    return RunPerformance(
        rows=measured("rows", "rows", rows),
        logical_bytes=measured("logical_bytes", "bytes", logical_bytes),
        duration_ms=measured("duration_ms", "milliseconds", duration_ms),
        throughput_rows_per_second=measured(
            "throughput_rows_per_second",
            "rows_per_second",
            _throughput(rows, throughput_duration_ms or duration_ms),
        ),
        peak_rss_bytes=measured("peak_rss_bytes", "bytes", peak_rss_bytes),
        retries=measured("retries", "count", 0),
        queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
        load_duration_ms=measured("load_duration_ms", "milliseconds", load_duration_ms),
        transform_duration_ms=measured(
            "transform_duration_ms", "milliseconds", transform_duration_ms
        ),
        catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
        provider_metrics=provider_metrics,
        costs=(
            costs
            if costs is not None
            else (CostAttribution("local", "postgresql", Decimal(0), estimated=False),)
        ),
    )


def _passed_objectives(names: Sequence[str], suffix: str) -> tuple[ObjectiveResult, ...]:
    return tuple(
        ObjectiveResult(
            name,
            ObjectiveStatus.PASSED,
            f"phase8/postgresql/{suffix}/{name}",
        )
        for name in names
    )


def _measured(name: str, unit: str, value: int | Decimal) -> PerformanceMeasurement:
    return PerformanceMeasurement.measured(name, unit, value)


def _throughput(rows: int, duration_ms: int) -> Decimal:
    return (Decimal(rows) * 1_000 / Decimal(max(duration_ms, 1))).quantize(Decimal("0.001"))


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"objective manifest {label} is incomplete")
    return cast("dict[str, object]", value)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _temporary_staging_count(pool: PostgreSQLPool) -> int:
    with pool.connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM pg_catalog.pg_class "
            "WHERE relname LIKE 'dander_stage_%' AND relpersistence = 't'"
        ).fetchone()
    return int(cast("int", row["count"] if row is not None else 0))


def _drop_schema(pool: PostgreSQLPool, schema: str) -> None:
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )


def _schema_exists(pool: PostgreSQLPool, schema: str) -> bool:
    with pool.connection() as connection:
        row = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname = %s) AS exists",
            (schema,),
        ).fetchone()
    return bool(row and row["exists"])


def _elapsed_ms(started: float) -> int:
    return max(round((time.perf_counter() - started) * 1_000), 1)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1_024)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-env", default="DANDER_PHASE8_POSTGRES_DSN")
    parser.add_argument("--benchmark-class", choices=("all", "bulk", "correctness"), default="all")
    parser.add_argument("--correctness-objectives", type=Path)
    parser.add_argument("--bulk-objectives", type=Path)
    parser.add_argument("--incremental-objectives", type=Path)
    parser.add_argument("--transform-objectives", type=Path)
    parser.add_argument("--failure-objectives", type=Path)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--benchmark-date", type=date.fromisoformat, required=True)
    parser.add_argument("--provider-job-id", action="append", required=True)
    parser.add_argument("--service-shape", action="append", required=True)
    parser.add_argument("--launcher", default="local")
    parser.add_argument("--region", action="append")
    parser.add_argument("--secret-provider", default="environment")
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--narrow-rows", type=int, default=500_000)
    parser.add_argument("--narrow-payload-bytes", type=int, default=32)
    parser.add_argument("--wide-rows", type=int, default=200_000)
    parser.add_argument("--wide-payload-bytes", type=int, default=1_024)
    parser.add_argument("--incremental-seed-rows", type=int, default=300_000)
    parser.add_argument("--incremental-delta-rows", type=int, default=3_000)
    parser.add_argument("--incremental-payload-bytes", type=int, default=128)
    parser.add_argument("--transform-fact-rows", type=int, default=100_000)
    parser.add_argument("--transform-dimension-rows", type=int, default=100)
    parser.add_argument("--batch-rows", type=int, default=1_000)
    parser.add_argument("--provider-cost-usd", type=Decimal)
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    dsn = os.environ.get(arguments.dsn_env)
    if not dsn:
        raise SystemExit(f"PostgreSQL DSN is required in {arguments.dsn_env}")
    config = Phase8PostgreSQLConfig(
        narrow_rows=arguments.narrow_rows,
        narrow_payload_bytes=arguments.narrow_payload_bytes,
        wide_rows=arguments.wide_rows,
        wide_payload_bytes=arguments.wide_payload_bytes,
        incremental_seed_rows=arguments.incremental_seed_rows,
        incremental_delta_rows=arguments.incremental_delta_rows,
        incremental_payload_bytes=arguments.incremental_payload_bytes,
        transform_fact_rows=arguments.transform_fact_rows,
        transform_dimension_rows=arguments.transform_dimension_rows,
        batch_rows=arguments.batch_rows,
    )
    identity = CandidateIdentity(
        release_version=arguments.candidate_version,
        git_commit=arguments.candidate_commit,
        image_digest=arguments.image_digest,
        approval_reference=arguments.approval_reference,
        benchmark_date=arguments.benchmark_date,
        launcher=arguments.launcher,
        regions=tuple(sorted(set(arguments.region or ("local",)))),
        secret_provider=arguments.secret_provider,
        provider_job_ids=tuple(sorted(set(arguments.provider_job_id))),
        service_shapes=tuple(sorted(set(arguments.service_shape))),
    )
    if arguments.benchmark_class == "correctness":
        if arguments.correctness_objectives is None:
            raise SystemExit("correctness qualification requires its objective manifest")
        correctness_approval = load_approval(
            arguments.correctness_objectives,
            config=config,
            benchmark_class=BenchmarkClass.CORRECTNESS,
        )
        correctness = run_postgresql_correctness_qualification(
            dsn,
            config=config,
            identity=identity,
            approval=correctness_approval,
            provider_cost_usd=arguments.provider_cost_usd,
        )
        arguments.output_directory.mkdir(parents=True, exist_ok=True)
        (arguments.output_directory / "postgresql-correctness.json").write_text(
            correctness.to_json() + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "correctness_status": correctness.status.value,
                    "python_version": platform.python_version(),
                    "release_version": __version__,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return
    if arguments.benchmark_class == "bulk":
        if arguments.bulk_objectives is None:
            raise SystemExit("bulk qualification requires its objective manifest")
        bulk_approval = load_approval(
            arguments.bulk_objectives,
            config=config,
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
        )
        bulk = run_postgresql_bulk_qualification(
            dsn,
            config=config,
            identity=identity,
            approval=bulk_approval,
            provider_cost_usd=arguments.provider_cost_usd,
        )
        arguments.output_directory.mkdir(parents=True, exist_ok=True)
        (arguments.output_directory / "postgresql-bulk-throughput.json").write_text(
            bulk.to_json() + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "bulk_status": bulk.status.value,
                    "python_version": platform.python_version(),
                    "release_version": __version__,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return
    objective_paths = (
        arguments.correctness_objectives,
        arguments.bulk_objectives,
        arguments.incremental_objectives,
        arguments.transform_objectives,
        arguments.failure_objectives,
    )
    if any(path is None for path in objective_paths):
        raise SystemExit("all-class qualification requires every objective manifest")
    if arguments.provider_cost_usd not in (None, Decimal(0)):
        raise SystemExit("all-class local qualification requires zero provider cost")
    correctness_approval = load_approval(
        arguments.correctness_objectives,
        config=config,
        benchmark_class=BenchmarkClass.CORRECTNESS,
    )
    bulk_approval = load_approval(
        arguments.bulk_objectives,
        config=config,
        benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
    )
    incremental_approval = load_approval(
        arguments.incremental_objectives,
        config=config,
        benchmark_class=BenchmarkClass.INCREMENTAL,
    )
    transform_approval = load_approval(
        arguments.transform_objectives,
        config=config,
        benchmark_class=BenchmarkClass.TRANSFORM,
    )
    failure_approval = load_approval(
        arguments.failure_objectives,
        config=config,
        benchmark_class=BenchmarkClass.FAILURE,
    )
    correctness, bulk, incremental, transform, failure = run_phase8_postgresql_qualification(
        dsn,
        config=config,
        identity=identity,
        correctness_approval=correctness_approval,
        bulk_approval=bulk_approval,
        incremental_approval=incremental_approval,
        transform_approval=transform_approval,
        failure_approval=failure_approval,
    )
    arguments.output_directory.mkdir(parents=True, exist_ok=True)
    (arguments.output_directory / "postgresql-correctness.json").write_text(
        correctness.to_json() + "\n",
        encoding="utf-8",
    )
    (arguments.output_directory / "postgresql-bulk-throughput.json").write_text(
        bulk.to_json() + "\n",
        encoding="utf-8",
    )
    (arguments.output_directory / "postgresql-incremental.json").write_text(
        incremental.to_json() + "\n",
        encoding="utf-8",
    )
    (arguments.output_directory / "postgresql-transform.json").write_text(
        transform.to_json() + "\n",
        encoding="utf-8",
    )
    (arguments.output_directory / "postgresql-failure.json").write_text(
        failure.to_json() + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "bulk_status": bulk.status.value,
                "correctness_status": correctness.status.value,
                "failure_status": failure.status.value,
                "incremental_status": incremental.status.value,
                "python_version": platform.python_version(),
                "release_version": __version__,
                "transform_status": transform.status.value,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
