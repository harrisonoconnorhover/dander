#!/usr/bin/env python3
"""Exact-candidate Snowflake scale and provider-failure qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast

from dander import __version__
from dander.concurrency import FencingToken, TargetFenceLostError
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry
from dander.providers.snowflake.session import execute, open_connection
from dander.providers.snowflake.transform import SnowflakeTransformRunner
from dander.providers.snowflake.writer import SnowflakeStagedWriter
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
from dander.telemetry import (
    CostAttribution,
    OperationTelemetry,
    PerformanceMeasurement,
    RunPerformance,
)
from dander.warehouse import RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from dander.providers.snowflake.fence import SnowflakeTargetFence
    from dander.providers.snowflake.session import SnowflakeConnectionFactory


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.snowflake-bulk/v1"
_INCREMENTAL_CONFIG_SCHEMA = "io.dander.phase8.snowflake-incremental/v1"
_CONCURRENCY_CONFIG_SCHEMA = "io.dander.phase8.snowflake-concurrency/v1"
_TRANSFORM_CONFIG_SCHEMA = "io.dander.phase8.snowflake-transform/v1"
_FAILURE_CONFIG_SCHEMA = "io.dander.phase8.snowflake-failure/v1"
_AUTHORITY_ID = "snowflake:phase8-bulk"
_INCREMENTAL_AUTHORITY_ID = "snowflake:phase8-incremental"
_CONCURRENCY_AUTHORITY_ID = "snowflake:phase8-concurrency"
_TRANSFORM_AUTHORITY_ID = "snowflake:phase8-transform"
_FAILURE_AUTHORITY_ID = "snowflake:phase8-failure"
_OBJECTIVES = (
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
_CONCURRENCY_OBJECTIVES = (
    "cleanup",
    "concurrent_pipeline_completion",
    "controlled_contention",
    "cost_ceiling",
    "stale_fence_rejection",
    "throughput_measurement",
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
    "cleanup",
    "closed_connection_recovery",
    "cost_ceiling",
    "credential_rejection",
    "stale_fence_rejection",
    "warehouse_timeout_rollback",
)
_OBJECTIVES_BY_CLASS = {
    BenchmarkClass.BULK_THROUGHPUT: _OBJECTIVES,
    BenchmarkClass.INCREMENTAL: _INCREMENTAL_OBJECTIVES,
    BenchmarkClass.CONCURRENT_PIPELINES: _CONCURRENCY_OBJECTIVES,
    BenchmarkClass.TRANSFORM: _TRANSFORM_OBJECTIVES,
    BenchmarkClass.FAILURE: _FAILURE_OBJECTIVES,
}


class SnowflakeBulkQualificationError(RuntimeError):
    """Raised with a sanitized Snowflake bulk-qualification summary."""


class SnowflakeIncrementalQualificationError(SnowflakeBulkQualificationError):
    """Raised with a sanitized Snowflake incremental-qualification summary."""


class SnowflakeConcurrencyQualificationError(SnowflakeBulkQualificationError):
    """Raised with a sanitized Snowflake concurrency-qualification summary."""


class SnowflakeTransformQualificationError(SnowflakeBulkQualificationError):
    """Raised with a sanitized Snowflake transform-qualification summary."""


class SnowflakeFailureQualificationError(SnowflakeBulkQualificationError):
    """Raised with a sanitized Snowflake failure-qualification summary."""


@dataclass(frozen=True, slots=True)
class SnowflakeBulkConfig:
    """Non-secret provider coordinates and the bounded bulk workload."""

    account: str
    user: str
    database: str
    warehouse: str
    role: str | None = None
    auth_method: str = "oauth"
    token_env: str = "DANDER_SNOWFLAKE_OAUTH_TOKEN"
    private_key_file_env: str = "DANDER_SNOWFLAKE_PRIVATE_KEY_FILE"
    private_key_password_env: str | None = None
    narrow_rows: int = 500_000
    narrow_payload_bytes: int = 32
    wide_rows: int = 200_000
    wide_payload_bytes: int = 1_024
    copy_part_rows: int = 50_000
    copy_part_logical_bytes: int = 16 * 1_024 * 1_024

    def __post_init__(self) -> None:
        if self.auth_method not in {"key_pair", "oauth"}:
            raise ValueError("auth_method must be key_pair or oauth")
        for name in (
            "narrow_rows",
            "narrow_payload_bytes",
            "wide_rows",
            "wide_payload_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.copy_part_rows > min(self.narrow_rows, self.wide_rows):
            raise ValueError("copy_part_rows must not exceed the smaller workload")
        default_provider_registry().parse(
            ProviderKind.WAREHOUSE,
            _provider_values(self, schema_name="DANDER_PHASE8_BULK_CHECK"),
        )

    def workload_payload(self) -> dict[str, object]:
        """Return the exact approval-bound workload configuration."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.BULK_THROUGHPUT.value,
            "narrow_rows": self.narrow_rows,
            "narrow_payload_bytes": self.narrow_payload_bytes,
            "wide_rows": self.wide_rows,
            "wide_payload_bytes": self.wide_payload_bytes,
            "copy_part_rows": self.copy_part_rows,
            "copy_part_logical_bytes": self.copy_part_logical_bytes,
        }

    def configuration_sha256(self) -> str:
        """Hash the canonical workload used by objective approval."""
        encoded = json.dumps(
            self.workload_payload(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SnowflakeIncrementalConfig:
    """Non-secret provider coordinates and the bounded incremental workload."""

    account: str
    user: str
    database: str
    warehouse: str
    role: str | None = None
    auth_method: str = "oauth"
    token_env: str = "DANDER_SNOWFLAKE_OAUTH_TOKEN"
    private_key_file_env: str = "DANDER_SNOWFLAKE_PRIVATE_KEY_FILE"
    private_key_password_env: str | None = None
    seed_rows: int = 300_000
    delta_rows: int = 3_000
    payload_bytes: int = 128
    copy_part_rows: int = 50_000
    copy_part_logical_bytes: int = 16 * 1_024 * 1_024

    def __post_init__(self) -> None:
        if self.auth_method not in {"key_pair", "oauth"}:
            raise ValueError("auth_method must be key_pair or oauth")
        for name in (
            "seed_rows",
            "delta_rows",
            "payload_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.delta_rows % 2:
            raise ValueError("delta_rows must be even")
        if self.delta_rows > self.seed_rows:
            raise ValueError("delta_rows must not exceed seed_rows")
        if self.copy_part_rows > self.seed_rows:
            raise ValueError("copy_part_rows must not exceed seed_rows")
        default_provider_registry().parse(
            ProviderKind.WAREHOUSE,
            _provider_values(self, schema_name="DANDER_PHASE8_INCREMENTAL_CHECK"),
        )

    def workload_payload(self) -> dict[str, object]:
        """Return the exact approval-bound incremental workload."""
        return {
            "schema": _INCREMENTAL_CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.INCREMENTAL.value,
            "seed_rows": self.seed_rows,
            "delta_rows": self.delta_rows,
            "payload_bytes": self.payload_bytes,
            "copy_part_rows": self.copy_part_rows,
            "copy_part_logical_bytes": self.copy_part_logical_bytes,
        }

    def configuration_sha256(self) -> str:
        """Hash the canonical workload used by objective approval."""
        encoded = json.dumps(
            self.workload_payload(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SnowflakeConcurrencyConfig:
    """Non-secret provider coordinates and bounded concurrent workload."""

    account: str
    user: str
    database: str
    warehouse: str
    role: str | None = None
    auth_method: str = "oauth"
    token_env: str = "DANDER_SNOWFLAKE_OAUTH_TOKEN"
    private_key_file_env: str = "DANDER_SNOWFLAKE_PRIVATE_KEY_FILE"
    private_key_password_env: str | None = None
    concurrent_pipelines: int = 4
    rows_per_pipeline: int = 5_000
    payload_bytes: int = 128
    copy_part_rows: int = 5_000
    copy_part_logical_bytes: int = 16 * 1_024 * 1_024

    def __post_init__(self) -> None:
        if self.auth_method not in {"key_pair", "oauth"}:
            raise ValueError("auth_method must be key_pair or oauth")
        for name in (
            "concurrent_pipelines",
            "rows_per_pipeline",
            "payload_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.concurrent_pipelines < 2 or self.concurrent_pipelines > 32:
            raise ValueError("concurrent_pipelines must be between 2 and 32")
        if self.copy_part_rows > self.rows_per_pipeline:
            raise ValueError("copy_part_rows must not exceed rows_per_pipeline")
        default_provider_registry().parse(
            ProviderKind.WAREHOUSE,
            _provider_values(self, schema_name="DANDER_PHASE8_CONCURRENCY_CHECK"),
        )

    def workload_payload(self) -> dict[str, object]:
        """Return the exact approval-bound concurrent workload."""
        return {
            "schema": _CONCURRENCY_CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.CONCURRENT_PIPELINES.value,
            "concurrent_pipelines": self.concurrent_pipelines,
            "rows_per_pipeline": self.rows_per_pipeline,
            "payload_bytes": self.payload_bytes,
            "copy_part_rows": self.copy_part_rows,
            "copy_part_logical_bytes": self.copy_part_logical_bytes,
        }

    def configuration_sha256(self) -> str:
        """Hash the canonical workload used by objective approval."""
        encoded = json.dumps(
            self.workload_payload(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SnowflakeTransformConfig:
    """Non-secret provider coordinates and bounded transform workload."""

    account: str
    user: str
    database: str
    warehouse: str
    role: str | None = None
    auth_method: str = "oauth"
    token_env: str = "DANDER_SNOWFLAKE_OAUTH_TOKEN"
    private_key_file_env: str = "DANDER_SNOWFLAKE_PRIVATE_KEY_FILE"
    private_key_password_env: str | None = None
    fact_rows: int = 100_000
    dimension_rows: int = 100
    copy_part_rows: int = 50_000
    copy_part_logical_bytes: int = 16 * 1_024 * 1_024

    def __post_init__(self) -> None:
        if self.auth_method not in {"key_pair", "oauth"}:
            raise ValueError("auth_method must be key_pair or oauth")
        for name in (
            "fact_rows",
            "dimension_rows",
            "copy_part_rows",
            "copy_part_logical_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.fact_rows < self.dimension_rows:
            raise ValueError("fact_rows must not be smaller than dimension_rows")
        if self.copy_part_rows > self.fact_rows:
            raise ValueError("copy_part_rows must not exceed fact_rows")
        default_provider_registry().parse(
            ProviderKind.WAREHOUSE,
            _provider_values(self, schema_name="DANDER_PHASE8_TRANSFORM_CHECK"),
        )

    def workload_payload(self) -> dict[str, object]:
        """Return the exact approval-bound transform workload."""
        return {
            "schema": _TRANSFORM_CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.TRANSFORM.value,
            "fact_rows": self.fact_rows,
            "dimension_rows": self.dimension_rows,
            "delta_rows": 2,
            "models": ["scan", "join", "aggregation", "incremental"],
            "generic_tests": ["accepted_values", "not_null", "unique"],
            "copy_part_rows": self.copy_part_rows,
            "copy_part_logical_bytes": self.copy_part_logical_bytes,
        }

    def configuration_sha256(self) -> str:
        """Hash the canonical workload used by objective approval."""
        encoded = json.dumps(
            self.workload_payload(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SnowflakeFailureConfig:
    """Non-secret provider coordinates and bounded failure probes."""

    account: str
    user: str
    database: str
    warehouse: str
    role: str | None = None
    auth_method: str = "oauth"
    token_env: str = "DANDER_SNOWFLAKE_OAUTH_TOKEN"
    private_key_file_env: str = "DANDER_SNOWFLAKE_PRIVATE_KEY_FILE"
    private_key_password_env: str | None = None
    statement_timeout_seconds: int = 1
    cancellation_wait_seconds: int = 30
    invalid_login_timeout_seconds: int = 5
    copy_part_rows: int = 1
    copy_part_logical_bytes: int = 1_024

    def __post_init__(self) -> None:
        if self.auth_method != "oauth":
            raise ValueError("Snowflake failure qualification requires oauth")
        for name in (
            "statement_timeout_seconds",
            "cancellation_wait_seconds",
            "invalid_login_timeout_seconds",
            "copy_part_rows",
            "copy_part_logical_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.cancellation_wait_seconds <= self.statement_timeout_seconds:
            raise ValueError("cancellation_wait_seconds must exceed statement_timeout_seconds")
        if self.statement_timeout_seconds > 5:
            raise ValueError("statement_timeout_seconds must not exceed 5")
        if self.invalid_login_timeout_seconds > 10:
            raise ValueError("invalid_login_timeout_seconds must not exceed 10")
        default_provider_registry().parse(
            ProviderKind.WAREHOUSE,
            _provider_values(self, schema_name="DANDER_PHASE8_FAILURE_CHECK"),
        )

    def workload_payload(self) -> dict[str, object]:
        """Return the exact approval-bound failure workload."""
        return {
            "schema": _FAILURE_CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.FAILURE.value,
            "probes": [
                "closed_connection_recovery",
                "credential_rejection",
                "stale_fence_rejection",
                "warehouse_timeout_rollback",
            ],
            "statement_timeout_seconds": self.statement_timeout_seconds,
            "cancellation_wait_seconds": self.cancellation_wait_seconds,
            "invalid_login_timeout_seconds": self.invalid_login_timeout_seconds,
        }

    def configuration_sha256(self) -> str:
        """Hash the canonical workload used by objective approval."""
        encoded = json.dumps(
            self.workload_payload(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


SnowflakeScaleConfig = (
    SnowflakeBulkConfig
    | SnowflakeIncrementalConfig
    | SnowflakeConcurrencyConfig
    | SnowflakeTransformConfig
    | SnowflakeFailureConfig
)


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    """Immutable candidate and execution coordinates for the report."""

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
    account_sha256: str
    operator_user_sha256: str
    database: str
    warehouse: str
    role: str


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
    copy_operations: int
    query_ids: tuple[str, ...]
    staging_tables: int
    staging_stages: int
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
    copy_operations: int
    query_ids: tuple[str, ...]
    staging_tables: int
    staging_stages: int
    cleanup_verified: bool


@dataclass(frozen=True, slots=True)
class _ConcurrencyResult:
    duration_ms: int
    peak_rss_bytes: int
    pipeline_count: int
    rows_per_pipeline: int
    total_rows: int
    logical_input_bytes: int
    concurrent_claim_attempts: int
    stale_publications_rejected: int
    copy_operations: int
    query_ids: tuple[str, ...]
    staging_tables: int
    staging_stages: int
    cleanup_verified: bool


@dataclass(slots=True)
class _Ownership:
    fence: FencingToken
    verifications: int = 0

    def verify(self) -> None:
        """Record each ownership guard verification made by the transform runner."""
        self.verifications += 1


@dataclass(frozen=True, slots=True)
class _TransformResult:
    duration_ms: int
    peak_rss_bytes: int
    load_duration_ms: int
    transform_duration_ms: int
    input_rows: int
    logical_input_bytes: int
    output_rows: int
    model_count: int
    assertion_count: int
    ownership_verifications: int
    copy_operations: int
    query_ids: tuple[str, ...]
    staging_tables: int
    staging_stages: int
    cleanup_verified: bool


@dataclass(frozen=True, slots=True)
class _FailureResult:
    duration_ms: int
    peak_rss_bytes: int
    probe_count: int
    connection_recovery_duration_ms: int
    credential_rejection_duration_ms: int
    timeout_rollback_duration_ms: int
    stale_publications_rejected: int
    query_ids: tuple[str, ...]
    staging_tables: int
    staging_stages: int
    cleanup_verified: bool


def load_approval(path: Path, *, config: SnowflakeScaleConfig) -> _Approval:
    """Load and validate one pre-mutation objective approval manifest."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective manifest has an incompatible schema")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective manifest workload does not match the benchmark configuration")
    raw_cost = payload.get("cost_ceiling")
    raw_objectives = payload.get("approved_objectives")
    raw_configuration = payload.get("configuration")
    if not isinstance(raw_cost, dict) or not isinstance(raw_objectives, dict):
        raise ValueError("objective manifest is incomplete")
    if not isinstance(raw_configuration, dict):
        raise ValueError("objective manifest configuration is incomplete")
    raw_snowflake = raw_configuration.get("snowflake")
    if not isinstance(raw_snowflake, dict):
        raise ValueError("objective manifest Snowflake configuration is incomplete")
    benchmark_class = BenchmarkClass(str(raw_objectives.get("benchmark_class")))
    expected_objectives = _OBJECTIVES_BY_CLASS.get(benchmark_class)
    if expected_objectives is None:
        raise ValueError("objective manifest benchmark class is unsupported")
    if tuple(raw_objectives.get("names", ())) != expected_objectives:
        raise ValueError("objective manifest does not contain the required objective set")
    objectives = ApprovedObjectiveSet(
        names=expected_objectives,
        benchmark_class=benchmark_class,
        profile_id=str(raw_objectives.get("profile_id")),
        release_version=str(raw_objectives.get("release_version")),
        git_commit=str(raw_objectives.get("git_commit")),
        image_digest=str(raw_objectives.get("image_digest")),
        configuration_sha256=str(raw_objectives.get("configuration_sha256")),
        approval_reference=str(raw_objectives.get("approval_reference")),
    )
    if objectives.configuration_sha256 != config.configuration_sha256():
        raise ValueError("objective manifest configuration hash does not match")
    cost_ceiling = ApprovedCostCeiling(
        amount_usd=Decimal(str(raw_cost.get("amount_usd"))),
        approval_reference=str(raw_cost.get("approval_reference")),
    )
    if cost_ceiling.approval_reference != objectives.approval_reference:
        raise ValueError("cost and objective approvals must use the same reference")
    coordinates = {
        name: str(raw_snowflake.get(name))
        for name in (
            "account_sha256",
            "operator_user_sha256",
            "database",
            "warehouse",
            "role",
        )
    }
    if any(value in {"", "None"} for value in coordinates.values()):
        raise ValueError("objective manifest Snowflake coordinates are incomplete")
    return _Approval(
        objectives=objectives,
        cost_ceiling=cost_ceiling,
        account_sha256=coordinates["account_sha256"],
        operator_user_sha256=coordinates["operator_user_sha256"],
        database=coordinates["database"],
        warehouse=coordinates["warehouse"],
        role=coordinates["role"],
    )


def run_phase8_snowflake_bulk(
    config: SnowflakeBulkConfig,
    *,
    identity: CandidateIdentity,
    approval: _Approval,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    """Run one bulk class in a disposable Snowflake schema."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_identity_match(identity, approval)
    _require_provider_match(config, approval)
    schema_name = f"DANDER_P8_BULK_{uuid.uuid4().hex[:12].upper()}"
    runtime = _warehouse_runtime(config, schema_name=schema_name)
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        narrow_rows, narrow_ms, narrow_operations = _write_table(
            runtime,
            database=config.database,
            schema=schema_name,
            table="narrow_records",
            pipeline_id="phase8_snowflake_bulk_narrow",
            rows=config.narrow_rows,
            payload_bytes=config.narrow_payload_bytes,
            copy_part_rows=config.copy_part_rows,
        )
        wide_rows, wide_ms, wide_operations = _write_table(
            runtime,
            database=config.database,
            schema=schema_name,
            table="wide_records",
            pipeline_id="phase8_snowflake_bulk_wide",
            rows=config.wide_rows,
            payload_bytes=config.wide_payload_bytes,
            copy_part_rows=config.copy_part_rows,
        )
        _require_table_shape(
            runtime,
            database=config.database,
            schema=schema_name,
            table="narrow_records",
            rows=config.narrow_rows,
            payload_bytes=config.narrow_payload_bytes,
        )
        _require_table_shape(
            runtime,
            database=config.database,
            schema=schema_name,
            table="wide_records",
            rows=config.wide_rows,
            payload_bytes=config.wide_payload_bytes,
        )
        staging_tables, staging_stages = _staging_residue(
            runtime,
            config.database,
            schema_name,
        )
        if staging_tables or staging_stages:
            raise SnowflakeBulkQualificationError(
                "Snowflake bulk qualification left run-scoped staging objects"
            )
    except SnowflakeBulkQualificationError:
        raise
    except Exception as error:
        raise SnowflakeBulkQualificationError(
            f"Snowflake bulk qualification failed in disposable schema {schema_name}"
        ) from error
    finally:
        _drop_schema(runtime, config.database, schema_name)
    cleanup = not _schema_exists(runtime, config.database, schema_name)
    if not cleanup:
        raise SnowflakeBulkQualificationError(
            f"Snowflake bulk qualification left disposable schema {schema_name}"
        )
    operations = (*narrow_operations, *wide_operations)
    result = _BulkResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        narrow_duration_ms=narrow_ms,
        narrow_rows=narrow_rows,
        narrow_logical_bytes=narrow_rows * (config.narrow_payload_bytes + 24),
        wide_duration_ms=wide_ms,
        wide_rows=wide_rows,
        wide_logical_bytes=wide_rows * (config.wide_payload_bytes + 24),
        copy_operations=len(operations),
        query_ids=_operation_query_ids(operations),
        staging_tables=staging_tables,
        staging_stages=staging_stages,
        cleanup_verified=cleanup,
    )
    return _bulk_report(
        config,
        identity,
        approval,
        result,
        provider_cost_usd=provider_cost_usd,
    )


def run_phase8_snowflake_incremental(
    config: SnowflakeIncrementalConfig,
    *,
    identity: CandidateIdentity,
    approval: _Approval,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    """Run one incremental class in a disposable Snowflake schema."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_identity_match(identity, approval)
    _require_provider_match(config, approval)
    schema_name = f"DANDER_P8_INCREMENTAL_{uuid.uuid4().hex[:12].upper()}"
    runtime = _warehouse_runtime(config, schema_name=schema_name)
    try:
        relation = RelationRef(
            catalog=config.database,
            namespace=schema_name,
            name="incremental_records",
        )
        publication = runtime.target_fence.claim(
            relation,
            FencingToken(
                lease_table=None,
                pipeline_id="phase8_snowflake_incremental",
                run_id="phase8-snowflake-incremental-one",
                token=1,
                authority_id=_INCREMENTAL_AUTHORITY_ID,
            ),
        )
        writer = runtime.writers.build_ingestion_writer(
            sandbox=False,
            batch_rows=config.copy_part_rows,
            schema_evolution=SchemaEvolution.STRICT,
            mode=WriteMode.INCREMENTAL,
            cursor_field="cursor_value",
        )
        if not isinstance(writer, SnowflakeStagedWriter):
            raise SnowflakeIncrementalQualificationError(
                "Snowflake incremental qualification did not select the staged writer"
            )
        target = WriteTarget(
            relation=relation,
            business_key=("id",),
            schema=(
                WriteField(name="id", data_type="STRING", mode="REQUIRED"),
                WriteField(name="payload", data_type="STRING"),
                WriteField(name="cursor_value", data_type="INT64", mode="REQUIRED"),
            ),
            publication_fence=publication,
        )
    except SnowflakeBulkQualificationError:
        _drop_schema(runtime, config.database, schema_name)
        raise
    except Exception as error:
        _drop_schema(runtime, config.database, schema_name)
        raise SnowflakeIncrementalQualificationError(
            f"Snowflake incremental qualification could not initialize {schema_name}"
        ) from error
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        seed_started = time.perf_counter()
        seed_rows = writer.write(_incremental_seed_records(config), target)
        seed_ms = _elapsed_ms(seed_started)
        seed_operations = writer.drain_telemetry()
        _require_copy_operations(seed_operations, workload="incremental seed")
        if seed_rows != config.seed_rows:
            raise SnowflakeIncrementalQualificationError(
                "Snowflake incremental seed affected an unexpected row count"
            )

        delta_started = time.perf_counter()
        delta_rows = writer.write(_incremental_delta_records(config), target)
        delta_ms = _elapsed_ms(delta_started)
        delta_operations = writer.drain_telemetry()
        _require_copy_operations(delta_operations, workload="incremental delta")
        if delta_rows != config.delta_rows:
            raise SnowflakeIncrementalQualificationError(
                "Snowflake incremental delta affected an unexpected row count"
            )

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
        regression_operations = writer.drain_telemetry()
        _require_copy_operations(regression_operations, workload="cursor regression")
        if regression_rows != 0:
            raise SnowflakeIncrementalQualificationError(
                "Snowflake incremental cursor regression changed the target"
            )
        final_rows = config.seed_rows + (config.delta_rows // 2)
        _require_incremental_result(
            runtime,
            database=config.database,
            schema=schema_name,
            config=config,
            expected_rows=final_rows,
        )
        staging_tables, staging_stages = _staging_residue(
            runtime,
            config.database,
            schema_name,
        )
        if staging_tables or staging_stages:
            raise SnowflakeIncrementalQualificationError(
                "Snowflake incremental qualification left run-scoped staging objects"
            )
    except SnowflakeBulkQualificationError:
        raise
    except Exception as error:
        raise SnowflakeIncrementalQualificationError(
            f"Snowflake incremental qualification failed in disposable schema {schema_name}"
        ) from error
    finally:
        _drop_schema(runtime, config.database, schema_name)
    cleanup = not _schema_exists(runtime, config.database, schema_name)
    if not cleanup:
        raise SnowflakeIncrementalQualificationError(
            f"Snowflake incremental qualification left disposable schema {schema_name}"
        )
    operations = (*seed_operations, *delta_operations, *regression_operations)
    row_width = config.payload_bytes + 32
    result = _IncrementalResult(
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
        copy_operations=len(operations),
        query_ids=_operation_query_ids(operations),
        staging_tables=staging_tables,
        staging_stages=staging_stages,
        cleanup_verified=cleanup,
    )
    return _incremental_report(
        config,
        identity,
        approval,
        result,
        provider_cost_usd=provider_cost_usd,
    )


def run_phase8_snowflake_concurrency(
    config: SnowflakeConcurrencyConfig,
    *,
    identity: CandidateIdentity,
    approval: _Approval,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    """Run independent pipelines and one controlled target-fence contention."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_identity_match(identity, approval)
    _require_provider_match(config, approval)
    schema_name = f"DANDER_P8_CONCURRENCY_{uuid.uuid4().hex[:12].upper()}"
    runtime = _warehouse_runtime(config, schema_name=schema_name)
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        operations = _write_concurrent_targets(runtime, config=config, schema=schema_name)
        stale_rejected, claim_attempts = _reject_stale_publication(
            runtime,
            database=config.database,
            schema=schema_name,
            copy_part_rows=config.copy_part_rows,
        )
        if not stale_rejected:
            raise SnowflakeConcurrencyQualificationError(
                "Snowflake concurrency qualification accepted a stale publication"
            )
        staging_tables, staging_stages = _staging_residue(
            runtime,
            config.database,
            schema_name,
        )
        if staging_tables or staging_stages:
            raise SnowflakeConcurrencyQualificationError(
                "Snowflake concurrency qualification left run-scoped staging objects"
            )
    except SnowflakeBulkQualificationError:
        raise
    except Exception as error:
        raise SnowflakeConcurrencyQualificationError(
            f"Snowflake concurrency qualification failed in disposable schema {schema_name}"
        ) from error
    finally:
        _drop_schema(runtime, config.database, schema_name)
    cleanup = not _schema_exists(runtime, config.database, schema_name)
    if not cleanup:
        raise SnowflakeConcurrencyQualificationError(
            f"Snowflake concurrency qualification left disposable schema {schema_name}"
        )
    total_rows = config.concurrent_pipelines * config.rows_per_pipeline
    result = _ConcurrencyResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        pipeline_count=config.concurrent_pipelines,
        rows_per_pipeline=config.rows_per_pipeline,
        total_rows=total_rows,
        logical_input_bytes=total_rows * (config.payload_bytes + 24),
        concurrent_claim_attempts=claim_attempts,
        stale_publications_rejected=1,
        copy_operations=len(operations),
        query_ids=_operation_query_ids(operations),
        staging_tables=staging_tables,
        staging_stages=staging_stages,
        cleanup_verified=cleanup,
    )
    return _concurrency_report(
        config,
        identity,
        approval,
        result,
        provider_cost_usd=provider_cost_usd,
    )


def run_phase8_snowflake_transform(
    config: SnowflakeTransformConfig,
    *,
    identity: CandidateIdentity,
    approval: _Approval,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    """Run scan, join, aggregation, incremental, and generic-test models."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_identity_match(identity, approval)
    _require_provider_match(config, approval)
    suffix = uuid.uuid4().hex[:12].upper()
    source_schema = f"DANDER_P8_TRANSFORM_SOURCE_{suffix}"
    target_schema = f"DANDER_P8_TRANSFORM_TARGET_{suffix}"
    runtime = _warehouse_runtime(config, schema_name=source_schema)
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        load_started = time.perf_counter()
        seed_operations = _seed_transform_sources(
            runtime,
            source_schema=source_schema,
            config=config,
        )
        load_duration_ms = _elapsed_ms(load_started)
        runner = runtime.transforms.build_transform_runner(
            graph_plan=None,
            build_models=True,
            raw_namespace=source_schema,
        )
        if not isinstance(runner, SnowflakeTransformRunner):
            raise SnowflakeTransformQualificationError(
                "Snowflake transform qualification did not select its native runner"
            )
    except SnowflakeBulkQualificationError:
        _drop_schema(runtime, config.database, target_schema)
        _drop_schema(runtime, config.database, source_schema)
        raise
    except Exception as error:
        _drop_schema(runtime, config.database, target_schema)
        _drop_schema(runtime, config.database, source_schema)
        raise SnowflakeTransformQualificationError(
            "Snowflake transform qualification could not initialize its disposable schemas"
        ) from error

    first_ownership = _transform_ownership(config.database, run_id="transform-one", token=1)
    second_ownership = _transform_ownership(config.database, run_id="transform-two", token=2)
    transform_started = time.perf_counter()
    try:
        with TemporaryDirectory(prefix="dander-phase8-snowflake-transform-") as temporary:
            models = Path(temporary)
            _write_transform_models(models, target_schema=target_schema)
            initial = runner.build(
                models,
                selected=("aggregate_records", "incremental_records"),
                ownership=first_ownership,
            )
            initial_query_ids = _require_transform_initial(
                runtime,
                target_schema=target_schema,
                config=config,
            )
            mutation_query_ids = _mutate_transform_sources(
                runtime,
                source_schema=source_schema,
                config=config,
            )
            replay = runner.build(
                models,
                selected=("incremental_records",),
                ownership=second_ownership,
            )
            incremental_query_ids = _require_transform_incremental(
                runtime,
                database=config.database,
                target_schema=target_schema,
                expected_rows=config.fact_rows + 1,
            )
            tested = runner.test(
                models,
                selected=("aggregate_records", "incremental_records"),
            )
            assertion_count = initial.assertions + replay.assertions + tested.assertions
            model_count = len(initial.models)
        transform_duration_ms = _elapsed_ms(transform_started)
        source_tables, source_stages = _staging_residue(
            runtime,
            config.database,
            source_schema,
        )
        target_tables, target_stages = _staging_residue(
            runtime,
            config.database,
            target_schema,
        )
        staging_tables = source_tables + target_tables
        staging_stages = source_stages + target_stages
        if staging_tables or staging_stages:
            raise SnowflakeTransformQualificationError(
                "Snowflake transform qualification left run-scoped staging objects"
            )
    except SnowflakeBulkQualificationError:
        raise
    except Exception as error:
        raise SnowflakeTransformQualificationError(
            "Snowflake transform qualification failed in its disposable schemas"
        ) from error
    finally:
        _drop_schema(runtime, config.database, target_schema)
        _drop_schema(runtime, config.database, source_schema)
    cleanup = not _schema_exists(
        runtime,
        config.database,
        target_schema,
    ) and not _schema_exists(runtime, config.database, source_schema)
    if not cleanup:
        raise SnowflakeTransformQualificationError(
            "Snowflake transform qualification left a disposable schema"
        )
    transform_operations = (*initial.telemetry, *replay.telemetry, *tested.telemetry)
    operation_query_ids = _operation_query_ids((*seed_operations, *transform_operations))
    query_ids = tuple(
        dict.fromkeys(
            (
                *operation_query_ids,
                *initial_query_ids,
                *mutation_query_ids,
                *incremental_query_ids,
            )
        )
    )[:100]
    input_rows = config.fact_rows + config.dimension_rows
    result = _TransformResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        load_duration_ms=load_duration_ms,
        transform_duration_ms=transform_duration_ms,
        input_rows=input_rows,
        logical_input_bytes=(config.fact_rows * 32) + (config.dimension_rows * 24),
        output_rows=config.fact_rows + 1,
        model_count=model_count,
        assertion_count=assertion_count,
        ownership_verifications=(first_ownership.verifications + second_ownership.verifications),
        copy_operations=len(seed_operations),
        query_ids=query_ids,
        staging_tables=staging_tables,
        staging_stages=staging_stages,
        cleanup_verified=cleanup,
    )
    return _transform_report(
        config,
        identity,
        approval,
        result,
        provider_cost_usd=provider_cost_usd,
    )


def run_phase8_snowflake_failure(
    config: SnowflakeFailureConfig,
    *,
    identity: CandidateIdentity,
    approval: _Approval,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    """Run bounded provider-specific connection, auth, fence, and timeout probes."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_identity_match(identity, approval)
    _require_provider_match(config, approval)
    schema_name = f"DANDER_P8_FAILURE_{uuid.uuid4().hex[:12].upper()}"
    runtime = _warehouse_runtime(config, schema_name=schema_name)
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        setup_query_ids = _initialize_failure_schema(
            runtime,
            database=config.database,
            schema=schema_name,
        )
        connection_ms, connection_query_ids = _probe_closed_connection_recovery(runtime)
        credential_ms = _probe_credential_rejection(config, schema_name=schema_name)
        stale_rejected = _probe_stale_fence_rejection(
            runtime,
            database=config.database,
            schema=schema_name,
        )
        timeout_ms, timeout_query_ids = _probe_warehouse_timeout_rollback(
            runtime,
            database=config.database,
            schema=schema_name,
            statement_timeout_seconds=config.statement_timeout_seconds,
            cancellation_wait_seconds=config.cancellation_wait_seconds,
        )
        staging_tables, staging_stages = _staging_residue(
            runtime,
            config.database,
            schema_name,
        )
        if staging_tables or staging_stages:
            raise SnowflakeFailureQualificationError(
                "Snowflake failure qualification left run-scoped staging objects"
            )
    except SnowflakeBulkQualificationError:
        raise
    except Exception as error:
        raise SnowflakeFailureQualificationError(
            f"Snowflake failure qualification failed in disposable schema {schema_name}"
        ) from error
    finally:
        _drop_schema(runtime, config.database, schema_name)
    cleanup = not _schema_exists(runtime, config.database, schema_name)
    if not cleanup:
        raise SnowflakeFailureQualificationError(
            f"Snowflake failure qualification left disposable schema {schema_name}"
        )
    query_ids = tuple(dict.fromkeys((*setup_query_ids, *connection_query_ids, *timeout_query_ids)))[
        :100
    ]
    result = _FailureResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        probe_count=4,
        connection_recovery_duration_ms=connection_ms,
        credential_rejection_duration_ms=credential_ms,
        timeout_rollback_duration_ms=timeout_ms,
        stale_publications_rejected=1 if stale_rejected else 0,
        query_ids=query_ids,
        staging_tables=staging_tables,
        staging_stages=staging_stages,
        cleanup_verified=cleanup,
    )
    return _failure_report(
        config,
        identity,
        approval,
        result,
        provider_cost_usd=provider_cost_usd,
    )


def _initialize_failure_schema(
    runtime: WarehouseRuntime,
    *,
    database: str,
    schema: str,
) -> tuple[str, ...]:
    with open_connection(_connection_factory(runtime)) as connection:
        created_schema = execute(
            connection,
            f"CREATE SCHEMA IF NOT EXISTS {_qualified(database, schema)}",
        )
        created_table = execute(
            connection,
            f"CREATE TABLE {_qualified(database, schema, 'failure_records')} "
            '("id" NUMBER(38,0) NOT NULL, "value" NUMBER(38,0) NOT NULL)',
        )
        inserted = execute(
            connection,
            f"INSERT INTO {_qualified(database, schema, 'failure_records')} "
            '("id", "value") VALUES (?, ?)',
            (1, 1),
        )
        connection.commit()
    return _statement_query_ids(
        created_schema.query_id,
        created_table.query_id,
        inserted.query_id,
    )


def _probe_closed_connection_recovery(
    runtime: WarehouseRuntime,
) -> tuple[int, tuple[str, ...]]:
    started = time.perf_counter()
    factory = _connection_factory(runtime)
    connection = factory()
    try:
        initial = execute(connection, "SELECT 1", fetch="one")
    finally:
        connection.close()
    closed_failure_observed = False
    try:
        execute(connection, "SELECT 1", fetch="one")
    except Exception:
        closed_failure_observed = True
    if not closed_failure_observed:
        raise SnowflakeFailureQualificationError(
            "Snowflake failure qualification did not observe its closed connection"
        )
    with open_connection(factory) as replacement:
        recovered = execute(replacement, "SELECT 1", fetch="one")
    if _count(recovered.row) != 1:
        raise SnowflakeFailureQualificationError(
            "Snowflake failure qualification did not recover on a replacement connection"
        )
    return _elapsed_ms(started), _statement_query_ids(initial.query_id, recovered.query_id)


def _probe_credential_rejection(
    config: SnowflakeFailureConfig,
    *,
    schema_name: str,
) -> int:
    valid_token = os.environ.get(config.token_env)
    if not valid_token:
        raise SnowflakeFailureQualificationError(
            "Snowflake failure qualification requires its projected OAuth token"
        )
    invalid_env = "DANDER_SNOWFLAKE_PHASE8_REJECTED_TOKEN"
    previous_invalid_token = os.environ.get(invalid_env)
    os.environ[invalid_env] = hashlib.sha256(valid_token.encode()).hexdigest()
    started = time.perf_counter()
    try:
        invalid_config = SnowflakeFailureConfig(
            account=config.account,
            user=config.user,
            database=config.database,
            warehouse=config.warehouse,
            role=config.role,
            token_env=invalid_env,
            statement_timeout_seconds=config.statement_timeout_seconds,
            cancellation_wait_seconds=config.cancellation_wait_seconds,
            invalid_login_timeout_seconds=config.invalid_login_timeout_seconds,
        )
        values = _provider_values(invalid_config, schema_name=schema_name)
        values["login_timeout_seconds"] = config.invalid_login_timeout_seconds
        values["network_timeout_seconds"] = config.invalid_login_timeout_seconds
        registry = default_provider_registry()
        parsed = registry.parse(ProviderKind.WAREHOUSE, values)
        rejected = False
        try:
            invalid_runtime = registry.build(
                ProviderKind.WAREHOUSE,
                parsed,
                context={"catalog": config.database},
            )
        except ProviderFactoryError:
            rejected = True
        else:
            if not isinstance(invalid_runtime, WarehouseRuntime):
                raise SnowflakeFailureQualificationError(
                    "Snowflake failure qualification built an invalid warehouse runtime"
                )
            try:
                with open_connection(_connection_factory(invalid_runtime)) as connection:
                    execute(connection, "SELECT 1", fetch="one")
            except Exception:
                rejected = True
        if not rejected:
            raise SnowflakeFailureQualificationError(
                "Snowflake failure qualification accepted a rejected credential"
            )
    finally:
        if previous_invalid_token is None:
            os.environ.pop(invalid_env, None)
        else:
            os.environ[invalid_env] = previous_invalid_token
    return _elapsed_ms(started)


def _probe_stale_fence_rejection(
    runtime: WarehouseRuntime,
    *,
    database: str,
    schema: str,
) -> bool:
    relation = RelationRef(catalog=database, namespace=schema, name="failure_records")
    accepted = FencingToken(
        lease_table=None,
        pipeline_id="phase8_snowflake_failure",
        run_id="phase8-snowflake-failure-newer",
        token=2,
        authority_id=_FAILURE_AUTHORITY_ID,
    )
    runtime.target_fence.claim(relation, accepted)
    try:
        runtime.target_fence.claim(
            relation,
            FencingToken(
                lease_table=None,
                pipeline_id=accepted.pipeline_id,
                run_id="phase8-snowflake-failure-stale",
                token=1,
                authority_id=_FAILURE_AUTHORITY_ID,
            ),
        )
    except TargetFenceLostError:
        return True
    raise SnowflakeFailureQualificationError(
        "Snowflake failure qualification accepted a stale publication"
    )


def _probe_warehouse_timeout_rollback(
    runtime: WarehouseRuntime,
    *,
    database: str,
    schema: str,
    statement_timeout_seconds: int,
    cancellation_wait_seconds: int,
) -> tuple[int, tuple[str, ...]]:
    started = time.perf_counter()
    with open_connection(_connection_factory(runtime)) as connection:
        configured = execute(
            connection,
            f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {statement_timeout_seconds}",
        )
        execute(connection, "BEGIN TRANSACTION")
        updated = execute(
            connection,
            f"UPDATE {_qualified(database, schema, 'failure_records')} "
            'SET "value" = 2 WHERE "id" = 1',
        )
        timed_out = False
        try:
            execute(
                connection,
                f"CALL SYSTEM$WAIT({cancellation_wait_seconds}, 'SECONDS')",
            )
        except Exception:
            timed_out = True
        finally:
            connection.rollback()
    if not timed_out:
        raise SnowflakeFailureQualificationError(
            "Snowflake failure qualification did not observe its statement timeout"
        )
    with open_connection(_connection_factory(runtime)) as connection:
        verified = execute(
            connection,
            f'SELECT "value" FROM {_qualified(database, schema, "failure_records")} WHERE "id" = 1',
            fetch="one",
        )
    if _count(verified.row) != 1:
        raise SnowflakeFailureQualificationError(
            "Snowflake failure qualification did not roll back its timed-out transaction"
        )
    return _elapsed_ms(started), _statement_query_ids(
        configured.query_id,
        updated.query_id,
        verified.query_id,
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


def _require_provider_match(config: SnowflakeScaleConfig, approval: _Approval) -> None:
    if (
        _identifier_sha256(config.account) != approval.account_sha256
        or _identifier_sha256(config.user) != approval.operator_user_sha256
        or config.database != approval.database
        or config.warehouse != approval.warehouse
        or config.role != approval.role
    ):
        raise ValueError("objective approval does not match the private Snowflake coordinates")


def _identifier_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _warehouse_runtime(config: SnowflakeScaleConfig, *, schema_name: str) -> WarehouseRuntime:
    registry = default_provider_registry()
    parsed = registry.parse(
        ProviderKind.WAREHOUSE,
        _provider_values(config, schema_name=schema_name),
    )
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        parsed,
        context={"catalog": config.database},
    )
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("Snowflake provider returned an invalid warehouse runtime")
    return runtime


def _provider_values(config: SnowflakeScaleConfig, *, schema_name: str) -> dict[str, object]:
    auth: dict[str, object]
    if config.auth_method == "oauth":
        auth = {"method": "oauth", "token_env": config.token_env}
    else:
        auth = {
            "method": "key_pair",
            "private_key_file_env": config.private_key_file_env,
        }
        if config.private_key_password_env is not None:
            auth["private_key_password_env"] = config.private_key_password_env
    return {
        "provider": "snowflake",
        "account": config.account,
        "user": config.user,
        "database": config.database,
        "schema": schema_name,
        "warehouse": config.warehouse,
        "role": config.role,
        "auth": auth,
        "login_timeout_seconds": 30,
        "max_rows_per_file": config.copy_part_rows,
        "max_logical_bytes_per_file": config.copy_part_logical_bytes,
        "direct_max_rows": 0,
        "direct_max_logical_bytes": 0,
    }


def _write_table(
    runtime: WarehouseRuntime,
    *,
    database: str,
    schema: str,
    table: str,
    pipeline_id: str,
    rows: int,
    payload_bytes: int,
    copy_part_rows: int,
    authority_id: str = _AUTHORITY_ID,
) -> tuple[int, int, tuple[OperationTelemetry, ...]]:
    relation = RelationRef(catalog=database, namespace=schema, name=table)
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=pipeline_id,
            run_id=f"{pipeline_id}-one",
            token=1,
            authority_id=authority_id,
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=copy_part_rows,
        schema_evolution=SchemaEvolution.STRICT,
    )
    if not isinstance(writer, SnowflakeStagedWriter):
        raise SnowflakeBulkQualificationError(
            "Snowflake bulk qualification did not select the staged writer"
        )
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
    affected = writer.write(_records(rows, payload_bytes), target)
    operations = writer.drain_telemetry()
    if not operations or any(
        operation.transport is not WriteTransport.COPY for operation in operations
    ):
        raise SnowflakeBulkQualificationError(
            "Snowflake bulk qualification did not use COPY for the complete workload"
        )
    if affected != rows:
        raise SnowflakeBulkQualificationError(
            "Snowflake bulk write affected an unexpected row count"
        )
    return affected, _elapsed_ms(started), operations


def _write_concurrent_targets(
    runtime: WarehouseRuntime,
    *,
    config: SnowflakeConcurrencyConfig,
    schema: str,
) -> tuple[OperationTelemetry, ...]:
    operations: list[OperationTelemetry] = []
    with ThreadPoolExecutor(max_workers=config.concurrent_pipelines) as executor:
        futures = tuple(
            executor.submit(
                _write_table,
                runtime,
                database=config.database,
                schema=schema,
                table=f"pipeline_{index:02d}_records",
                pipeline_id=f"phase8_snowflake_concurrency_{index:02d}",
                rows=config.rows_per_pipeline,
                payload_bytes=config.payload_bytes,
                copy_part_rows=config.copy_part_rows,
                authority_id=_CONCURRENCY_AUTHORITY_ID,
            )
            for index in range(config.concurrent_pipelines)
        )
        for index, future in enumerate(futures):
            affected, _duration_ms, pipeline_operations = future.result()
            if affected != config.rows_per_pipeline:
                raise SnowflakeConcurrencyQualificationError(
                    "Snowflake concurrent pipeline affected an unexpected row count"
                )
            _require_table_shape(
                runtime,
                database=config.database,
                schema=schema,
                table=f"pipeline_{index:02d}_records",
                rows=config.rows_per_pipeline,
                payload_bytes=config.payload_bytes,
            )
            operations.extend(pipeline_operations)
    return tuple(operations)


def _transform_ownership(database: str, *, run_id: str, token: int) -> _Ownership:
    return _Ownership(
        FencingToken(
            lease_table=None,
            pipeline_id="phase8_snowflake_transform",
            run_id=run_id,
            token=token,
            authority_id=f"{_TRANSFORM_AUTHORITY_ID}:{database}",
        )
    )


def _seed_transform_sources(
    runtime: WarehouseRuntime,
    *,
    source_schema: str,
    config: SnowflakeTransformConfig,
) -> tuple[OperationTelemetry, ...]:
    dimensions = _write_transform_source(
        runtime,
        config=config,
        source_schema=source_schema,
        table="dimensions",
        pipeline_id="phase8_snowflake_transform_dimensions",
        business_key=("dimension_id",),
        fields=(
            WriteField(name="dimension_id", data_type="INT64", mode="REQUIRED"),
            WriteField(name="category", data_type="STRING", mode="REQUIRED"),
        ),
        records=_transform_dimension_records(config),
        expected_rows=config.dimension_rows,
    )
    facts = _write_transform_source(
        runtime,
        config=config,
        source_schema=source_schema,
        table="facts",
        pipeline_id="phase8_snowflake_transform_facts",
        business_key=("id",),
        fields=(
            WriteField(name="id", data_type="INT64", mode="REQUIRED"),
            WriteField(name="dimension_id", data_type="INT64", mode="REQUIRED"),
            WriteField(name="amount", data_type="INT64", mode="REQUIRED"),
            WriteField(name="updated_at", data_type="INT64", mode="REQUIRED"),
        ),
        records=_transform_fact_records(config),
        expected_rows=config.fact_rows,
    )
    return (*dimensions, *facts)


def _write_transform_source(
    runtime: WarehouseRuntime,
    *,
    config: SnowflakeTransformConfig,
    source_schema: str,
    table: str,
    pipeline_id: str,
    business_key: tuple[str, ...],
    fields: tuple[WriteField, ...],
    records: Iterator[dict[str, object]],
    expected_rows: int,
) -> tuple[OperationTelemetry, ...]:
    relation = RelationRef(catalog=config.database, namespace=source_schema, name=table)
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=pipeline_id,
            run_id=f"{pipeline_id}-one",
            token=1,
            authority_id=_TRANSFORM_AUTHORITY_ID,
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=config.copy_part_rows,
        schema_evolution=SchemaEvolution.STRICT,
    )
    if not isinstance(writer, SnowflakeStagedWriter):
        raise SnowflakeTransformQualificationError(
            "Snowflake transform source seed did not select the staged writer"
        )
    target = WriteTarget(
        relation=relation,
        business_key=business_key,
        schema=fields,
        publication_fence=publication,
    )
    affected = writer.write(records, target)
    operations = writer.drain_telemetry()
    if not operations or any(
        operation.transport is not WriteTransport.COPY for operation in operations
    ):
        raise SnowflakeTransformQualificationError(
            "Snowflake transform source seed did not use COPY for the complete workload"
        )
    if affected != expected_rows:
        raise SnowflakeTransformQualificationError(
            "Snowflake transform source seed affected an unexpected row count"
        )
    return operations


def _transform_dimension_records(
    config: SnowflakeTransformConfig,
) -> Iterator[dict[str, object]]:
    for index in range(config.dimension_rows):
        yield {"dimension_id": index, "category": f"category_{index % 10}"}


def _transform_fact_records(
    config: SnowflakeTransformConfig,
) -> Iterator[dict[str, object]]:
    for index in range(1, config.fact_rows + 1):
        yield {
            "id": index,
            "dimension_id": index % config.dimension_rows,
            "amount": index % 17,
            "updated_at": 1,
        }


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
            "description: Phase 8 Snowflake transform qualification.\n"
            "owner: data-eng\n"
            "dialect: portable\n"
            f"materialization: {materialization}\n"
            f"dataset: {target_schema}\n"
            "source_system: phase8_fixture\n"
            "sensitivity: public\n"
            f"{incremental}"
            f"columns:\n{column_yaml}"
            f"tests:\n{tests}",
            encoding="utf-8",
        )


def _mutate_transform_sources(
    runtime: WarehouseRuntime,
    *,
    source_schema: str,
    config: SnowflakeTransformConfig,
) -> tuple[str, ...]:
    with open_connection(_connection_factory(runtime)) as connection:
        updated = execute(
            connection,
            f"UPDATE {_qualified(config.database, source_schema, 'facts')} "
            'SET "amount" = 999, "updated_at" = 2 WHERE "id" = 1',
        )
        inserted = execute(
            connection,
            f"INSERT INTO {_qualified(config.database, source_schema, 'facts')} "
            '("id", "dimension_id", "amount", "updated_at") VALUES (?, 1, 5, 2)',
            (config.fact_rows + 1,),
        )
        connection.commit()
    if updated.rowcount != 1 or inserted.rowcount != 1:
        raise SnowflakeTransformQualificationError(
            "Snowflake transform delta affected an unexpected row count"
        )
    return _statement_query_ids(updated.query_id, inserted.query_id)


def _require_transform_initial(
    runtime: WarehouseRuntime,
    *,
    target_schema: str,
    config: SnowflakeTransformConfig,
) -> tuple[str, ...]:
    with open_connection(_connection_factory(runtime)) as connection:
        scan = execute(
            connection,
            f"SELECT COUNT(*) FROM {_qualified(config.database, target_schema, 'scan_records')}",
            fetch="one",
        )
        joined = execute(
            connection,
            'SELECT COUNT(*), COUNT(DISTINCT "category") FROM '
            f"{_qualified(config.database, target_schema, 'joined_records')}",
            fetch="one",
        )
        aggregate = execute(
            connection,
            'SELECT COUNT(*), SUM("row_count"), SUM("total_amount") FROM '
            f"{_qualified(config.database, target_schema, 'aggregate_records')}",
            fetch="one",
        )
        incremental = execute(
            connection,
            f"SELECT COUNT(*) FROM "
            f"{_qualified(config.database, target_schema, 'incremental_records')}",
            fetch="one",
        )
    expected_amount = sum(value % 17 for value in range(1, config.fact_rows + 1))
    if _integer_row(scan.row, 1) != (config.fact_rows,):
        raise SnowflakeTransformQualificationError(
            "Snowflake transform scan produced unexpected rows"
        )
    if _integer_row(joined.row, 2) != (config.fact_rows, 10):
        raise SnowflakeTransformQualificationError(
            "Snowflake transform join produced unexpected rows"
        )
    if _integer_row(aggregate.row, 3) != (10, config.fact_rows, expected_amount):
        raise SnowflakeTransformQualificationError(
            "Snowflake transform aggregation produced unexpected rows"
        )
    if _integer_row(incremental.row, 1) != (config.fact_rows,):
        raise SnowflakeTransformQualificationError(
            "Snowflake transform incremental seed produced unexpected rows"
        )
    return _statement_query_ids(
        scan.query_id,
        joined.query_id,
        aggregate.query_id,
        incremental.query_id,
    )


def _require_transform_incremental(
    runtime: WarehouseRuntime,
    *,
    database: str,
    target_schema: str,
    expected_rows: int,
) -> tuple[str, ...]:
    with open_connection(_connection_factory(runtime)) as connection:
        result = execute(
            connection,
            "SELECT COUNT(*), "
            'COUNT_IF("id" = 1 AND "amount" = 999 AND "updated_at" = 2), '
            'COUNT_IF("id" = ? AND "amount" = 5 AND "updated_at" = 2) FROM '
            f"{_qualified(database, target_schema, 'incremental_records')}",
            (expected_rows,),
            fetch="one",
        )
    if _integer_row(result.row, 3) != (expected_rows, 1, 1):
        raise SnowflakeTransformQualificationError(
            "Snowflake transform incremental merge produced unexpected rows"
        )
    return _statement_query_ids(result.query_id)


def _reject_stale_publication(
    runtime: WarehouseRuntime,
    *,
    database: str,
    schema: str,
    copy_part_rows: int,
) -> tuple[bool, int]:
    target_fence = cast("SnowflakeTargetFence", runtime.target_fence)
    relation = RelationRef(
        catalog=database,
        namespace=schema,
        name="contention_records",
    )
    old_token = FencingToken(
        lease_table=None,
        pipeline_id="phase8_snowflake_concurrency_contention",
        run_id="contention-old",
        token=20,
        authority_id=_CONCURRENCY_AUTHORITY_ID,
    )
    old_publication = target_fence.claim(relation, old_token)
    newer_token = FencingToken(
        lease_table=None,
        pipeline_id=old_token.pipeline_id,
        run_id="contention-new",
        token=21,
        authority_id=old_token.authority_id,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(target_fence.claim, relation, old_token),
            executor.submit(target_fence.claim, relation, newer_token),
        )
        for future in futures:
            with suppress(TargetFenceLostError):
                future.result()
    target_fence.claim(relation, newer_token)
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=copy_part_rows,
        schema_evolution=SchemaEvolution.STRICT,
        mode=WriteMode.SCD1,
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="payload", data_type="STRING"),
        ),
        publication_fence=old_publication,
    )
    try:
        writer.write(({"id": "stale", "payload": "must-not-publish"},), target)
    except TargetFenceLostError:
        _require_count(runtime, relation, expected=0)
        return True, len(futures)
    raise SnowflakeConcurrencyQualificationError(
        "Snowflake concurrency qualification accepted a stale publication"
    )


def _records(rows: int, payload_bytes: int) -> Iterator[dict[str, object]]:
    padding = "x" * payload_bytes
    for index in range(rows):
        yield {"id": f"{index:012d}", "payload": padding}


def _incremental_seed_records(
    config: SnowflakeIncrementalConfig,
) -> Iterator[dict[str, object]]:
    padding = "s" * config.payload_bytes
    for index in range(config.seed_rows):
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 1}


def _incremental_delta_records(
    config: SnowflakeIncrementalConfig,
) -> Iterator[dict[str, object]]:
    updated = config.delta_rows // 2
    padding = "d" * config.payload_bytes
    for index in range(updated):
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 2}
    for offset in range(config.delta_rows - updated):
        index = config.seed_rows + offset
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 2}


def _require_copy_operations(
    operations: tuple[OperationTelemetry, ...],
    *,
    workload: str,
) -> None:
    if not operations or any(
        operation.transport is not WriteTransport.COPY for operation in operations
    ):
        raise SnowflakeIncrementalQualificationError(
            f"Snowflake {workload} did not use COPY for the complete workload"
        )


def _operation_query_ids(
    operations: tuple[OperationTelemetry, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            operation.query_id
            for operation in operations
            if isinstance(operation.query_id, str) and operation.query_id
        )
    )[:100]


def _statement_query_ids(*query_ids: str | None) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(query_id for query_id in query_ids if isinstance(query_id, str) and query_id)
    )


def _integer_row(row: object, expected_columns: int) -> tuple[int, ...]:
    if not isinstance(row, (tuple, list)) or len(row) != expected_columns:
        raise SnowflakeTransformQualificationError("Snowflake transform readback was malformed")
    try:
        return tuple(int(value) for value in row)
    except (TypeError, ValueError) as error:
        raise SnowflakeTransformQualificationError(
            "Snowflake transform readback returned a non-integer"
        ) from error


def _require_table_shape(
    runtime: WarehouseRuntime,
    *,
    database: str,
    schema: str,
    table: str,
    rows: int,
    payload_bytes: int,
) -> None:
    with open_connection(_connection_factory(runtime)) as connection:
        result = execute(
            connection,
            f'SELECT COUNT(*), COUNT(DISTINCT "id"), '
            f'MIN(LENGTH("payload")), MAX(LENGTH("payload")) '
            f"FROM {_qualified(database, schema, table)}",
            fetch="one",
        ).row
    if not isinstance(result, (tuple, list)) or len(result) != 4:
        raise SnowflakeBulkQualificationError("Snowflake bulk readback was malformed")
    if tuple(int(value) for value in result) != (rows, rows, payload_bytes, payload_bytes):
        raise SnowflakeBulkQualificationError("Snowflake bulk readback differs from the workload")


def _require_count(runtime: WarehouseRuntime, relation: RelationRef, *, expected: int) -> None:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(
            connection,
            f"SELECT COUNT(*) FROM {_qualified(*relation.coordinates)}",
            fetch="one",
        ).row
    if _count(row) != expected:
        raise SnowflakeConcurrencyQualificationError(
            "Snowflake stale publication changed its contended target"
        )


def _require_incremental_result(
    runtime: WarehouseRuntime,
    *,
    database: str,
    schema: str,
    config: SnowflakeIncrementalConfig,
    expected_rows: int,
) -> None:
    boundary = f"{config.seed_rows:012d}"
    expected_delta_half = config.delta_rows // 2
    with open_connection(_connection_factory(runtime)) as connection:
        result = execute(
            connection,
            f"SELECT COUNT(*), "
            f'COUNT_IF("cursor_value" = 2 AND "id" < ?), '
            f'COUNT_IF("cursor_value" = 2 AND "id" >= ?), '
            f'COUNT_IF("id" = \'000000000000\' AND "cursor_value" = 2 '
            f'AND "payload" = ?), '
            f'MIN(IFF("cursor_value" = 2, LENGTH("payload"), NULL)), '
            f'MAX(IFF("cursor_value" = 2, LENGTH("payload"), NULL)) '
            f"FROM {_qualified(database, schema, 'incremental_records')}",
            (boundary, boundary, "d" * config.payload_bytes),
            fetch="one",
        ).row
    if not isinstance(result, (tuple, list)) or len(result) != 6:
        raise SnowflakeIncrementalQualificationError("Snowflake incremental readback was malformed")
    expected = (
        expected_rows,
        expected_delta_half,
        expected_delta_half,
        1,
        config.payload_bytes,
        config.payload_bytes,
    )
    if tuple(int(value) for value in result) != expected:
        raise SnowflakeIncrementalQualificationError(
            "Snowflake incremental readback differs from the accepted workload"
        )


def _staging_residue(
    runtime: WarehouseRuntime,
    database: str,
    schema_name: str,
) -> tuple[int, int]:
    with open_connection(_connection_factory(runtime)) as connection:
        tables = execute(
            connection,
            f"SELECT COUNT(*) FROM {_qualified(database, 'INFORMATION_SCHEMA', 'TABLES')} "
            "WHERE TABLE_SCHEMA = ? "
            "AND REGEXP_LIKE(TABLE_NAME, '^dander_(stage|model)_[0-9a-f]{20}$')",
            (schema_name,),
            fetch="one",
        ).row
        stages = execute(
            connection,
            f"SELECT COUNT(*) FROM {_qualified(database, 'INFORMATION_SCHEMA', 'STAGES')} "
            "WHERE STAGE_SCHEMA = ? AND STAGE_NAME ILIKE 'DANDER_FILES_%'",
            (schema_name,),
            fetch="one",
        ).row
    return _count(tables), _count(stages)


def _drop_schema(runtime: WarehouseRuntime, database: str, schema_name: str) -> None:
    try:
        with open_connection(_connection_factory(runtime)) as connection:
            execute(
                connection,
                f"DROP SCHEMA IF EXISTS {_qualified(database, schema_name)} CASCADE",
            )
            connection.commit()
    except Exception as error:
        raise SnowflakeBulkQualificationError(
            f"Snowflake bulk qualification could not remove disposable schema {schema_name}"
        ) from error


def _schema_exists(runtime: WarehouseRuntime, database: str, schema_name: str) -> bool:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(
            connection,
            f"SELECT COUNT(*) FROM {_qualified(database, 'INFORMATION_SCHEMA', 'SCHEMATA')} "
            "WHERE SCHEMA_NAME = ?",
            (schema_name,),
            fetch="one",
        ).row
    return _count(row) != 0


def _connection_factory(runtime: WarehouseRuntime) -> SnowflakeConnectionFactory:
    target_fence = cast("SnowflakeTargetFence", runtime.target_fence)
    return target_fence.connection_factory


def _count(row: object) -> int:
    if not isinstance(row, (tuple, list)) or not row:
        raise SnowflakeBulkQualificationError("Snowflake count query was malformed")
    try:
        return int(row[0])
    except (TypeError, ValueError) as error:
        raise SnowflakeBulkQualificationError(
            "Snowflake count query returned a non-integer"
        ) from error


def _qualified(*parts: str) -> str:
    return ".".join(f'"{part.replace(chr(34), chr(34) * 2)}"' for part in parts)


def _bulk_report(
    config: SnowflakeBulkConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _BulkResult,
    *,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    rows = result.narrow_rows + result.wide_rows
    logical_bytes = result.narrow_logical_bytes + result.wide_logical_bytes
    observed_cost = (
        provider_cost_usd if provider_cost_usd is not None else approval.cost_ceiling.amount_usd
    )
    cost = CostAttribution(
        provider="snowflake",
        service="virtual_warehouse",
        amount=observed_cost,
        estimated=provider_cost_usd is None,
    )
    cost_status = (
        ObjectiveStatus.NOT_EVALUATED
        if provider_cost_usd is None
        else (
            ObjectiveStatus.PASSED
            if provider_cost_usd <= approval.cost_ceiling.amount_usd
            else ObjectiveStatus.FAILED
        )
    )
    objectives = tuple(
        ObjectiveResult(
            name,
            cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
            f"phase8/snowflake/bulk/{name}",
        )
        for name in approval.objectives.names
    )
    status = (
        QualificationStatus.NOT_EVALUATED
        if cost_status is ObjectiveStatus.NOT_EVALUATED
        else (
            QualificationStatus.FAILED
            if cost_status is ObjectiveStatus.FAILED
            else QualificationStatus.PASSED
        )
    )
    measurements = PerformanceMeasurement.measured
    return QualificationReport(
        context=QualificationContext(
            release_version=identity.release_version,
            git_commit=identity.git_commit,
            image_digest=identity.image_digest,
            benchmark_date=identity.benchmark_date,
            profile_id=approval.objectives.profile_id,
            launcher=identity.launcher,
            warehouse="snowflake",
            state_backend="none",
            catalog="none",
            secret_provider=identity.secret_provider,
            regions=identity.regions,
            service_shapes=identity.service_shapes,
            provider_job_ids=tuple(sorted(set((*identity.provider_job_ids, *result.query_ids)))),
            cost_ceiling=approval.cost_ceiling,
        ),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
            input_rows=rows,
            logical_input_bytes=logical_bytes,
            row_width_bytes=config.wide_payload_bytes + 24,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="none_wide_and_narrow",
            concurrency=1,
            batch_rows=config.copy_part_rows,
            batch_bytes=config.copy_part_logical_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measurements("rows", "rows", rows),
            logical_bytes=measurements("logical_bytes", "bytes", logical_bytes),
            duration_ms=measurements("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measurements(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(rows, result.duration_ms),
            ),
            peak_rss_bytes=measurements("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measurements("retries", "count", 0),
            queue_duration_ms=measurements("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measurements(
                "load_duration_ms",
                "milliseconds",
                result.narrow_duration_ms + result.wide_duration_ms,
            ),
            transform_duration_ms=measurements("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measurements("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measurements("copy_operations", "count", result.copy_operations),
                measurements("narrow_duration_ms", "milliseconds", result.narrow_duration_ms),
                measurements("narrow_logical_bytes", "bytes", result.narrow_logical_bytes),
                measurements("narrow_rows", "rows", result.narrow_rows),
                measurements(
                    "narrow_throughput_rows_per_second",
                    "rows_per_second",
                    _throughput(result.narrow_rows, result.narrow_duration_ms),
                ),
                measurements("staging_stages", "count", result.staging_stages),
                measurements("staging_tables", "count", result.staging_tables),
                measurements("wide_duration_ms", "milliseconds", result.wide_duration_ms),
                measurements("wide_logical_bytes", "bytes", result.wide_logical_bytes),
                measurements("wide_rows", "rows", result.wide_rows),
                measurements(
                    "wide_throughput_rows_per_second",
                    "rows_per_second",
                    _throughput(result.wide_rows, result.wide_duration_ms),
                ),
            ),
            costs=(cost,),
        ),
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=status,
    )


def _incremental_report(
    config: SnowflakeIncrementalConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _IncrementalResult,
    *,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    ratio = Decimal(result.seed_rows) / Decimal(result.delta_rows)
    if ratio < 100:
        raise SnowflakeIncrementalQualificationError(
            "Snowflake incremental target is less than 100 times its delta"
        )
    observed_cost = (
        provider_cost_usd if provider_cost_usd is not None else approval.cost_ceiling.amount_usd
    )
    cost = CostAttribution(
        provider="snowflake",
        service="virtual_warehouse",
        amount=observed_cost,
        estimated=provider_cost_usd is None,
    )
    cost_status = (
        ObjectiveStatus.NOT_EVALUATED
        if provider_cost_usd is None
        else (
            ObjectiveStatus.PASSED
            if provider_cost_usd <= approval.cost_ceiling.amount_usd
            else ObjectiveStatus.FAILED
        )
    )
    objectives = tuple(
        ObjectiveResult(
            name,
            cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
            f"phase8/snowflake/incremental/{name}",
        )
        for name in approval.objectives.names
    )
    status = (
        QualificationStatus.NOT_EVALUATED
        if cost_status is ObjectiveStatus.NOT_EVALUATED
        else (
            QualificationStatus.FAILED
            if cost_status is ObjectiveStatus.FAILED
            else QualificationStatus.PASSED
        )
    )
    measurements = PerformanceMeasurement.measured
    return QualificationReport(
        context=QualificationContext(
            release_version=identity.release_version,
            git_commit=identity.git_commit,
            image_digest=identity.image_digest,
            benchmark_date=identity.benchmark_date,
            profile_id=approval.objectives.profile_id,
            launcher=identity.launcher,
            warehouse="snowflake",
            state_backend="none",
            catalog="none",
            secret_provider=identity.secret_provider,
            regions=identity.regions,
            service_shapes=identity.service_shapes,
            provider_job_ids=tuple(sorted(set((*identity.provider_job_ids, *result.query_ids)))),
            cost_ceiling=approval.cost_ceiling,
        ),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.INCREMENTAL,
            input_rows=result.delta_rows,
            logical_input_bytes=result.delta_logical_bytes,
            row_width_bytes=config.payload_bytes + 32,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="cursor_merge_small_delta",
            concurrency=1,
            batch_rows=config.copy_part_rows,
            batch_bytes=config.copy_part_logical_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measurements("rows", "rows", result.delta_rows),
            logical_bytes=measurements(
                "logical_bytes",
                "bytes",
                result.delta_logical_bytes,
            ),
            duration_ms=measurements("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measurements(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(result.delta_rows, result.delta_duration_ms),
            ),
            peak_rss_bytes=measurements("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measurements("retries", "count", 0),
            queue_duration_ms=measurements("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measurements(
                "load_duration_ms",
                "milliseconds",
                result.seed_duration_ms + result.delta_duration_ms,
            ),
            transform_duration_ms=measurements("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measurements("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measurements("copy_operations", "count", result.copy_operations),
                measurements("delta_duration_ms", "milliseconds", result.delta_duration_ms),
                measurements("delta_target_ratio", "ratio", ratio),
                measurements("final_target_rows", "rows", result.final_rows),
                measurements(
                    "regression_rows_affected",
                    "rows",
                    result.regression_rows_affected,
                ),
                measurements("seed_duration_ms", "milliseconds", result.seed_duration_ms),
                measurements("seed_logical_bytes", "bytes", result.seed_logical_bytes),
                measurements("seed_rows", "rows", result.seed_rows),
                measurements("staging_stages", "count", result.staging_stages),
                measurements("staging_tables", "count", result.staging_tables),
            ),
            costs=(cost,),
        ),
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=status,
    )


def _concurrency_report(
    config: SnowflakeConcurrencyConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _ConcurrencyResult,
    *,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    observed_cost = (
        provider_cost_usd if provider_cost_usd is not None else approval.cost_ceiling.amount_usd
    )
    cost = CostAttribution(
        provider="snowflake",
        service="virtual_warehouse",
        amount=observed_cost,
        estimated=provider_cost_usd is None,
    )
    cost_status = (
        ObjectiveStatus.NOT_EVALUATED
        if provider_cost_usd is None
        else (
            ObjectiveStatus.PASSED
            if provider_cost_usd <= approval.cost_ceiling.amount_usd
            else ObjectiveStatus.FAILED
        )
    )
    objectives = tuple(
        ObjectiveResult(
            name,
            cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
            f"phase8/snowflake/concurrency/{name}",
        )
        for name in approval.objectives.names
    )
    status = (
        QualificationStatus.NOT_EVALUATED
        if cost_status is ObjectiveStatus.NOT_EVALUATED
        else (
            QualificationStatus.FAILED
            if cost_status is ObjectiveStatus.FAILED
            else QualificationStatus.PASSED
        )
    )
    measurements = PerformanceMeasurement.measured
    return QualificationReport(
        context=QualificationContext(
            release_version=identity.release_version,
            git_commit=identity.git_commit,
            image_digest=identity.image_digest,
            benchmark_date=identity.benchmark_date,
            profile_id=approval.objectives.profile_id,
            launcher=identity.launcher,
            warehouse="snowflake",
            state_backend="none",
            catalog="none",
            secret_provider=identity.secret_provider,
            regions=identity.regions,
            service_shapes=identity.service_shapes,
            provider_job_ids=tuple(sorted(set((*identity.provider_job_ids, *result.query_ids)))),
            cost_ceiling=approval.cost_ceiling,
        ),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.CONCURRENT_PIPELINES,
            input_rows=result.total_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=config.payload_bytes + 24,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="independent_targets_and_contended_fence",
            concurrency=result.pipeline_count,
            batch_rows=config.copy_part_rows,
            batch_bytes=config.copy_part_logical_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measurements("rows", "rows", result.total_rows),
            logical_bytes=measurements(
                "logical_bytes",
                "bytes",
                result.logical_input_bytes,
            ),
            duration_ms=measurements("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measurements(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(result.total_rows, result.duration_ms),
            ),
            peak_rss_bytes=measurements("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measurements("retries", "count", 0),
            queue_duration_ms=measurements("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measurements(
                "load_duration_ms",
                "milliseconds",
                result.duration_ms,
            ),
            transform_duration_ms=measurements("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measurements("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measurements(
                    "concurrent_claim_attempts",
                    "count",
                    result.concurrent_claim_attempts,
                ),
                measurements("copy_operations", "count", result.copy_operations),
                measurements("pipeline_count", "count", result.pipeline_count),
                measurements("rows_per_pipeline", "rows", result.rows_per_pipeline),
                measurements("staging_stages", "count", result.staging_stages),
                measurements("staging_tables", "count", result.staging_tables),
                measurements(
                    "stale_publications_rejected",
                    "count",
                    result.stale_publications_rejected,
                ),
            ),
            costs=(cost,),
        ),
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=status,
    )


def _transform_report(
    config: SnowflakeTransformConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _TransformResult,
    *,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    observed_cost = (
        provider_cost_usd if provider_cost_usd is not None else approval.cost_ceiling.amount_usd
    )
    cost = CostAttribution(
        provider="snowflake",
        service="virtual_warehouse",
        amount=observed_cost,
        estimated=provider_cost_usd is None,
    )
    cost_status = (
        ObjectiveStatus.NOT_EVALUATED
        if provider_cost_usd is None
        else (
            ObjectiveStatus.PASSED
            if provider_cost_usd <= approval.cost_ceiling.amount_usd
            else ObjectiveStatus.FAILED
        )
    )
    objectives = tuple(
        ObjectiveResult(
            name,
            cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
            f"phase8/snowflake/transform/{name}",
        )
        for name in approval.objectives.names
    )
    status = (
        QualificationStatus.NOT_EVALUATED
        if cost_status is ObjectiveStatus.NOT_EVALUATED
        else (
            QualificationStatus.FAILED
            if cost_status is ObjectiveStatus.FAILED
            else QualificationStatus.PASSED
        )
    )
    measurements = PerformanceMeasurement.measured
    return QualificationReport(
        context=QualificationContext(
            release_version=identity.release_version,
            git_commit=identity.git_commit,
            image_digest=identity.image_digest,
            benchmark_date=identity.benchmark_date,
            profile_id=approval.objectives.profile_id,
            launcher=identity.launcher,
            warehouse="snowflake",
            state_backend="none",
            catalog="none",
            secret_provider=identity.secret_provider,
            regions=identity.regions,
            service_shapes=identity.service_shapes,
            provider_job_ids=tuple(sorted(set((*identity.provider_job_ids, *result.query_ids)))),
            cost_ceiling=approval.cost_ceiling,
        ),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.TRANSFORM,
            input_rows=result.input_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=32,
            schema_depth=4,
            source_rate_limit="unlimited_local_fixture",
            transform_complexity="scan_join_aggregate_incremental_tests",
            concurrency=1,
            batch_rows=config.copy_part_rows,
            batch_bytes=config.copy_part_logical_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measurements("rows", "rows", result.output_rows),
            logical_bytes=measurements(
                "logical_bytes",
                "bytes",
                result.logical_input_bytes,
            ),
            duration_ms=measurements("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measurements(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(result.output_rows, result.duration_ms),
            ),
            peak_rss_bytes=measurements("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measurements("retries", "count", 0),
            queue_duration_ms=measurements("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measurements(
                "load_duration_ms",
                "milliseconds",
                result.load_duration_ms,
            ),
            transform_duration_ms=measurements(
                "transform_duration_ms",
                "milliseconds",
                result.transform_duration_ms,
            ),
            catalog_duration_ms=measurements("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measurements("assertion_count", "count", result.assertion_count),
                measurements("copy_operations", "count", result.copy_operations),
                measurements("model_count", "count", result.model_count),
                measurements(
                    "ownership_verifications",
                    "count",
                    result.ownership_verifications,
                ),
                measurements("staging_stages", "count", result.staging_stages),
                measurements("staging_tables", "count", result.staging_tables),
            ),
            costs=(cost,),
        ),
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=status,
    )


def _failure_report(
    config: SnowflakeFailureConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _FailureResult,
    *,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    observed_cost = (
        provider_cost_usd if provider_cost_usd is not None else approval.cost_ceiling.amount_usd
    )
    cost = CostAttribution(
        provider="snowflake",
        service="virtual_warehouse",
        amount=observed_cost,
        estimated=provider_cost_usd is None,
    )
    cost_status = (
        ObjectiveStatus.NOT_EVALUATED
        if provider_cost_usd is None
        else (
            ObjectiveStatus.PASSED
            if provider_cost_usd <= approval.cost_ceiling.amount_usd
            else ObjectiveStatus.FAILED
        )
    )
    objectives = tuple(
        ObjectiveResult(
            name,
            cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
            f"phase8/snowflake/failure/{name}",
        )
        for name in approval.objectives.names
    )
    status = (
        QualificationStatus.NOT_EVALUATED
        if cost_status is ObjectiveStatus.NOT_EVALUATED
        else (
            QualificationStatus.FAILED
            if cost_status is ObjectiveStatus.FAILED
            else QualificationStatus.PASSED
        )
    )
    measurements = PerformanceMeasurement.measured
    return QualificationReport(
        context=QualificationContext(
            release_version=identity.release_version,
            git_commit=identity.git_commit,
            image_digest=identity.image_digest,
            benchmark_date=identity.benchmark_date,
            profile_id=approval.objectives.profile_id,
            launcher=identity.launcher,
            warehouse="snowflake",
            state_backend="none",
            catalog="none",
            secret_provider=identity.secret_provider,
            regions=identity.regions,
            service_shapes=identity.service_shapes,
            provider_job_ids=tuple(sorted(set((*identity.provider_job_ids, *result.query_ids)))),
            cost_ceiling=approval.cost_ceiling,
        ),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.FAILURE,
            input_rows=result.probe_count,
            logical_input_bytes=result.probe_count,
            row_width_bytes=1,
            schema_depth=1,
            source_rate_limit="controlled_provider_failure_injection",
            transform_complexity="connection_auth_fence_and_warehouse_timeout",
            concurrency=1,
            batch_rows=1,
            batch_bytes=1,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measurements("rows", "rows", result.probe_count),
            logical_bytes=measurements(
                "logical_bytes",
                "bytes",
                result.probe_count,
            ),
            duration_ms=measurements("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measurements(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(result.probe_count, result.duration_ms),
            ),
            peak_rss_bytes=measurements("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measurements("retries", "count", 0),
            queue_duration_ms=measurements("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measurements("load_duration_ms", "milliseconds", 0),
            transform_duration_ms=measurements("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measurements("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measurements(
                    "connection_recovery_duration_ms",
                    "milliseconds",
                    result.connection_recovery_duration_ms,
                ),
                measurements(
                    "credential_rejection_duration_ms",
                    "milliseconds",
                    result.credential_rejection_duration_ms,
                ),
                measurements("probe_count", "count", result.probe_count),
                measurements(
                    "staging_stages",
                    "count",
                    result.staging_stages,
                ),
                measurements(
                    "staging_tables",
                    "count",
                    result.staging_tables,
                ),
                measurements(
                    "stale_publications_rejected",
                    "count",
                    result.stale_publications_rejected,
                ),
                measurements(
                    "timeout_rollback_duration_ms",
                    "milliseconds",
                    result.timeout_rollback_duration_ms,
                ),
            ),
            costs=(cost,),
        ),
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=status,
    )


def _throughput(rows: int, duration_ms: int) -> Decimal:
    return (Decimal(rows) * 1_000 / Decimal(max(duration_ms, 1))).quantize(Decimal("0.001"))


def _elapsed_ms(started: float) -> int:
    return max(round((time.perf_counter() - started) * 1_000), 1)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1_024)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-class",
        choices=(
            BenchmarkClass.BULK_THROUGHPUT.value,
            BenchmarkClass.INCREMENTAL.value,
            BenchmarkClass.CONCURRENT_PIPELINES.value,
            BenchmarkClass.TRANSFORM.value,
            BenchmarkClass.FAILURE.value,
        ),
        default=BenchmarkClass.BULK_THROUGHPUT.value,
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--role")
    parser.add_argument("--auth-method", choices=("key_pair", "oauth"), default="oauth")
    parser.add_argument("--token-env", default="DANDER_SNOWFLAKE_OAUTH_TOKEN")
    parser.add_argument("--private-key-file-env", default="DANDER_SNOWFLAKE_PRIVATE_KEY_FILE")
    parser.add_argument("--private-key-password-env")
    parser.add_argument("--objectives", type=Path, required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--benchmark-date", type=date.fromisoformat, required=True)
    parser.add_argument("--provider-job-id", action="append", required=True)
    parser.add_argument("--service-shape", action="append", required=True)
    parser.add_argument("--launcher", default="docker_local")
    parser.add_argument("--region", action="append")
    parser.add_argument("--secret-provider", default="environment")
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--provider-cost-usd", type=Decimal)
    parser.add_argument("--narrow-rows", type=int, default=500_000)
    parser.add_argument("--narrow-payload-bytes", type=int, default=32)
    parser.add_argument("--wide-rows", type=int, default=200_000)
    parser.add_argument("--wide-payload-bytes", type=int, default=1_024)
    parser.add_argument("--copy-part-rows", type=int, default=50_000)
    parser.add_argument("--copy-part-logical-bytes", type=int, default=16 * 1_024 * 1_024)
    parser.add_argument("--incremental-seed-rows", type=int, default=300_000)
    parser.add_argument("--incremental-delta-rows", type=int, default=3_000)
    parser.add_argument("--incremental-payload-bytes", type=int, default=128)
    parser.add_argument("--concurrent-pipelines", type=int, default=4)
    parser.add_argument("--concurrent-rows-per-pipeline", type=int, default=5_000)
    parser.add_argument("--concurrent-payload-bytes", type=int, default=128)
    parser.add_argument("--concurrent-copy-part-rows", type=int, default=5_000)
    parser.add_argument("--transform-fact-rows", type=int, default=100_000)
    parser.add_argument("--transform-dimension-rows", type=int, default=100)
    parser.add_argument("--failure-statement-timeout-seconds", type=int, default=1)
    parser.add_argument("--failure-cancellation-wait-seconds", type=int, default=30)
    parser.add_argument("--failure-invalid-login-timeout-seconds", type=int, default=5)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    benchmark_class = BenchmarkClass(arguments.benchmark_class)
    try:
        common = {
            "account": arguments.account,
            "user": arguments.user,
            "database": arguments.database,
            "warehouse": arguments.warehouse,
            "role": arguments.role,
            "auth_method": arguments.auth_method,
            "token_env": arguments.token_env,
            "private_key_file_env": arguments.private_key_file_env,
            "private_key_password_env": arguments.private_key_password_env,
        }
        config: SnowflakeScaleConfig
        if benchmark_class is BenchmarkClass.BULK_THROUGHPUT:
            config = SnowflakeBulkConfig(
                **common,
                narrow_rows=arguments.narrow_rows,
                narrow_payload_bytes=arguments.narrow_payload_bytes,
                wide_rows=arguments.wide_rows,
                wide_payload_bytes=arguments.wide_payload_bytes,
                copy_part_rows=arguments.copy_part_rows,
                copy_part_logical_bytes=arguments.copy_part_logical_bytes,
            )
        elif benchmark_class is BenchmarkClass.INCREMENTAL:
            config = SnowflakeIncrementalConfig(
                **common,
                seed_rows=arguments.incremental_seed_rows,
                delta_rows=arguments.incremental_delta_rows,
                payload_bytes=arguments.incremental_payload_bytes,
                copy_part_rows=arguments.copy_part_rows,
                copy_part_logical_bytes=arguments.copy_part_logical_bytes,
            )
        elif benchmark_class is BenchmarkClass.CONCURRENT_PIPELINES:
            config = SnowflakeConcurrencyConfig(
                **common,
                concurrent_pipelines=arguments.concurrent_pipelines,
                rows_per_pipeline=arguments.concurrent_rows_per_pipeline,
                payload_bytes=arguments.concurrent_payload_bytes,
                copy_part_rows=arguments.concurrent_copy_part_rows,
                copy_part_logical_bytes=arguments.copy_part_logical_bytes,
            )
        elif benchmark_class is BenchmarkClass.TRANSFORM:
            config = SnowflakeTransformConfig(
                **common,
                fact_rows=arguments.transform_fact_rows,
                dimension_rows=arguments.transform_dimension_rows,
                copy_part_rows=arguments.copy_part_rows,
                copy_part_logical_bytes=arguments.copy_part_logical_bytes,
            )
        else:
            config = SnowflakeFailureConfig(
                **common,
                statement_timeout_seconds=arguments.failure_statement_timeout_seconds,
                cancellation_wait_seconds=arguments.failure_cancellation_wait_seconds,
                invalid_login_timeout_seconds=arguments.failure_invalid_login_timeout_seconds,
            )
        approval = load_approval(arguments.objectives, config=config)
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
        if isinstance(config, SnowflakeBulkConfig):
            report = run_phase8_snowflake_bulk(
                config,
                identity=identity,
                approval=approval,
                provider_cost_usd=arguments.provider_cost_usd,
            )
        elif isinstance(config, SnowflakeIncrementalConfig):
            report = run_phase8_snowflake_incremental(
                config,
                identity=identity,
                approval=approval,
                provider_cost_usd=arguments.provider_cost_usd,
            )
        elif isinstance(config, SnowflakeConcurrencyConfig):
            report = run_phase8_snowflake_concurrency(
                config,
                identity=identity,
                approval=approval,
                provider_cost_usd=arguments.provider_cost_usd,
            )
        elif isinstance(config, SnowflakeTransformConfig):
            report = run_phase8_snowflake_transform(
                config,
                identity=identity,
                approval=approval,
                provider_cost_usd=arguments.provider_cost_usd,
            )
        else:
            report = run_phase8_snowflake_failure(
                config,
                identity=identity,
                approval=approval,
                provider_cost_usd=arguments.provider_cost_usd,
            )
    except (ValueError, SnowflakeBulkQualificationError):
        print(
            json.dumps(
                {
                    "schema": "io.dander.qualification.failure/v1",
                    "provider": "snowflake",
                    "benchmark_class": benchmark_class.value,
                    "status": QualificationStatus.FAILED.value,
                    "summary": (
                        "Snowflake scale qualification failed; inspect provider logs and verify "
                        "cleanup before any bounded rerun."
                    ),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 1
    arguments.output_file.parent.mkdir(parents=True, exist_ok=True)
    arguments.output_file.write_text(report.to_json() + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "benchmark_class": benchmark_class.value,
                "cost_status": next(
                    item.status.value for item in report.objectives if item.name == "cost_ceiling"
                ),
                "release_version": __version__,
                "status": report.status.value,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
