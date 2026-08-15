#!/usr/bin/env python3
"""Measure the bounded PostgreSQL DIRECT-to-COPY crossover on one exact candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander import __version__
from dander.concurrency import FencingToken
from dander.providers import ProviderKind, default_provider_registry
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
from dander.telemetry import CostAttribution, PerformanceMeasurement, RunPerformance
from dander.warehouse import (
    RelationRef,
    RelationSchema,
    WarehouseRuntime,
    normalize_staging_record,
    staging_logical_size,
)
from dander.writer import (
    SchemaEvolution,
    WriteField,
    WriteTarget,
    WriteTransport,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]

_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.postgresql-crossover/v1"
_OBJECTIVES = (
    "canonical_equality",
    "cleanup",
    "copy_transport_observed",
    "cost_ceiling",
    "crossover_measured",
    "direct_transport_observed",
    "threshold_recorded",
)
_CROSSOVER_FIELDS = (
    WriteField(name="id", data_type="STRING", mode="REQUIRED"),
    WriteField(name="payload", data_type="STRING"),
)
_CROSSOVER_SCHEMA = RelationSchema(
    fields=tuple(field.to_canonical() for field in _CROSSOVER_FIELDS)
)


@dataclass(frozen=True, slots=True)
class PostgreSQLCrossoverConfig:
    """Reviewed row sizes and bounds for one local crossover measurement."""

    row_counts: tuple[int, ...] = (1, 10, 100, 1_000, 5_000)
    payload_bytes: int = 128
    repetitions: int = 5
    direct_max_logical_bytes: int = 1_024 * 1_024

    def __post_init__(self) -> None:
        if not self.row_counts or tuple(sorted(set(self.row_counts))) != self.row_counts:
            raise ValueError("row_counts must be unique positive values in ascending order")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in self.row_counts
        ):
            raise ValueError("row_counts must be unique positive values in ascending order")
        if (
            isinstance(self.payload_bytes, bool)
            or not isinstance(self.payload_bytes, int)
            or self.payload_bytes <= 0
        ):
            raise ValueError("payload_bytes must be a positive integer")
        if (
            isinstance(self.repetitions, bool)
            or not isinstance(self.repetitions, int)
            or self.repetitions < 3
            or self.repetitions % 2 == 0
        ):
            raise ValueError("repetitions must be an odd integer of at least three")
        if (
            isinstance(self.direct_max_logical_bytes, bool)
            or not isinstance(self.direct_max_logical_bytes, int)
            or self.direct_max_logical_bytes <= 0
            or self.direct_max_logical_bytes > 1_024 * 1_024
        ):
            raise ValueError("direct_max_logical_bytes must be between 1 and 1 MiB")
        if self.row_counts[-1] > 10_000:
            raise ValueError("crossover row counts cannot exceed the writer's 10,000-row bound")
        if (
            _workload_logical_bytes(self.row_counts[-1], self.payload_bytes)
            > self.direct_max_logical_bytes
        ):
            raise ValueError(
                "largest crossover workload does not fit the approved direct byte bound"
            )

    @property
    def row_width_bytes(self) -> int:
        return _workload_logical_bytes(1, self.payload_bytes)

    def workload_payload(self) -> dict[str, object]:
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.CROSSOVER.value,
            "row_counts": list(self.row_counts),
            "payload_bytes": self.payload_bytes,
            "repetitions": self.repetitions,
            "direct_max_rows": self.row_counts[-1],
            "direct_max_logical_bytes": self.direct_max_logical_bytes,
            "write_mode": "scd1",
            "transports": [WriteTransport.COPY.value, WriteTransport.DIRECT.value],
        }

    def configuration_sha256(self) -> str:
        encoded = json.dumps(
            self.workload_payload(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
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
class _Sample:
    transport: WriteTransport
    rows: int
    repetition: int
    duration_ms: int
    canonical_sha256: str


@dataclass(frozen=True, slots=True)
class _CrossoverResult:
    duration_ms: int
    peak_rss_bytes: int
    samples: tuple[_Sample, ...]
    medians: Mapping[WriteTransport, Mapping[int, int]]
    recommended_direct_max_rows: int
    recommended_direct_max_logical_bytes: int
    temporary_staging_relations: int
    cleanup_verified: bool


def load_approval(
    path: Path,
    *,
    config: PostgreSQLCrossoverConfig,
) -> _Approval:
    """Load the pre-mutation objective set bound to the exact crossover workload."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective manifest has an incompatible schema")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective manifest workload does not match crossover configuration")
    raw_cost = payload.get("cost_ceiling")
    raw_objectives = payload.get("approved_objectives")
    if not isinstance(raw_cost, dict) or not isinstance(raw_objectives, dict):
        raise ValueError("objective manifest is incomplete")
    if tuple(raw_objectives.get("names", ())) != _OBJECTIVES:
        raise ValueError("objective manifest does not contain the crossover objective set")
    objectives = ApprovedObjectiveSet(
        names=_OBJECTIVES,
        benchmark_class=BenchmarkClass(str(raw_objectives.get("benchmark_class"))),
        profile_id=str(raw_objectives.get("profile_id")),
        release_version=str(raw_objectives.get("release_version")),
        git_commit=str(raw_objectives.get("git_commit")),
        image_digest=str(raw_objectives.get("image_digest")),
        configuration_sha256=str(raw_objectives.get("configuration_sha256")),
        approval_reference=str(raw_objectives.get("approval_reference")),
    )
    if objectives.benchmark_class is not BenchmarkClass.CROSSOVER:
        raise ValueError("objective manifest benchmark class does not match crossover")
    if objectives.configuration_sha256 != config.configuration_sha256():
        raise ValueError("objective manifest configuration hash does not match crossover")
    cost_ceiling = ApprovedCostCeiling(
        amount_usd=Decimal(str(raw_cost.get("amount_usd"))),
        approval_reference=str(raw_cost.get("approval_reference")),
    )
    if cost_ceiling.amount_usd != 0:
        raise ValueError("local PostgreSQL crossover requires a zero-dollar ceiling")
    if cost_ceiling.approval_reference != objectives.approval_reference:
        raise ValueError("cost and objective approvals must use the same reference")
    return _Approval(objectives=objectives, cost_ceiling=cost_ceiling)


