#!/usr/bin/env python3
"""Exact-candidate Redshift concurrency Phase 8 qualification."""

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
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dander import __version__
from dander.concurrency import FencingToken
from dander.providers.redshift.session import execute, open_connection
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
    TelemetryOperation,
)
from dander.warehouse import RelationRef
from scripts.benchmarks import redshift as shared
from scripts.benchmarks import redshift_bulk_phase8 as bulk

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dander.warehouse import WarehouseRuntime


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.redshift-concurrency/v1"
_INTERIM_SCHEMA = "io.dander.phase8.redshift-concurrency-interim/v1"
_OBJECTIVES = (
    "cleanup",
    "concurrent_pipeline_completion",
    "controlled_contention",
    "cost_ceiling",
    "stale_fence_rejection",
    "throughput_measurement",
)
_TASK_ROLE_REQUIREMENTS = bulk._TASK_ROLE_REQUIREMENTS  # noqa: SLF001
_FARGATE_LAUNCHER_REQUIREMENTS = bulk._FARGATE_LAUNCHER_REQUIREMENTS  # noqa: SLF001
_LEGACY_TASK_ROLE_REQUIREMENTS = bulk._LEGACY_TASK_ROLE_REQUIREMENTS  # noqa: SLF001
_LEGACY_FARGATE_LAUNCHER_REQUIREMENTS = (  # noqa: SLF001
    bulk._LEGACY_FARGATE_LAUNCHER_REQUIREMENTS
)
_LEGACY_CANDIDATE_COMMAND = (
    "dander qualification-run /tmp/harness/scripts/benchmarks/redshift_concurrency_phase8.py"
)
_CANDIDATE_COMMAND = (
    "cd /tmp/harness && PYTHONPATH=/tmp/harness dander qualification-run "
    "scripts/benchmarks/redshift_concurrency_phase8.py --defer-cost-attribution"
)


class RedshiftConcurrencyQualificationError(RuntimeError):
    """Raised with a credential-free Redshift concurrency summary."""


