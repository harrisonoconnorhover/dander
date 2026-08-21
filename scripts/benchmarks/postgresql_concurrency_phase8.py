#!/usr/bin/env python3
"""Exact-candidate PostgreSQL concurrency Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from scripts.benchmarks import postgresql as benchmark

if TYPE_CHECKING:
    from collections.abc import Sequence

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]

_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.postgresql-concurrency/v1"
_OBJECTIVES = (
    "cleanup",
    "concurrent_pipeline_completion",
    "cost_ceiling",
    "stale_fence_rejection",
    "throughput_measurement",
)


class PostgreSQLConcurrencyQualificationError(RuntimeError):
    """Raised with a sanitized PostgreSQL concurrency summary."""


@dataclass(frozen=True, slots=True)
class PostgreSQLConcurrencyConfig:
    """The accepted four-pipeline PostgreSQL concurrency workload."""

    concurrent_pipelines: int = 4
    rows_per_pipeline: int = 5_000
    payload_bytes: int = 128
    batch_rows: int = 1_000
    memory_limit_mib: int = 256

    def __post_init__(self) -> None:
        for name in (
            "concurrent_pipelines",
            "rows_per_pipeline",
            "payload_bytes",
            "batch_rows",
            "memory_limit_mib",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.concurrent_pipelines < 2 or self.concurrent_pipelines > 32:
            raise ValueError("concurrent_pipelines must be between 2 and 32")
        if self.batch_rows > self.rows_per_pipeline:
            raise ValueError("batch_rows must not exceed rows_per_pipeline")
        if self.batch_rows * (self.payload_bytes + 24) > 256 * 1_024 * 1_024:
            raise ValueError("one batch must not exceed 256 MiB of logical input")

    @property
    def total_rows(self) -> int:
        return self.concurrent_pipelines * self.rows_per_pipeline

    @property
    def logical_input_bytes(self) -> int:
        return self.total_rows * (self.payload_bytes + 24)

    @property
    def memory_limit_bytes(self) -> int:
        return self.memory_limit_mib * 1_024 * 1_024

    def workload_payload(self) -> dict[str, object]:
        """Return the exact configuration covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.CONCURRENT_PIPELINES.value,
            "concurrent_pipelines": self.concurrent_pipelines,
            "rows_per_pipeline": self.rows_per_pipeline,
            "payload_bytes": self.payload_bytes,
            "batch_rows": self.batch_rows,
            "memory_limit_mib": self.memory_limit_mib,
        }

    def configuration_sha256(self) -> str:
        encoded = json.dumps(
            self.workload_payload(), separators=(",", ":"), sort_keys=True
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
class _ConcurrencyResult:
    duration_ms: int
    peak_rss_bytes: int
    total_rows: int
    logical_input_bytes: int
    independent_targets: int
    stale_publications_rejected: int
    temporary_staging_relations: int
    tls_verified: bool
    cleanup_verified: bool


def _load_approval(
    path: Path,
    *,
    config: PostgreSQLConcurrencyConfig,
    identity: CandidateIdentity,
) -> _Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective approval schema is incompatible")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective approval workload does not match the requested run")
    execution = _mapping(
        _mapping(payload.get("configuration"), "configuration").get("execution"),
        "execution configuration",
    )
    if execution.get("harness_sha256") != _file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    dependency = Path(benchmark.__file__)
    if execution.get("benchmark_dependency_sha256") != _file_sha256(dependency):
        raise ValueError("objective approval does not match the protected benchmark dependency")
    if execution.get("manual_candidate_executions") != 1:
        raise ValueError("objective approval must allow exactly one candidate execution")
    if execution.get("automatic_candidate_retry") is not False:
        raise ValueError("objective approval must disable automatic candidate retry")
    if execution.get("provider_operation_retries") != 0:
        raise ValueError("objective approval must disable provider-operation retries")

    raw_objectives = _mapping(payload.get("approved_objectives"), "approved objectives")
    objectives = ApprovedObjectiveSet(
        names=tuple(cast("list[str]", raw_objectives.get("names"))),
        benchmark_class=BenchmarkClass(str(raw_objectives.get("benchmark_class"))),
        profile_id=str(raw_objectives.get("profile_id")),
        release_version=str(raw_objectives.get("release_version")),
        git_commit=str(raw_objectives.get("git_commit")),
        image_digest=str(raw_objectives.get("image_digest")),
        configuration_sha256=str(raw_objectives.get("configuration_sha256")),
        approval_reference=str(raw_objectives.get("approval_reference")),
    )
    if objectives.names != _OBJECTIVES:
        raise ValueError("objective approval names do not match concurrency qualification")
    if objectives.benchmark_class is not BenchmarkClass.CONCURRENT_PIPELINES:
        raise ValueError("objective approval benchmark class is not concurrent pipelines")
    if (
        objectives.release_version != identity.release_version
        or objectives.git_commit != identity.git_commit
        or objectives.image_digest != identity.image_digest
        or objectives.approval_reference != identity.approval_reference
        or objectives.configuration_sha256 != config.configuration_sha256()
    ):
        raise ValueError("objective approval does not match the exact candidate")
    raw_cost = _mapping(payload.get("cost_ceiling"), "cost ceiling")
    cost_ceiling = ApprovedCostCeiling(
        amount_usd=Decimal(str(raw_cost.get("amount_usd"))),
        approval_reference=str(raw_cost.get("approval_reference")),
    )
    if cost_ceiling.amount_usd != Decimal("0.50"):
        raise ValueError("cost ceiling must match the established GKE per-cell ceiling")
    if cost_ceiling.approval_reference != identity.approval_reference:
        raise ValueError("cost ceiling approval does not match the candidate approval")
    return _Approval(objectives=objectives, cost_ceiling=cost_ceiling)