def run_postgresql_crossover_qualification(
    dsn: str,
    *,
    config: PostgreSQLCrossoverConfig,
    identity: CandidateIdentity,
    approval: _Approval,
) -> QualificationReport:
    """Measure both transports against equal targets and emit one normalized report."""
    if not dsn:
        raise ValueError("PostgreSQL crossover requires a non-empty DSN")
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    objectives = approval.objectives
    if (
        objectives.release_version != identity.release_version
        or objectives.git_commit != identity.git_commit
        or objectives.image_digest != identity.image_digest
        or objectives.approval_reference != identity.approval_reference
    ):
        raise ValueError("objective approval does not match the exact candidate identity")
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
            raise RuntimeError("PostgreSQL crossover could not read the database name")
        result = _run_crossover(pool, database=row["database"], config=config)
    finally:
        pool.close()
    return _report(config, identity, approval, result)


def _run_crossover(
    pool: PostgreSQLPool,
    *,
    database: str,
    config: PostgreSQLCrossoverConfig,
) -> _CrossoverResult:
    schema = f"dander_phase8_crossover_{uuid.uuid4().hex[:12]}"
    runtimes = {
        WriteTransport.COPY: _warehouse_runtime(pool, database, config=None),
        WriteTransport.DIRECT: _warehouse_runtime(pool, database, config=config),
    }
    samples: list[_Sample] = []
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    staging = 0
    try:
        for repetition in range(config.repetitions):
            for row_index, rows in enumerate(config.row_counts):
                order = (
                    (WriteTransport.COPY, WriteTransport.DIRECT)
                    if (repetition + row_index) % 2 == 0
                    else (WriteTransport.DIRECT, WriteTransport.COPY)
                )
                paired: dict[WriteTransport, _Sample] = {}
                for transport in order:
                    sample = _measure_sample(
                        pool,
                        runtimes[transport],
                        database=database,
                        schema=schema,
                        transport=transport,
                        rows=rows,
                        payload_bytes=config.payload_bytes,
                        repetition=repetition,
                    )
                    samples.append(sample)
                    paired[transport] = sample
                if (
                    paired[WriteTransport.COPY].canonical_sha256
                    != paired[WriteTransport.DIRECT].canonical_sha256
                ):
                    raise RuntimeError("PostgreSQL crossover transports produced unequal rows")
        staging = _temporary_staging_count(pool)
        if staging:
            raise RuntimeError("PostgreSQL crossover left temporary staging relations")
    finally:
        _drop_schema(pool, schema)
    cleanup = not _schema_exists(pool, schema)
    if not cleanup:
        raise RuntimeError("PostgreSQL crossover did not remove its disposable schema")
    medians = _median_durations(samples, config.row_counts)
    winning = tuple(
        rows
        for rows in config.row_counts
        if medians[WriteTransport.DIRECT][rows] <= medians[WriteTransport.COPY][rows]
    )
    recommended_rows = max(winning, default=0)
    recommended_bytes = (
        _workload_logical_bytes(recommended_rows, config.payload_bytes) if recommended_rows else 0
    )
    return _CrossoverResult(
        duration_ms=_elapsed_ms(started),
        peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
        samples=tuple(samples),
        medians=medians,
        recommended_direct_max_rows=recommended_rows,
        recommended_direct_max_logical_bytes=recommended_bytes,
        temporary_staging_relations=staging,
        cleanup_verified=cleanup,
    )


