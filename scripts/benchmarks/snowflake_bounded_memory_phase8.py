#!/usr/bin/env python3
"""Exact-candidate Snowflake bounded-memory Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dander import __version__
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
from scripts.benchmarks import snowflake_bulk_phase8 as bulk

if TYPE_CHECKING:
    from collections.abc import Sequence


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.snowflake-bounded-memory/v1"
_OBJECTIVES = (
    "bounded_input_ratio",
    "cleanup",
    "cost_ceiling",
    "peak_rss",
    "throughput_measurement",
)
_AUTHORITY_ID = "snowflake:phase8-bounded-memory"


class SnowflakeBoundedMemoryQualificationError(RuntimeError):
    """Raised with a sanitized Snowflake bounded-memory summary."""


@dataclass(frozen=True, slots=True)
class SnowflakeBoundedMemoryConfig:
    """Private provider coordinates and the accepted bounded-memory workload."""

    account: str
    user: str
    database: str
    warehouse: str
    role: str | None = None
    auth_method: str = "oauth"
    token_env: str = "DANDER_SNOWFLAKE_OAUTH_TOKEN"
    private_key_file_env: str = "DANDER_SNOWFLAKE_PRIVATE_KEY_FILE"
    private_key_password_env: str | None = None
    rows: int = 2_600_000
    payload_bytes: int = 1_024
    copy_part_rows: int = 10_000
    copy_part_logical_bytes: int = 16 * 1_024 * 1_024
    memory_limit_mib: int = 256

    def __post_init__(self) -> None:
        if self.auth_method not in {"key_pair", "oauth"}:
            raise ValueError("auth_method must be key_pair or oauth")
        for name in (
            "rows",
            "payload_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
            "memory_limit_mib",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.copy_part_rows > self.rows:
            raise ValueError("copy_part_rows must not exceed rows")
        if self.copy_part_rows * (self.payload_bytes + 24) > self.copy_part_logical_bytes:
            raise ValueError("one COPY part exceeds copy_part_logical_bytes")
        default_provider_registry().parse(
            ProviderKind.WAREHOUSE,
            bulk._provider_values(
                cast("bulk.SnowflakeScaleConfig", self),
                schema_name="DANDER_PHASE8_BOUNDED_MEMORY_CHECK",
            ),
        )

    @property
    def memory_limit_bytes(self) -> int:
        return self.memory_limit_mib * 1_024 * 1_024

    @property
    def logical_input_bytes(self) -> int:
        return self.rows * (self.payload_bytes + 24)

    def workload_payload(self) -> dict[str, object]:
        """Return the exact configuration covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.BOUNDED_MEMORY.value,
            "rows": self.rows,
            "payload_bytes": self.payload_bytes,
            "copy_part_rows": self.copy_part_rows,
            "copy_part_logical_bytes": self.copy_part_logical_bytes,
            "memory_limit_mib": self.memory_limit_mib,
            "minimum_input_to_memory_ratio": 10,
            "maximum_peak_rss_fraction": "0.80",
        }

    def configuration_sha256(self) -> str:
        encoded = json.dumps(
            self.workload_payload(), separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _BoundedMemoryResult:
    duration_ms: int
    peak_rss_bytes: int
    rows: int
    logical_input_bytes: int
    copy_operations: int
    query_ids: tuple[str, ...]
    staging_tables: int
    staging_stages: int
    cleanup_verified: bool


def _load_approval(
    path: Path,
    *,
    config: SnowflakeBoundedMemoryConfig,
    identity: bulk.CandidateIdentity,
) -> bulk._Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective approval schema is incompatible")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective approval workload does not match the requested run")
    configuration = _mapping(payload.get("configuration"), "configuration")
    snowflake = _mapping(configuration.get("snowflake"), "Snowflake configuration")
    execution = _mapping(configuration.get("execution"), "execution configuration")
    if execution.get("harness_sha256") != _file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    dependency = Path(bulk.__file__)
    if execution.get("bulk_harness_sha256") != _file_sha256(dependency):
        raise ValueError("objective approval does not match the protected Snowflake dependency")
    if execution.get("manual_candidate_executions") != 1:
        raise ValueError("objective approval must allow exactly one candidate execution")
    if execution.get("automatic_candidate_retry") is not False:
        raise ValueError("objective approval must disable automatic candidate retry")
    if execution.get("provider_operation_retries") != 0:
        raise ValueError("objective approval must disable provider-operation retries")

    objectives_payload = _mapping(payload.get("approved_objectives"), "approved objectives")
    objectives = ApprovedObjectiveSet(
        names=tuple(cast("list[str]", objectives_payload.get("names"))),
        benchmark_class=BenchmarkClass(str(objectives_payload.get("benchmark_class"))),
        profile_id=str(objectives_payload.get("profile_id")),
        release_version=str(objectives_payload.get("release_version")),
        git_commit=str(objectives_payload.get("git_commit")),
        image_digest=str(objectives_payload.get("image_digest")),
        configuration_sha256=str(objectives_payload.get("configuration_sha256")),
        approval_reference=str(objectives_payload.get("approval_reference")),
    )
    if objectives.names != _OBJECTIVES:
        raise ValueError("objective approval names do not match bounded-memory qualification")
    if objectives.benchmark_class is not BenchmarkClass.BOUNDED_MEMORY:
        raise ValueError("objective approval benchmark class is not bounded memory")
    if (
        objectives.release_version != identity.release_version
        or objectives.git_commit != identity.git_commit
        or objectives.image_digest != identity.image_digest
        or objectives.approval_reference != identity.approval_reference
        or objectives.configuration_sha256 != config.configuration_sha256()
    ):
        raise ValueError("objective approval does not match the exact candidate")

    cost_payload = _mapping(payload.get("cost_ceiling"), "cost ceiling")
    cost_ceiling = ApprovedCostCeiling(
        amount_usd=Decimal(str(cost_payload.get("amount_usd"))),
        approval_reference=str(cost_payload.get("approval_reference")),
    )
    if cost_ceiling.amount_usd != Decimal("0.50"):
        raise ValueError("cost ceiling must match the established Snowflake per-cell ceiling")
    if cost_ceiling.approval_reference != identity.approval_reference:
        raise ValueError("cost ceiling approval does not match the candidate approval")

    coordinates = {
        "account_sha256": bulk._identifier_sha256(config.account),
        "operator_user_sha256": bulk._identifier_sha256(config.user),
        "database": config.database,
        "warehouse": config.warehouse,
        "role": config.role or "",
    }
    if any(snowflake.get(name) != value for name, value in coordinates.items()):
        raise ValueError("objective approval does not match the private Snowflake coordinates")
    return bulk._Approval(
        objectives=objectives,
        cost_ceiling=cost_ceiling,
        account_sha256=coordinates["account_sha256"],
        operator_user_sha256=coordinates["operator_user_sha256"],
        database=coordinates["database"],
        warehouse=coordinates["warehouse"],
        role=coordinates["role"],
    )