@dataclass(frozen=True, slots=True)
class RedshiftConcurrencyConfig:
    """Non-secret Redshift coordinates and the accepted concurrent workload."""

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
    concurrent_pipelines: int = 4
    rows_per_pipeline: int = 5_000
    payload_bytes: int = 128
    copy_part_rows: int = 5_000
    copy_part_logical_bytes: int = 64 * 1_024 * 1_024
    cost_observation_delay_seconds: int = 70
    on_demand_rate_usd_per_rpu_hour: Decimal = Decimal("0.375")

    def __post_init__(self) -> None:
        if len(self.account_id) != 12 or not self.account_id.isdigit():
            raise ValueError("account_id must be a 12-digit AWS account id")
        for name in (
            "port",
            "connect_timeout_seconds",
            "statement_timeout_ms",
            "concurrent_pipelines",
            "rows_per_pipeline",
            "payload_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
            "cost_observation_delay_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.concurrent_pipelines != 4:
            raise ValueError("concurrent_pipelines must be exactly 4")
        if self.rows_per_pipeline != 5_000:
            raise ValueError("rows_per_pipeline must be exactly 5000")
        if self.copy_part_rows > self.rows_per_pipeline:
            raise ValueError("copy_part_rows must not exceed rows_per_pipeline")
        if self.cost_observation_delay_seconds < 60:
            raise ValueError("cost observation must wait for one complete provider interval")
        if (
            not self.on_demand_rate_usd_per_rpu_hour.is_finite()
            or self.on_demand_rate_usd_per_rpu_hour <= 0
        ):
            raise ValueError("Redshift on-demand rate must be a positive Decimal")
        _bulk_config(self)

    def workload_payload(self) -> dict[str, object]:
        """Return the exact workload covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.CONCURRENT_PIPELINES.value,
            "concurrent_pipelines": self.concurrent_pipelines,
            "rows_per_pipeline": self.rows_per_pipeline,
            "payload_bytes": self.payload_bytes,
            "copy_part_rows": self.copy_part_rows,
            "copy_part_logical_bytes": self.copy_part_logical_bytes,
            "controlled_claims": 2,
        }

    def configuration_sha256(self) -> str:
        """Hash the canonical workload used by objective approval."""
        encoded = json.dumps(
            self.workload_payload(), separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


CandidateIdentity = bulk.CandidateIdentity


@dataclass(frozen=True, slots=True)
class _Approval:
    objectives: ApprovedObjectiveSet
    cost_ceiling: ApprovedCostCeiling
    account_id: str
    region: str
    workgroup_name: str
    copy_role_arn: str
    staging_bucket: str
    staging_prefix: str
    cost_observation_delay_seconds: int
    on_demand_rate_usd_per_rpu_hour: Decimal


@dataclass(frozen=True, slots=True)
class _ConcurrencyResult:
    duration_ms: int
    peak_rss_bytes: int
    pipeline_duration_ms: int
    pipeline_count: int
    rows_per_pipeline: int
    total_rows: int
    logical_input_bytes: int
    concurrent_claim_attempts: int
    stale_publications_rejected: int
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
    provider_operation_retries: int
    staging_tables: int
    staging_objects: int
    cleanup_verified: bool


def _load_approval(
    path: Path,
    *,
    config: RedshiftConcurrencyConfig,
    identity: CandidateIdentity,
) -> _Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective approval schema is incompatible")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective approval workload does not match the requested run")
    configuration = _mapping(payload.get("configuration"), "configuration")
    provider = _mapping(configuration.get("redshift"), "Redshift configuration")
    legacy_expected_provider = {
        "account_id": config.account_id,
        "region": config.region,
        "workgroup_name": config.workgroup_name,
        "copy_role_arn": config.copy_role_arn,
        "staging_bucket": config.staging_bucket,
        "staging_prefix": config.staging_prefix,
        "on_demand_rate_usd_per_rpu_hour": str(config.on_demand_rate_usd_per_rpu_hour),
    }
    launcher_expected_provider = {
        **legacy_expected_provider,
        "host": config.host,
        "database": config.database,
    }
    if provider not in (legacy_expected_provider, launcher_expected_provider):
        raise ValueError("objective approval does not match the Redshift data plane")
    canonical_rc32 = identity.release_version == "0.9.0rc32"
    expected_task_role = (
        _TASK_ROLE_REQUIREMENTS if canonical_rc32 else _LEGACY_TASK_ROLE_REQUIREMENTS
    )
    if _mapping(configuration.get("task_role"), "task role") != expected_task_role:
        raise ValueError("objective approval does not bind the required Redshift task role")
    execution = _mapping(configuration.get("execution"), "execution configuration")
    if execution.get("harness_sha256") != _file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    if execution.get("shared_harness_sha256") != _file_sha256(Path(shared.__file__)):
        raise ValueError("objective approval does not match the shared Redshift harness")
    if execution.get("bulk_harness_sha256") != _file_sha256(Path(bulk.__file__)):
        raise ValueError("objective approval does not match the reused Redshift runtime helpers")
    if execution.get("manual_candidate_executions") != 1:
        raise ValueError("objective approval must allow exactly one candidate execution")
    if execution.get("automatic_candidate_retry") is not False:
        raise ValueError("objective approval must disable automatic candidate retry")
    if execution.get("provider_operation_retries") != 0:
        raise ValueError("objective approval must disable provider-operation retries")
    if execution.get("cost_observation_delay_seconds") != config.cost_observation_delay_seconds:
        raise ValueError("objective approval changed the provider cost observation")
    if canonical_rc32 and execution.get("defer_provider_cost_attribution") is not True:
        raise ValueError("objective approval must defer superuser-only cost attribution")
    expected_command = _CANDIDATE_COMMAND if canonical_rc32 else _LEGACY_CANDIDATE_COMMAND
    if execution.get("candidate_command") != expected_command:
        raise ValueError("objective approval does not bind the candidate command")
    fargate = _mapping(configuration.get("fargate_harness"), "Fargate configuration")
    expected_fargate = (
        _FARGATE_LAUNCHER_REQUIREMENTS
        if canonical_rc32
        else {
            "task_cpu_units": 2_048,
            "task_memory_mib": 4_096,
            "task_timeout_seconds": 900,
            "cluster_executions": 1,
            "state_machine_executions": 1,
            "state_machine_retry_states": 0,
            "ecs_task_retries": 0,
            "container_restarts": 0,
            "automatic_retry": False,
            **_LEGACY_FARGATE_LAUNCHER_REQUIREMENTS,
        }
    )
    if any(fargate.get(name) != value for name, value in expected_fargate.items()):
        raise ValueError("objective approval must use the exact zero-retry Fargate shape")
    objective_payload = _mapping(payload.get("approved_objectives"), "approved objectives")
    names = objective_payload.get("names")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("objective approval names are malformed")
    objectives = ApprovedObjectiveSet(
        names=tuple(names),
        benchmark_class=BenchmarkClass(str(objective_payload.get("benchmark_class"))),
        profile_id=str(objective_payload.get("profile_id")),
        release_version=str(objective_payload.get("release_version")),
        git_commit=str(objective_payload.get("git_commit")),
        image_digest=str(objective_payload.get("image_digest")),
        configuration_sha256=str(objective_payload.get("configuration_sha256")),
        approval_reference=str(objective_payload.get("approval_reference")),
    )
    if objectives.names != _OBJECTIVES:
        raise ValueError("objective approval names do not match Redshift concurrency")
    if objectives.benchmark_class is not BenchmarkClass.CONCURRENT_PIPELINES:
        raise ValueError("objective approval benchmark class is not concurrent_pipelines")
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
    if cost_ceiling.approval_reference != identity.approval_reference:
        raise ValueError("cost ceiling approval does not match the candidate approval")
    return _Approval(
        objectives=objectives,
        cost_ceiling=cost_ceiling,
        account_id=config.account_id,
        region=config.region,
        workgroup_name=config.workgroup_name,
        copy_role_arn=config.copy_role_arn,
        staging_bucket=config.staging_bucket,
        staging_prefix=config.staging_prefix,
        cost_observation_delay_seconds=config.cost_observation_delay_seconds,
        on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
    )


def run_phase8_redshift_concurrency(
    config: RedshiftConcurrencyConfig,
    *,
    identity: CandidateIdentity,
    approval: _Approval,
    defer_cost_attribution: bool = False,
) -> QualificationReport | _ConcurrencyResult:
    """Run the accepted concurrency class in one disposable Redshift schema."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_no_provider_retries()
    if approval.objectives.configuration_sha256 != config.configuration_sha256():
        raise ValueError("objective approval does not match the requested workload")
    suffix = uuid.uuid4().hex[:12]
    schema_name = f"dander_p8_concurrency_{suffix}"
    staging_prefix = f"{config.staging_prefix}/concurrency/{suffix}"
    runtime = bulk._warehouse_runtime(  # noqa: SLF001
        _bulk_config(config), schema_name=schema_name, staging_prefix=staging_prefix
    )
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    result: _ConcurrencyResult | None = None
    failure: Exception | None = None
    try:
        pipelines_started = time.perf_counter()
        total_rows, operations = _write_independent_pipelines(
            runtime, config=config, schema=schema_name
        )
        _require_independent_readback(runtime, config=config, schema=schema_name)
        pipeline_duration_ms = _elapsed_ms(pipelines_started)
        stale_rejected, claim_attempts = shared._exercise_concurrent_fence(  # noqa: SLF001
            runtime, schema_name
        )
        if not stale_rejected or claim_attempts != 2:
            raise RedshiftConcurrencyQualificationError(
                "Redshift controlled contention evidence is incomplete"
            )
        staging_tables = bulk._staging_table_count(runtime, schema_name)  # noqa: SLF001
        staging_objects = shared._prefix_object_count(  # noqa: SLF001
            _shared_config(config), staging_prefix
        )
        if staging_tables or staging_objects:
            raise RedshiftConcurrencyQualificationError(
                "Redshift concurrency qualification left run-scoped staging resources"
            )
        charged = compute = capacity = provider_cost = Decimal(0)
        if not defer_cost_attribution:
            time.sleep(config.cost_observation_delay_seconds)
            charged, compute, capacity = bulk._serverless_usage(runtime)  # noqa: SLF001
            if charged <= 0:
                raise RedshiftConcurrencyQualificationError(
                    "Redshift Serverless did not report charged provider usage"
                )
            provider_cost = (
                charged * config.on_demand_rate_usd_per_rpu_hour / Decimal(3600)
            ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
        result = _ConcurrencyResult(
            duration_ms=_elapsed_ms(started),
            peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
            pipeline_duration_ms=pipeline_duration_ms,
            pipeline_count=config.concurrent_pipelines,
            rows_per_pipeline=config.rows_per_pipeline,
            total_rows=total_rows,
            logical_input_bytes=total_rows * (config.payload_bytes + 24),
            concurrent_claim_attempts=claim_attempts,
            stale_publications_rejected=1,
            copy_operations=sum(
                operation.operation is TelemetryOperation.LOAD for operation in operations
            ),
            query_ids=bulk._operation_query_ids(operations),  # noqa: SLF001
            queue_duration_ms=sum(operation.queue_duration_ms for operation in operations),
            load_duration_ms=sum(operation.duration_ms for operation in operations),
            bytes_processed=sum(operation.bytes_processed for operation in operations),
            spill_bytes=sum(operation.spill_bytes for operation in operations),
            charged_seconds=charged,
            compute_seconds=compute,
            maximum_compute_capacity_rpu=capacity,
            provider_cost_usd=provider_cost,
            provider_operation_retries=sum(operation.retry_count for operation in operations),
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
            shared._delete_prefix(_shared_config(config), staging_prefix)  # noqa: SLF001
        except Exception as error:
            cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            raise RedshiftConcurrencyQualificationError(
                "Redshift concurrency qualification could not remove all owned resources"
            ) from cleanup_error
    cleanup = not shared._schema_exists(runtime, schema_name) and (  # noqa: SLF001
        shared._prefix_object_count(_shared_config(config), staging_prefix) == 0  # noqa: SLF001
    )
    if not cleanup:
        raise RedshiftConcurrencyQualificationError(
            "Redshift concurrency qualification cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, (RedshiftConcurrencyQualificationError, ValueError)):
            raise failure
        raise RedshiftConcurrencyQualificationError(
            "Redshift concurrency qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    completed = replace(result, cleanup_verified=True)
    if defer_cost_attribution:
        return completed
    return _report(config, identity, approval, completed)


def _write_independent_pipelines(
    runtime: WarehouseRuntime,
    *,
    config: RedshiftConcurrencyConfig,
    schema: str,
) -> tuple[int, tuple[OperationTelemetry, ...]]:
    _initialize_pipeline_fences(runtime, config=config, schema=schema)
    with ThreadPoolExecutor(max_workers=config.concurrent_pipelines) as executor:
        futures = tuple(
            executor.submit(
                bulk._write_table,  # noqa: SLF001
                runtime,
                config=_bulk_config(config),
                schema=schema,
                table=f"pipeline_{index:02d}_records",
                pipeline_id=f"phase8_redshift_concurrency_{index:02d}",
                rows=config.rows_per_pipeline,
                payload_bytes=config.payload_bytes,
            )
            for index in range(config.concurrent_pipelines)
        )
        values = tuple(future.result() for future in futures)
    affected = tuple(value[0] for value in values)
    if affected != (config.rows_per_pipeline,) * config.concurrent_pipelines:
        raise RedshiftConcurrencyQualificationError(
            "Redshift concurrent pipeline affected an unexpected row count"
        )
    return sum(affected), tuple(operation for value in values for operation in value[2])


def _initialize_pipeline_fences(
    runtime: WarehouseRuntime,
    *,
    config: RedshiftConcurrencyConfig,
    schema: str,
) -> None:
    """Create the shared fence table before independent claims enter worker threads."""
    for index in range(config.concurrent_pipelines):
        pipeline_id = f"phase8_redshift_concurrency_{index:02d}"
        runtime.target_fence.claim(
            RelationRef(
                catalog=config.database,
                namespace=schema,
                name=f"pipeline_{index:02d}_records",
            ),
            FencingToken(
                lease_table=None,
                pipeline_id=pipeline_id,
                run_id=f"{pipeline_id}-one",
                token=1,
                authority_id=bulk._AUTHORITY_ID,  # noqa: SLF001
            ),
        )


def _require_independent_readback(
    runtime: WarehouseRuntime,
    *,
    config: RedshiftConcurrencyConfig,
    schema: str,
) -> None:
    union = " UNION ALL ".join(
        f'SELECT {index} AS pipeline_index, "id", "payload" FROM '
        f"{bulk._qualified(schema, f'pipeline_{index:02d}_records')}"  # noqa: SLF001
        for index in range(config.concurrent_pipelines)
    )
    statement = (
        'SELECT pipeline_index, COUNT(*) AS row_count, COUNT(DISTINCT "id"), '
        'MIN(LEN("payload")), MAX(LEN("payload")) '
        f"FROM ({union}) grouped_rows GROUP BY pipeline_index ORDER BY pipeline_index"
    )
    with open_connection(bulk._connection_factory(runtime)) as connection:  # noqa: SLF001
        rows = execute(connection, statement, fetch="all").rows
    expected = tuple(
        (
            index,
            config.rows_per_pipeline,
            config.rows_per_pipeline,
            config.payload_bytes,
            config.payload_bytes,
        )
        for index in range(config.concurrent_pipelines)
    )
    try:
        observed = tuple(_readback_row(row) for row in rows)
    except (TypeError, ValueError) as error:
        raise RedshiftConcurrencyQualificationError(
            "Redshift concurrency readback was not numeric"
        ) from error
    if observed != expected:
        raise RedshiftConcurrencyQualificationError(
            "Redshift concurrent pipeline readback differs from the accepted workload"
        )


def _readback_row(row: object) -> tuple[int, int, int, int, int]:
    if not isinstance(row, (tuple, list)) or len(row) != 5:
        raise TypeError("Redshift concurrency readback row is malformed")
    values = tuple(int(value) for value in row)
    return cast("tuple[int, int, int, int, int]", values)


def _report(
    config: RedshiftConcurrencyConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _ConcurrencyResult,
) -> QualificationReport:
    if (
        result.total_rows != config.concurrent_pipelines * config.rows_per_pipeline
        or result.concurrent_claim_attempts != 2
        or result.stale_publications_rejected != 1
        or result.provider_operation_retries != 0
        or result.staging_tables != 0
        or result.staging_objects != 0
        or not result.cleanup_verified
    ):
        raise RedshiftConcurrencyQualificationError("Redshift concurrency evidence is incomplete")
    cost_status = (
        ObjectiveStatus.PASSED
        if result.provider_cost_usd <= approval.cost_ceiling.amount_usd
        else ObjectiveStatus.FAILED
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
            benchmark_class=BenchmarkClass.CONCURRENT_PIPELINES,
            input_rows=result.total_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=config.payload_bytes + 24,
            schema_depth=1,
            source_rate_limit="unlimited_container_generator",
            transform_complexity="independent_targets_and_contended_fence",
            concurrency=result.pipeline_count,
            batch_rows=config.copy_part_rows,
            batch_bytes=config.copy_part_logical_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.total_rows),
            logical_bytes=measured("logical_bytes", "bytes", result.logical_input_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(result.total_rows, result.pipeline_duration_ms),
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
                measured("concurrent_claim_attempts", "count", result.concurrent_claim_attempts),
                measured("copy_operations", "count", result.copy_operations),
                measured("maximum_compute_capacity", "rpu", result.maximum_compute_capacity_rpu),
                measured("pipeline_count", "count", result.pipeline_count),
                measured("pipeline_duration_ms", "milliseconds", result.pipeline_duration_ms),
                measured("provider_operation_retries", "count", result.provider_operation_retries),
                measured("readback_rows", "rows", result.total_rows),
                measured("rows_per_pipeline", "rows", result.rows_per_pipeline),
                measured("spill_bytes", "bytes", result.spill_bytes),
                measured("staging_objects", "count", result.staging_objects),
                measured("staging_tables", "count", result.staging_tables),
                measured(
                    "stale_publications_rejected",
                    "count",
                    result.stale_publications_rejected,
                ),
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
                f"phase8/aws/redshift/concurrency/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=(
            QualificationStatus.PASSED
            if cost_status is ObjectiveStatus.PASSED
            else QualificationStatus.FAILED
        ),
    )


def _bulk_config(config: RedshiftConcurrencyConfig) -> bulk.RedshiftBulkConfig:
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
        narrow_rows=config.rows_per_pipeline,
        narrow_payload_bytes=config.payload_bytes,
        wide_rows=config.rows_per_pipeline,
        wide_payload_bytes=config.payload_bytes,
        copy_part_rows=config.copy_part_rows,
        copy_part_logical_bytes=config.copy_part_logical_bytes,
        cost_observation_delay_seconds=config.cost_observation_delay_seconds,
        on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
    )


def _shared_config(config: RedshiftConcurrencyConfig) -> shared.RedshiftQualificationConfig:
    return bulk._shared_config(_bulk_config(config))  # noqa: SLF001


def _require_no_provider_retries() -> None:
    if os.environ.get("AWS_MAX_ATTEMPTS") != "1":
        raise RedshiftConcurrencyQualificationError("AWS_MAX_ATTEMPTS must be exactly 1")
    if os.environ.get("AWS_RETRY_MODE") != "standard":
        raise RedshiftConcurrencyQualificationError("AWS_RETRY_MODE must be standard")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast("Mapping[str, object]", value)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _throughput(rows: int, duration_ms: int) -> Decimal:
    return (Decimal(rows) * 1_000 / Decimal(max(duration_ms, 1))).quantize(Decimal("0.001"))


def _elapsed_ms(started: float) -> int:
    return max(round((time.perf_counter() - started) * 1_000), 1)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1_024)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    attribution = parser.add_mutually_exclusive_group()
    attribution.add_argument("--defer-cost-attribution", action="store_true")
    attribution.add_argument("--finalize-cost-attribution", type=Path)
    parser.add_argument("--charged-seconds", type=Decimal)
    parser.add_argument("--compute-seconds", type=Decimal)
    parser.add_argument("--maximum-compute-capacity-rpu", type=Decimal)
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
    parser.add_argument("--launcher", default="aws_step_functions_fargate")
    parser.add_argument("--secret-provider", default="aws_task_role")
    parser.add_argument("--service-shape", action="append", required=True)
    parser.add_argument("--provider-job-id", action="append", required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    config = RedshiftConcurrencyConfig(
        account_id=arguments.account_id,
        host=arguments.host,
        database=arguments.database,
        region=arguments.region,
        workgroup_name=arguments.workgroup_name,
        copy_role_arn=arguments.copy_role_arn,
        staging_bucket=arguments.staging_bucket,
        staging_prefix=arguments.staging_prefix,
    )
    identity = CandidateIdentity(
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
    if arguments.defer_cost_attribution:
        result = run_phase8_redshift_concurrency(
            config,
            identity=identity,
            approval=approval,
            defer_cost_attribution=True,
        )
        assert isinstance(result, _ConcurrencyResult)
        interim = json.dumps(
            bulk._deferred_cost_interim_payload(  # noqa: SLF001
                schema=_INTERIM_SCHEMA,
                configuration_sha256=config.configuration_sha256(),
                identity=identity,
                approval=approval,
                result=result,
            ),
            separators=(",", ":"),
            sort_keys=True,
        )
        arguments.output.write_text(interim + "\n", encoding="utf-8")
        print(interim)
        return
    if arguments.finalize_cost_attribution is not None:
        workload = bulk._load_deferred_cost_workload(  # noqa: SLF001
            arguments.finalize_cost_attribution,
            schema=_INTERIM_SCHEMA,
            configuration_sha256=config.configuration_sha256(),
            identity=identity,
            approval=approval,
            result_type=_ConcurrencyResult,
        )
        result = bulk._with_external_cost(  # noqa: SLF001
            workload,
            charged_seconds=arguments.charged_seconds,
            compute_seconds=arguments.compute_seconds,
            maximum_compute_capacity_rpu=arguments.maximum_compute_capacity_rpu,
            on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
        )
        report = _report(config, identity, approval, result)
    else:
        if any(
            value is not None
            for value in (
                arguments.charged_seconds,
                arguments.compute_seconds,
                arguments.maximum_compute_capacity_rpu,
            )
        ):
            raise ValueError("provider measurements require external cost finalization")
        report_candidate = run_phase8_redshift_concurrency(
            config, identity=identity, approval=approval
        )
        assert isinstance(report_candidate, QualificationReport)
        report = report_candidate
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (RedshiftConcurrencyQualificationError, ValueError, OSError) as error:
        print(f"Redshift concurrency qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