def _warehouse_runtime(
    pool: PostgreSQLPool,
    database: str,
    *,
    config: PostgreSQLCrossoverConfig | None,
) -> WarehouseRuntime:
    raw: dict[str, object] = {
        "provider": "postgresql",
        "database": database,
        "dsn_env": "DANDER_PHASE8_POSTGRES_DSN",
        "statement_timeout_ms": 300_000,
        "lock_timeout_ms": 30_000,
        "idle_transaction_timeout_ms": 60_000,
    }
    if config is not None:
        raw.update(
            direct_max_rows=config.row_counts[-1],
            direct_max_logical_bytes=config.direct_max_logical_bytes,
        )
    registry = default_provider_registry()
    parsed = registry.parse(ProviderKind.WAREHOUSE, raw)
    runtime = registry.build(ProviderKind.WAREHOUSE, parsed, context={"pool": pool})
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("PostgreSQL crossover received an invalid warehouse runtime")
    return runtime


def _measure_sample(
    pool: PostgreSQLPool,
    warehouse: WarehouseRuntime,
    *,
    database: str,
    schema: str,
    transport: WriteTransport,
    rows: int,
    payload_bytes: int,
    repetition: int,
) -> _Sample:
    relation = RelationRef(
        catalog=database,
        namespace=schema,
        name=f"{transport.value}_{rows}_{repetition}",
    )
    publication = warehouse.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=f"phase8_crossover_{transport.value}_{rows}",
            run_id=f"crossover-{transport.value}-{rows}-{repetition}",
            token=repetition + 1,
            authority_id="postgresql:phase8-local",
        ),
    )
    writer = warehouse.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=rows,
        schema_evolution=SchemaEvolution.STRICT,
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=_CROSSOVER_FIELDS,
        publication_fence=publication,
    )
    started = time.perf_counter()
    affected = writer.write(_records(rows, payload_bytes), target)
    duration_ms = _elapsed_ms(started)
    operations = writer.drain_telemetry()
    if affected != rows:
        raise RuntimeError("PostgreSQL crossover write affected an unexpected row count")
    if len(operations) != 1 or operations[0].transport is not transport:
        raise RuntimeError("PostgreSQL crossover did not observe its selected transport")
    return _Sample(
        transport=transport,
        rows=rows,
        repetition=repetition,
        duration_ms=duration_ms,
        canonical_sha256=_table_sha256(pool, schema=schema, table=relation.name),
    )


def _records(rows: int, payload_bytes: int) -> Iterator[dict[str, object]]:
    payload = "x" * payload_bytes
    for index in range(rows):
        yield {"id": f"{index:012d}", "payload": payload}


def _workload_logical_bytes(rows: int, payload_bytes: int) -> int:
    return sum(
        staging_logical_size(
            normalize_staging_record(record, _CROSSOVER_SCHEMA, row_index=row_index)
        )
        for row_index, record in enumerate(_records(rows, payload_bytes))
    )