def _run_bounded_memory(
    config: SnowflakeBoundedMemoryConfig,
    *,
    identity: bulk.CandidateIdentity,
    approval: bulk._Approval,
) -> _BoundedMemoryResult:
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    bulk._require_identity_match(identity, approval)
    bulk._require_provider_match(cast("bulk.SnowflakeScaleConfig", config), approval)
    schema_name = f"DANDER_P8_BOUNDED_{uuid.uuid4().hex[:12].upper()}"
    runtime = bulk._warehouse_runtime(
        cast("bulk.SnowflakeScaleConfig", config), schema_name=schema_name
    )
    started = time.perf_counter()
    peak_before = bulk._peak_rss_bytes()
    result: _BoundedMemoryResult | None = None
    failure: Exception | None = None
    try:
        rows, _write_duration_ms, operations = bulk._write_table(
            runtime,
            database=config.database,
            schema=schema_name,
            table="bounded_records",
            pipeline_id="phase8_snowflake_bounded_memory",
            rows=config.rows,
            payload_bytes=config.payload_bytes,
            copy_part_rows=config.copy_part_rows,
            authority_id=_AUTHORITY_ID,
        )
        bulk._require_table_shape(
            runtime,
            database=config.database,
            schema=schema_name,
            table="bounded_records",
            rows=config.rows,
            payload_bytes=config.payload_bytes,
        )
        staging_tables, staging_stages = bulk._staging_residue(
            runtime, config.database, schema_name
        )
        if staging_tables or staging_stages:
            raise SnowflakeBoundedMemoryQualificationError(
                "Snowflake bounded-memory qualification left staging objects"
            )
        result = _BoundedMemoryResult(
            duration_ms=bulk._elapsed_ms(started),
            peak_rss_bytes=max(peak_before, bulk._peak_rss_bytes()),
            rows=rows,
            logical_input_bytes=config.logical_input_bytes,
            copy_operations=len(operations),
            query_ids=bulk._operation_query_ids(operations),
            staging_tables=staging_tables,
            staging_stages=staging_stages,
            cleanup_verified=False,
        )
    except Exception as error:
        failure = error
    finally:
        try:
            bulk._drop_schema(runtime, config.database, schema_name)
        except Exception as error:
            raise SnowflakeBoundedMemoryQualificationError(
                "Snowflake bounded-memory schema cleanup failed"
            ) from error
    cleanup = not bulk._schema_exists(runtime, config.database, schema_name)
    if not cleanup:
        raise SnowflakeBoundedMemoryQualificationError(
            "Snowflake bounded-memory schema cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, SnowflakeBoundedMemoryQualificationError):
            raise failure
        raise SnowflakeBoundedMemoryQualificationError(
            "Snowflake bounded-memory qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return replace(result, cleanup_verified=True)


def _report(
    config: SnowflakeBoundedMemoryConfig,
    identity: bulk.CandidateIdentity,
    approval: bulk._Approval,
    result: _BoundedMemoryResult,
    *,
    provider_cost_usd: Decimal | None,
) -> QualificationReport:
    if result.rows != config.rows or result.logical_input_bytes != config.logical_input_bytes:
        raise SnowflakeBoundedMemoryQualificationError(
            "bounded-memory result differs from approval"
        )
    if result.logical_input_bytes < config.memory_limit_bytes * 10:
        raise SnowflakeBoundedMemoryQualificationError(
            "logical input is less than ten times the container memory limit"
        )
    if result.peak_rss_bytes * 5 > config.memory_limit_bytes * 4:
        raise SnowflakeBoundedMemoryQualificationError(
            "peak RSS exceeds eighty percent of the container memory limit"
        )
    if result.copy_operations <= 0:
        raise SnowflakeBoundedMemoryQualificationError(
            "Snowflake bounded-memory run recorded no COPY operations"
        )
    if result.staging_tables or result.staging_stages or not result.cleanup_verified:
        raise SnowflakeBoundedMemoryQualificationError(
            "Snowflake bounded-memory cleanup is incomplete"
        )
    if provider_cost_usd is not None and provider_cost_usd > approval.cost_ceiling.amount_usd:
        raise SnowflakeBoundedMemoryQualificationError(
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
            benchmark_class=BenchmarkClass.BOUNDED_MEMORY,
            input_rows=result.rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=config.payload_bytes + 24,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="none",
            concurrency=1,
            batch_rows=config.copy_part_rows,
            batch_bytes=config.copy_part_rows * (config.payload_bytes + 24),
            memory_limit_bytes=config.memory_limit_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.rows),
            logical_bytes=measured("logical_bytes", "bytes", result.logical_input_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                bulk._throughput(result.rows, result.duration_ms),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured("load_duration_ms", "milliseconds", result.duration_ms),
            transform_duration_ms=measured("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measured("copy_operations", "count", result.copy_operations),
                measured("memory_limit_bytes", "bytes", config.memory_limit_bytes),
                measured("snowflake_provider_operation_retries", "count", 0),
                measured("staging_stages", "count", result.staging_stages),
                measured("staging_tables", "count", result.staging_tables),
            ),
            costs=(
                CostAttribution(
                    provider="snowflake",
                    service="virtual_warehouse",
                    amount=observed_cost,
                    estimated=provider_cost_usd is None,
                ),
            ),
        ),
        objectives=tuple(
            ObjectiveResult(
                name=name,
                status=cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
                evidence_reference=f"phase8/snowflake/bounded-memory/{name}",
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


def _require_container_memory_limit(config: SnowflakeBoundedMemoryConfig) -> None:
    if _container_memory_limit_bytes() != config.memory_limit_bytes:
        raise SnowflakeBoundedMemoryQualificationError(
            "container memory limit does not match the approved bounded-memory objective"
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
    parser.add_argument("--account", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--auth-method", choices=("key_pair", "oauth"), default="oauth")
    parser.add_argument("--token-env", default="DANDER_SNOWFLAKE_OAUTH_TOKEN")
    parser.add_argument("--private-key-file-env", default="DANDER_SNOWFLAKE_PRIVATE_KEY_FILE")
    parser.add_argument("--private-key-password-env")
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--benchmark-date", type=date.fromisoformat, required=True)
    parser.add_argument("--provider-job-id", action="append", required=True)
    parser.add_argument("--service-shape", action="append", required=True)
    parser.add_argument("--launcher", default="docker_local")
    parser.add_argument("--region", action="append")
    parser.add_argument("--secret-provider", default="environment")
    parser.add_argument("--provider-cost-usd", type=Decimal)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        config = SnowflakeBoundedMemoryConfig(
            account=arguments.account,
            user=arguments.user,
            database=arguments.database,
            warehouse=arguments.warehouse,
            role=arguments.role,
            auth_method=arguments.auth_method,
            token_env=arguments.token_env,
            private_key_file_env=arguments.private_key_file_env,
            private_key_password_env=arguments.private_key_password_env,
        )
        _require_container_memory_limit(config)
        identity = bulk.CandidateIdentity(
            release_version=arguments.release_version,
            git_commit=arguments.git_commit,
            image_digest=arguments.image_digest,
            approval_reference=arguments.approval_reference,
            benchmark_date=arguments.benchmark_date,
            launcher=arguments.launcher,
            regions=tuple(sorted(set(arguments.region or ("local",)))),
            secret_provider=arguments.secret_provider,
            provider_job_ids=tuple(sorted(set(arguments.provider_job_id))),
            service_shapes=tuple(sorted(set(arguments.service_shape))),
        )
        approval = _load_approval(arguments.approval_manifest, config=config, identity=identity)
        result = _run_bounded_memory(config, identity=identity, approval=approval)
        report = _report(
            config,
            identity,
            approval,
            result,
            provider_cost_usd=arguments.provider_cost_usd,
        )
    except (ValueError, SnowflakeBoundedMemoryQualificationError):
        print(
            json.dumps(
                {
                    "schema": "io.dander.qualification.failure/v1",
                    "provider": "snowflake",
                    "benchmark_class": BenchmarkClass.BOUNDED_MEMORY.value,
                    "status": QualificationStatus.FAILED.value,
                    "summary": (
                        "Snowflake bounded-memory qualification failed; inspect provider logs "
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
    print(
        json.dumps(
            {
                "benchmark_class": BenchmarkClass.BOUNDED_MEMORY.value,
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
