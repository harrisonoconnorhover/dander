#!/usr/bin/env python3
"""Exact-candidate Redshift bounded-memory Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

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
from scripts.benchmarks import redshift as shared
from scripts.benchmarks import redshift_bulk_phase8 as bulk

if TYPE_CHECKING:
    from collections.abc import Mapping


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.redshift-bounded-memory/v1"
_OBJECTIVES = (
    "bounded_input_ratio",
    "cleanup",
    "cost_ceiling",
    "peak_rss",
    "throughput_measurement",
)


class RedshiftBoundedMemoryQualificationError(RuntimeError):
    """Raised with a credential-free Redshift bounded-memory summary."""


@dataclass(frozen=True, slots=True)
class RedshiftBoundedMemoryConfig:
    """Non-secret Redshift coordinates and the accepted bounded-memory workload."""

    account_id: str
    host: str
    database: str
    region: str
    workgroup_name: str
    copy_role_arn: str
    staging_bucket: str
    staging_prefix: str
    port: int = 5439
    connect_timeout_seconds: int = 300
    statement_timeout_ms: int = 900_000
    rows: int = 2_600_000
    payload_bytes: int = 1_024
    copy_part_rows: int = 10_000
    copy_part_logical_bytes: int = 16 * 1_024 * 1_024
    memory_limit_mib: int = 256
    cost_observation_delay_seconds: int = 70
    on_demand_rate_usd_per_rpu_hour: Decimal = Decimal("0.375")

    def __post_init__(self) -> None:
        for name in (
            "port",
            "connect_timeout_seconds",
            "statement_timeout_ms",
            "rows",
            "payload_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
            "memory_limit_mib",
            "cost_observation_delay_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.copy_part_rows > self.rows:
            raise ValueError("copy_part_rows must not exceed rows")
        if self.copy_part_rows * (self.payload_bytes + 24) > self.copy_part_logical_bytes:
            raise ValueError("one COPY part exceeds copy_part_logical_bytes")
        if self.cost_observation_delay_seconds < 60:
            raise ValueError("cost observation must wait for one complete provider interval")
        if self.logical_input_bytes < self.memory_limit_bytes * 10:
            raise ValueError("logical input must be at least ten times the memory limit")
        _bulk_config(self)

    @property
    def logical_input_bytes(self) -> int:
        return self.rows * (self.payload_bytes + 24)

    @property
    def memory_limit_bytes(self) -> int:
        return self.memory_limit_mib * 1_024 * 1_024

    def workload_payload(self) -> dict[str, object]:
        """Return the exact workload covered by objective approval."""
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
class _Approval:
    objectives: ApprovedObjectiveSet
    cost_ceiling: ApprovedCostCeiling


@dataclass(frozen=True, slots=True)
class _BoundedResult:
    duration_ms: int
    peak_rss_bytes: int
    rows: int
    logical_input_bytes: int
    copy_operations: int
    query_ids: tuple[str, ...]
    queue_duration_ms: int
    load_duration_ms: int
    bytes_processed: int
    spill_bytes: int
    charged_seconds: Decimal
    compute_seconds: Decimal
    maximum_compute_capacity_rpu: Decimal
    provider_cost_usd: Decimal
    staging_tables: int
    staging_objects: int
    cleanup_verified: bool


def _load_approval(
    path: Path,
    *,
    config: RedshiftBoundedMemoryConfig,
    identity: bulk.CandidateIdentity,
) -> _Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective approval schema is incompatible")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective approval workload does not match the requested run")
    configuration = _mapping(payload.get("configuration"), "configuration")
    provider = _mapping(configuration.get("redshift"), "Redshift configuration")
    expected_provider = {
        "account_id": config.account_id,
        "region": config.region,
        "workgroup_name": config.workgroup_name,
        "copy_role_arn": config.copy_role_arn,
        "staging_bucket": config.staging_bucket,
        "staging_prefix": config.staging_prefix,
        "on_demand_rate_usd_per_rpu_hour": str(config.on_demand_rate_usd_per_rpu_hour),
    }
    if provider != expected_provider:
        raise ValueError("objective approval does not match the Redshift data plane")
    execution = _mapping(configuration.get("execution"), "execution configuration")
    if execution.get("harness_sha256") != _file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    if execution.get("bulk_harness_sha256") != _file_sha256(Path(bulk.__file__)):
        raise ValueError("objective approval does not match the reused Redshift bulk harness")
    if execution.get("shared_harness_sha256") != _file_sha256(Path(shared.__file__)):
        raise ValueError("objective approval does not match the shared Redshift harness")
    if execution.get("manual_candidate_executions") != 1:
        raise ValueError("objective approval must allow exactly one candidate execution")
    if execution.get("automatic_candidate_retry") is not False:
        raise ValueError("objective approval must disable automatic candidate retry")
    if execution.get("provider_operation_retries") != 0:
        raise ValueError("objective approval must disable provider-operation retries")
    if execution.get("cost_observation_delay_seconds") != config.cost_observation_delay_seconds:
        raise ValueError("objective approval changed the provider cost observation")
    fargate = _mapping(configuration.get("fargate_harness"), "Fargate harness configuration")
    expected_fargate = {
        "task_cpu_units": 2_048,
        "task_memory_mib": 4_096,
        "candidate_container_memory_mib": config.memory_limit_mib,
        "task_timeout_seconds": 1_500,
        "cluster_executions": 1,
        "state_machine_executions": 1,
        "state_machine_retry_states": 0,
        "ecs_task_retries": 0,
        "container_restarts": 0,
        "automatic_retry": False,
    }
    if any(fargate.get(name) != value for name, value in expected_fargate.items()):
        raise ValueError("objective approval must enforce the bounded zero-retry Fargate shape")
    objectives_payload = _mapping(payload.get("approved_objectives"), "approved objectives")
    names = objectives_payload.get("names")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("objective approval names are malformed")
    objectives = ApprovedObjectiveSet(
        names=tuple(cast("list[str]", names)),
        benchmark_class=BenchmarkClass(str(objectives_payload.get("benchmark_class"))),
        profile_id=str(objectives_payload.get("profile_id")),
        release_version=str(objectives_payload.get("release_version")),
        git_commit=str(objectives_payload.get("git_commit")),
        image_digest=str(objectives_payload.get("image_digest")),
        configuration_sha256=str(objectives_payload.get("configuration_sha256")),
        approval_reference=str(objectives_payload.get("approval_reference")),
    )
    if objectives.names != _OBJECTIVES:
        raise ValueError("objective approval names do not match Redshift bounded memory")
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
        raise ValueError("cost ceiling must match the Redshift per-cell ceiling")
    if cost_ceiling.approval_reference != identity.approval_reference:
        raise ValueError("cost ceiling approval does not match the candidate approval")
    return _Approval(objectives=objectives, cost_ceiling=cost_ceiling)


def run_phase8_redshift_bounded_memory(
    config: RedshiftBoundedMemoryConfig,
    *,
    identity: bulk.CandidateIdentity,
    approval: _Approval,
) -> QualificationReport:
    """Run the accepted bounded-memory class in one disposable Redshift schema."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    bulk._require_no_provider_retries()  # noqa: SLF001
    _require_container_memory_limit(config)
    if approval.objectives.configuration_sha256 != config.configuration_sha256():
        raise ValueError("objective approval does not match the requested workload")
    suffix = uuid.uuid4().hex[:12]
    schema_name = f"dander_p8_bounded_{suffix}"
    staging_prefix = f"{config.staging_prefix}/bounded-memory/{suffix}"
    bulk_config = _bulk_config(config)
    runtime = bulk._warehouse_runtime(  # noqa: SLF001
        bulk_config, schema_name=schema_name, staging_prefix=staging_prefix
    )
    started = time.perf_counter()
    peak_before = bulk._peak_rss_bytes()  # noqa: SLF001
    result: _BoundedResult | None = None
    failure: Exception | None = None
    try:
        rows, _write_ms, operations = bulk._write_table(  # noqa: SLF001
            runtime,
            config=bulk_config,
            schema=schema_name,
            table="bounded_records",
            pipeline_id="phase8_redshift_bounded_memory",
            rows=config.rows,
            payload_bytes=config.payload_bytes,
        )
        workload_duration_ms = bulk._elapsed_ms(started)  # noqa: SLF001
        bulk._require_table_shape(  # noqa: SLF001
            runtime,
            config=bulk_config,
            schema=schema_name,
            table="bounded_records",
            rows=config.rows,
            payload_bytes=config.payload_bytes,
        )
        staging_tables = bulk._staging_table_count(runtime, schema_name)  # noqa: SLF001
        staging_objects = shared._prefix_object_count(  # noqa: SLF001
            bulk._shared_config(bulk_config),
            staging_prefix,  # noqa: SLF001
        )
        if staging_tables or staging_objects:
            raise RedshiftBoundedMemoryQualificationError(
                "Redshift bounded-memory qualification left run-scoped staging objects"
            )
        time.sleep(config.cost_observation_delay_seconds)
        charged, compute, capacity = bulk._serverless_usage(runtime)  # noqa: SLF001
        if charged <= 0:
            raise RedshiftBoundedMemoryQualificationError(
                "Redshift Serverless did not report charged provider usage"
            )
        provider_cost = (
            charged * config.on_demand_rate_usd_per_rpu_hour / Decimal(3_600)
        ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
        result = _BoundedResult(
            duration_ms=workload_duration_ms,
            peak_rss_bytes=max(peak_before, bulk._peak_rss_bytes()),  # noqa: SLF001
            rows=rows,
            logical_input_bytes=config.logical_input_bytes,
            copy_operations=len(operations),
            query_ids=bulk._operation_query_ids(operations),  # noqa: SLF001
            queue_duration_ms=sum(item.queue_duration_ms for item in operations),
            load_duration_ms=sum(item.duration_ms for item in operations),
            bytes_processed=sum(item.bytes_processed for item in operations),
            spill_bytes=sum(item.spill_bytes for item in operations),
            charged_seconds=charged,
            compute_seconds=compute,
            maximum_compute_capacity_rpu=capacity,
            provider_cost_usd=provider_cost,
            staging_tables=staging_tables,
            staging_objects=staging_objects,
            cleanup_verified=False,
        )
    except Exception as error:
        failure = error
    finally:
        cleanup_error: Exception | None = None
        try:
            shared._drop_schema(runtime, schema_name)  # noqa: SLF001
        except Exception as error:
            cleanup_error = error
        try:
            shared._delete_prefix(  # noqa: SLF001
                bulk._shared_config(bulk_config),
                staging_prefix,  # noqa: SLF001
            )
        except Exception as error:
            cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            raise RedshiftBoundedMemoryQualificationError(
                "Redshift bounded-memory qualification could not remove all owned resources"
            ) from cleanup_error
    cleanup = not shared._schema_exists(runtime, schema_name) and (  # noqa: SLF001
        shared._prefix_object_count(  # noqa: SLF001
            bulk._shared_config(bulk_config),
            staging_prefix,  # noqa: SLF001
        )
        == 0
    )
    if not cleanup:
        raise RedshiftBoundedMemoryQualificationError(
            "Redshift bounded-memory qualification cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, (RedshiftBoundedMemoryQualificationError, ValueError)):
            raise failure
        raise RedshiftBoundedMemoryQualificationError(
            "Redshift bounded-memory qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return _report(config, identity, approval, replace(result, cleanup_verified=True))


def _report(
    config: RedshiftBoundedMemoryConfig,
    identity: bulk.CandidateIdentity,
    approval: _Approval,
    result: _BoundedResult,
) -> QualificationReport:
    if result.rows != config.rows or result.logical_input_bytes != config.logical_input_bytes:
        raise RedshiftBoundedMemoryQualificationError("bounded-memory result differs from approval")
    if result.logical_input_bytes < config.memory_limit_bytes * 10:
        raise RedshiftBoundedMemoryQualificationError(
            "logical input is less than ten times the container memory limit"
        )
    if result.peak_rss_bytes * 5 > config.memory_limit_bytes * 4:
        raise RedshiftBoundedMemoryQualificationError(
            "peak RSS exceeds eighty percent of the container memory limit"
        )
    if result.copy_operations <= 0:
        raise RedshiftBoundedMemoryQualificationError(
            "Redshift bounded-memory run recorded no COPY operations"
        )
    if result.staging_tables or result.staging_objects or not result.cleanup_verified:
        raise RedshiftBoundedMemoryQualificationError(
            "Redshift bounded-memory cleanup is incomplete"
        )
    cost_status = (
        ObjectiveStatus.PASSED
        if result.provider_cost_usd <= approval.cost_ceiling.amount_usd
        else ObjectiveStatus.FAILED
    )
    status = (
        QualificationStatus.PASSED
        if cost_status is ObjectiveStatus.PASSED
        else QualificationStatus.FAILED
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
            warehouse="redshift",
            state_backend="none",
            catalog="none",
            secret_provider=identity.secret_provider,
            regions=(f"aws:{config.region}",),
            service_shapes=tuple(sorted(set(identity.service_shapes))),
            provider_job_ids=tuple(sorted(set((*identity.provider_job_ids, *result.query_ids)))),
            cost_ceiling=approval.cost_ceiling,
        ),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.BOUNDED_MEMORY,
            input_rows=result.rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=config.payload_bytes + 24,
            schema_depth=1,
            source_rate_limit="unlimited_container_generator",
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
                bulk._throughput(result.rows, result.duration_ms),  # noqa: SLF001
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured(
                "queue_duration_ms", "milliseconds", result.queue_duration_ms
            ),
            load_duration_ms=measured("load_duration_ms", "milliseconds", result.load_duration_ms),
            transform_duration_ms=measured("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measured("bytes_processed", "bytes", result.bytes_processed),
                measured("charged_seconds", "rpu_seconds", result.charged_seconds),
                measured("compute_seconds", "rpu_seconds", result.compute_seconds),
                measured("copy_operations", "count", result.copy_operations),
                measured("maximum_compute_capacity", "rpu", result.maximum_compute_capacity_rpu),
                measured("memory_limit_bytes", "bytes", config.memory_limit_bytes),
                measured("provider_operation_retries", "count", 0),
                measured("spill_bytes", "bytes", result.spill_bytes),
                measured("staging_objects", "count", result.staging_objects),
                measured("staging_tables", "count", result.staging_tables),
            ),
            costs=(
                CostAttribution(
                    provider="aws",
                    service="redshift_serverless",
                    amount=result.provider_cost_usd,
                    estimated=False,
                ),
            ),
        ),
        objectives=tuple(
            ObjectiveResult(
                name,
                cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
                f"phase8/aws/redshift/bounded-memory/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=status,
    )


def _bulk_config(config: RedshiftBoundedMemoryConfig) -> bulk.RedshiftBulkConfig:
    return bulk.RedshiftBulkConfig(
        account_id=config.account_id,
        host=config.host,
        database=config.database,
        region=config.region,
        workgroup_name=config.workgroup_name,
        copy_role_arn=config.copy_role_arn,
        staging_bucket=config.staging_bucket,
        staging_prefix=config.staging_prefix,
        port=config.port,
        connect_timeout_seconds=config.connect_timeout_seconds,
        statement_timeout_ms=config.statement_timeout_ms,
        narrow_rows=config.rows,
        narrow_payload_bytes=config.payload_bytes,
        wide_rows=config.rows,
        wide_payload_bytes=config.payload_bytes,
        copy_part_rows=config.copy_part_rows,
        copy_part_logical_bytes=config.copy_part_logical_bytes,
        cost_observation_delay_seconds=config.cost_observation_delay_seconds,
        on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
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


def _require_container_memory_limit(config: RedshiftBoundedMemoryConfig) -> None:
    if _container_memory_limit_bytes() != config.memory_limit_bytes:
        raise RedshiftBoundedMemoryQualificationError(
            "container memory limit does not match the approved bounded-memory objective"
        )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"objective approval {label} is incomplete")
    return cast("Mapping[str, object]", value)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--workgroup-name", required=True)
    parser.add_argument("--copy-role-arn", required=True)
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--staging-prefix", required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--benchmark-date", type=date.fromisoformat, required=True)
    parser.add_argument("--launcher", default="aws_native_fargate")
    parser.add_argument("--secret-provider", default="aws_task_role")
    parser.add_argument("--service-shape", action="append", required=True)
    parser.add_argument("--provider-job-id", action="append", required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    config = RedshiftBoundedMemoryConfig(
        account_id=arguments.account_id,
        host=arguments.host,
        database=arguments.database,
        region=arguments.region,
        workgroup_name=arguments.workgroup_name,
        copy_role_arn=arguments.copy_role_arn,
        staging_bucket=arguments.staging_bucket,
        staging_prefix=arguments.staging_prefix,
    )
    identity = bulk.CandidateIdentity(
        release_version=arguments.release_version,
        git_commit=arguments.git_commit,
        image_digest=arguments.image_digest,
        approval_reference=arguments.approval_reference,
        benchmark_date=arguments.benchmark_date,
        launcher=arguments.launcher,
        secret_provider=arguments.secret_provider,
        service_shapes=tuple(sorted(set(arguments.service_shape))),
        provider_job_ids=tuple(sorted(set(arguments.provider_job_id))),
    )
    approval = _load_approval(arguments.approval_manifest, config=config, identity=identity)
    report = run_phase8_redshift_bounded_memory(config, identity=identity, approval=approval)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (RedshiftBoundedMemoryQualificationError, ValueError, OSError) as error:
        print(f"Redshift bounded-memory qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