def _run_concurrency(dsn: str, config: PostgreSQLConcurrencyConfig) -> _ConcurrencyResult:
    if not dsn:
        raise ValueError("PostgreSQL concurrency qualification requires a non-empty DSN")
    pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=max(5, config.concurrent_pipelines + 1),
            timeout=10,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=10)
    suffix = uuid.uuid4().hex[:12]
    warehouse_schema = f"dander_concurrency_{suffix}"
    state_schema = f"dander_concurrency_state_{suffix}"
    result: _ConcurrencyResult | None = None
    failure: Exception | None = None
    cleanup = False
    try:
        with pool.connection() as connection:
            metadata = connection.execute(
                "SELECT current_database() AS database, current_setting('ssl') AS ssl"
            ).fetchone()
        if metadata is None or str(metadata["ssl"]).lower() != "on":
            raise PostgreSQLConcurrencyQualificationError(
                "PostgreSQL concurrency qualification requires TLS"
            )
        database = cast("str", metadata["database"])
        warehouse = benchmark._warehouse_runtime(pool, database)
        state = benchmark._state_runtime(pool, state_schema)
        state.migrator.migrate()
        peak_before = benchmark._peak_rss_bytes()
        rows, concurrent_duration = benchmark._write_concurrently(
            warehouse,
            state,
            database=database,
            schema=warehouse_schema,
            pipelines=config.concurrent_pipelines,
            rows_per_pipeline=config.rows_per_pipeline,
            batch_rows=config.batch_rows,
        )
        _require_exact_targets(pool, warehouse_schema, config)
        stale_rejected = benchmark._reject_stale_publication(
            warehouse,
            state,
            database=database,
            schema=warehouse_schema,
        )
        staging = benchmark._temporary_staging_count(pool)
        if staging:
            raise PostgreSQLConcurrencyQualificationError(
                "PostgreSQL concurrency qualification left staging relations"
            )
        result = _ConcurrencyResult(
            duration_ms=max(round(concurrent_duration * 1_000), 1),
            peak_rss_bytes=max(peak_before, benchmark._peak_rss_bytes()),
            total_rows=rows,
            logical_input_bytes=config.logical_input_bytes,
            independent_targets=config.concurrent_pipelines,
            stale_publications_rejected=int(stale_rejected),
            temporary_staging_relations=staging,
            tls_verified=True,
            cleanup_verified=False,
        )
    except Exception as error:
        failure = error
    finally:
        try:
            with pool.connection() as connection:
                for schema_name in (warehouse_schema, state_schema):
                    connection.execute(
                        sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                            sql.Identifier(schema_name)
                        )
                    )
                cleanup = _schemas_absent(connection, warehouse_schema, state_schema)
        finally:
            pool.close()
    if not cleanup:
        raise PostgreSQLConcurrencyQualificationError(
            "PostgreSQL concurrency schema cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, PostgreSQLConcurrencyQualificationError):
            raise failure
        raise PostgreSQLConcurrencyQualificationError(
            "PostgreSQL concurrency qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return _ConcurrencyResult(
        duration_ms=result.duration_ms,
        peak_rss_bytes=result.peak_rss_bytes,
        total_rows=result.total_rows,
        logical_input_bytes=result.logical_input_bytes,
        independent_targets=result.independent_targets,
        stale_publications_rejected=result.stale_publications_rejected,
        temporary_staging_relations=result.temporary_staging_relations,
        tls_verified=result.tls_verified,
        cleanup_verified=True,
    )


def _require_exact_targets(
    pool: PostgreSQLPool,
    schema: str,
    config: PostgreSQLConcurrencyConfig,
) -> None:
    with pool.connection() as connection:
        for index in range(config.concurrent_pipelines):
            row = connection.execute(
                sql.SQL("SELECT COUNT(*) AS count FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(f"concurrent_{index}")
                )
            ).fetchone()
            if row is None or int(row["count"]) != config.rows_per_pipeline:
                raise PostgreSQLConcurrencyQualificationError(
                    "PostgreSQL concurrency readback differs from the approved workload"
                )


def _schemas_absent(connection: Connection[PostgreSQLRow], *schema_names: str) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM information_schema.schemata WHERE schema_name = ANY(%s)",
        (list(schema_names),),
    ).fetchone()
    return row is not None and int(row["count"]) == 0