def _table_sha256(pool: PostgreSQLPool, *, schema: str, table: str) -> str:
    digest = hashlib.sha256()
    with pool.connection() as connection:
        cursor = connection.execute(
            sql.SQL("SELECT id, payload FROM {} ORDER BY id").format(sql.Identifier(schema, table))
        )
        for row in cursor:
            digest.update(json.dumps(row, separators=(",", ":"), sort_keys=True).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _median_durations(
    samples: Sequence[_Sample],
    row_counts: Sequence[int],
) -> Mapping[WriteTransport, Mapping[int, int]]:
    return {
        transport: {
            rows: int(
                statistics.median(
                    sample.duration_ms
                    for sample in samples
                    if sample.transport is transport and sample.rows == rows
                )
            )
            for rows in row_counts
        }
        for transport in (WriteTransport.COPY, WriteTransport.DIRECT)
    }


def _report(
    config: PostgreSQLCrossoverConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _CrossoverResult,
) -> QualificationReport:
    total_rows = sum(config.row_counts) * config.repetitions * 2
    logical_bytes = (
        sum(_workload_logical_bytes(rows, config.payload_bytes) for rows in config.row_counts)
        * config.repetitions
        * 2
    )
    metrics: list[PerformanceMeasurement] = [
        _measured(
            "recommended_direct_max_logical_bytes",
            "bytes",
            result.recommended_direct_max_logical_bytes,
        ),
        _measured(
            "recommended_direct_max_rows",
            "rows",
            result.recommended_direct_max_rows,
        ),
        _measured(
            "temporary_staging_relations",
            "count",
            result.temporary_staging_relations,
        ),
    ]
    for rows in config.row_counts:
        copy_ms = result.medians[WriteTransport.COPY][rows]
        direct_ms = result.medians[WriteTransport.DIRECT][rows]
        metrics.extend(
            (
                _measured(f"copy_{rows}_median_duration_ms", "milliseconds", copy_ms),
                _measured(f"direct_{rows}_median_duration_ms", "milliseconds", direct_ms),
                _measured(
                    f"direct_to_copy_{rows}_duration_ratio",
                    "ratio",
                    (Decimal(direct_ms) / Decimal(max(copy_ms, 1))).quantize(Decimal("0.001")),
                ),
            )
        )
    performance = RunPerformance(
        rows=_measured("rows", "rows", total_rows),
        logical_bytes=_measured("logical_bytes", "bytes", logical_bytes),
        duration_ms=_measured("duration_ms", "milliseconds", result.duration_ms),
        throughput_rows_per_second=_measured(
            "throughput_rows_per_second",
            "rows_per_second",
            (Decimal(total_rows) * 1_000 / Decimal(max(result.duration_ms, 1))).quantize(
                Decimal("0.001")
            ),
        ),
        peak_rss_bytes=_measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
        retries=_measured("retries", "count", 0),
        queue_duration_ms=_measured("queue_duration_ms", "milliseconds", 0),
        load_duration_ms=_measured("load_duration_ms", "milliseconds", result.duration_ms),
        transform_duration_ms=_measured("transform_duration_ms", "milliseconds", 0),
        catalog_duration_ms=_measured("catalog_duration_ms", "milliseconds", 0),
        provider_metrics=tuple(sorted(metrics, key=lambda metric: metric.name)),
        costs=(CostAttribution("local", "postgresql", Decimal(0), estimated=False),),
    )
    return QualificationReport(
        context=QualificationContext(
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
        ),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.CROSSOVER,
            input_rows=total_rows,
            logical_input_bytes=logical_bytes,
            row_width_bytes=config.row_width_bytes,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="scd1_equal_transport_comparison",
            concurrency=1,
            batch_rows=config.row_counts[-1],
            batch_bytes=config.direct_max_logical_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=performance,
        objectives=tuple(
            ObjectiveResult(
                name,
                ObjectiveStatus.PASSED,
                f"phase8/postgresql/crossover/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _measured(name: str, unit: str, value: int | Decimal) -> PerformanceMeasurement:
    return PerformanceMeasurement.measured(name, unit, value)


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
    parser.add_argument("--objectives", type=Path, required=True)
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--row-count", action="append", type=int)
    parser.add_argument("--payload-bytes", type=int, default=128)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--direct-max-logical-bytes", type=int, default=1_024 * 1_024)
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    dsn = os.environ.get(arguments.dsn_env)
    if not dsn:
        raise SystemExit(f"PostgreSQL DSN is required in {arguments.dsn_env}")
    config = PostgreSQLCrossoverConfig(
        row_counts=tuple(arguments.row_count or (1, 10, 100, 1_000, 5_000)),
        payload_bytes=arguments.payload_bytes,
        repetitions=arguments.repetitions,
        direct_max_logical_bytes=arguments.direct_max_logical_bytes,
    )
    approval = load_approval(arguments.objectives, config=config)
    report = run_postgresql_crossover_qualification(
        dsn,
        config=config,
        identity=CandidateIdentity(
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
        ),
        approval=approval,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "python_version": platform.python_version(),
                "release_version": __version__,
                "status": report.status.value,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