def _report(
    config: PostgreSQLConcurrencyConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _ConcurrencyResult,
    *,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    if result.total_rows != config.total_rows:
        raise PostgreSQLConcurrencyQualificationError(
            "concurrency result differs from the approved row count"
        )
    if result.independent_targets != config.concurrent_pipelines:
        raise PostgreSQLConcurrencyQualificationError(
            "concurrency result differs from the approved pipeline count"
        )
    if result.stale_publications_rejected != 1:
        raise PostgreSQLConcurrencyQualificationError(
            "PostgreSQL concurrency did not reject exactly one stale publication"
        )
    if result.temporary_staging_relations or not result.tls_verified or not result.cleanup_verified:
        raise PostgreSQLConcurrencyQualificationError(
            "PostgreSQL concurrency cleanup or TLS verification is incomplete"
        )
    if provider_cost_usd is not None and provider_cost_usd > approval.cost_ceiling.amount_usd:
        raise PostgreSQLConcurrencyQualificationError(
            "provider-metered cost exceeds its approved ceiling"
        )
    observed_cost = (
        provider_cost_usd if provider_cost_usd is not None else approval.cost_ceiling.amount_usd
    )
    cost_status = (
        ObjectiveStatus.PASSED if provider_cost_usd is not None else ObjectiveStatus.NOT_EVALUATED
    )
    status = (
        QualificationStatus.PASSED
        if provider_cost_usd is not None
        else QualificationStatus.NOT_EVALUATED
    )
    measured = PerformanceMeasurement.measured
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
            benchmark_class=BenchmarkClass.CONCURRENT_PIPELINES,
            input_rows=result.total_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=config.payload_bytes + 24,
            schema_depth=1,
            source_rate_limit="unlimited_in_cluster_generator",
            transform_complexity="independent_targets_and_stale_fence",
            concurrency=config.concurrent_pipelines,
            batch_rows=config.batch_rows,
            batch_bytes=config.batch_rows * (config.payload_bytes + 24),
            memory_limit_bytes=config.memory_limit_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.total_rows),
            logical_bytes=measured("logical_bytes", "bytes", result.logical_input_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                (
                    Decimal(result.total_rows)
                    * Decimal(1_000)
                    / Decimal(max(result.duration_ms, 1))
                ).quantize(Decimal("0.001")),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured("load_duration_ms", "milliseconds", result.duration_ms),
            transform_duration_ms=measured("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measured("independent_targets", "count", result.independent_targets),
                measured("kubernetes_job_retries", "count", 0),
                measured("provider_operation_retries", "count", 0),
                measured(
                    "stale_publications_rejected",
                    "count",
                    result.stale_publications_rejected,
                ),
                measured(
                    "temporary_staging_relations",
                    "count",
                    result.temporary_staging_relations,
                ),
                measured("tls_verified", "count", int(result.tls_verified)),
            ),
            costs=(
                CostAttribution(
                    provider="gcp",
                    service="gke_standard_zonal",
                    amount=observed_cost,
                    estimated=provider_cost_usd is None,
                ),
            ),
        ),
        objectives=tuple(
            ObjectiveResult(
                name=name,
                status=cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
                evidence_reference=f"phase8/gcp/gke/concurrency/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=status,
    )


def _container_memory_limit_bytes() -> int | None:
    for path in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _require_container_memory_limit(config: PostgreSQLConcurrencyConfig) -> None:
    if _container_memory_limit_bytes() != config.memory_limit_bytes:
        raise PostgreSQLConcurrencyQualificationError(
            "container memory limit does not match the approved concurrency objective"
        )


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"objective approval {label} is incomplete")
    return cast("dict[str, object]", value)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dsn-env", default="DANDER_BENCHMARK_POSTGRES_DSN")
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--benchmark-date", type=date.fromisoformat, required=True)
    parser.add_argument("--provider-job-id", action="append", required=True)
    parser.add_argument("--service-shape", action="append", required=True)
    parser.add_argument("--launcher", default="kubernetes")
    parser.add_argument("--region", action="append", required=True)
    parser.add_argument("--secret-provider", default="kubernetes")
    parser.add_argument("--provider-cost-usd", type=Decimal)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.release_version != __version__:
            raise ValueError("installed release does not match objective candidate")
        config = PostgreSQLConcurrencyConfig()
        _require_container_memory_limit(config)
        identity = CandidateIdentity(
            release_version=arguments.release_version,
            git_commit=arguments.git_commit,
            image_digest=arguments.image_digest,
            approval_reference=arguments.approval_reference,
            benchmark_date=arguments.benchmark_date,
            launcher=arguments.launcher,
            regions=tuple(sorted(set(arguments.region))),
            secret_provider=arguments.secret_provider,
            provider_job_ids=tuple(sorted(set(arguments.provider_job_id))),
            service_shapes=tuple(sorted(set(arguments.service_shape))),
        )
        approval = _load_approval(arguments.approval_manifest, config=config, identity=identity)
        dsn = os.environ.get(arguments.dsn_env)
        if not dsn:
            raise ValueError(f"PostgreSQL DSN is required in {arguments.dsn_env}")
        result = _run_concurrency(dsn, config)
        report = _report(
            config,
            identity,
            approval,
            result,
            provider_cost_usd=arguments.provider_cost_usd,
        )
    except (ValueError, PostgreSQLConcurrencyQualificationError):
        print(
            json.dumps(
                {
                    "schema": "io.dander.qualification.failure/v1",
                    "provider": "postgresql",
                    "benchmark_class": BenchmarkClass.CONCURRENT_PIPELINES.value,
                    "status": QualificationStatus.FAILED.value,
                    "summary": (
                        "PostgreSQL concurrency qualification failed; inspect provider logs "
                        "and verify cleanup before any bounded rerun."
                    ),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 1
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
