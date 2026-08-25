#!/usr/bin/env python3
"""Exact-candidate Redshift incremental Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dander import __version__
from dander.concurrency import FencingToken
from dander.providers.redshift.session import execute, open_connection
from dander.providers.redshift.writer import RedshiftStagedWriter
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
from dander.warehouse import RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget, WriteTransport
from scripts.benchmarks import redshift as shared
from scripts.benchmarks import redshift_bulk_phase8 as bulk

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.redshift-incremental/v1"
_INTERIM_SCHEMA = "io.dander.phase8.redshift-incremental-interim/v1"
_AUTHORITY_ID = "redshift:phase8-incremental"
_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "delta_target_ratio",
    "exact_result",
    "incremental_cursor_monotonic",
    "incremental_throughput_measurement",
)
_TASK_ROLE_REQUIREMENTS = bulk._TASK_ROLE_REQUIREMENTS  # noqa: SLF001
_FARGATE_LAUNCHER_REQUIREMENTS = bulk._FARGATE_LAUNCHER_REQUIREMENTS  # noqa: SLF001
_LEGACY_TASK_ROLE_REQUIREMENTS: dict[str, object] = {
    "redshift_db_roles_tag": {"key": "RedshiftDbRoles", "value": "dander_runtime"},
    "required_global_actions": ["tag:GetResources", "tag:GetTagKeys"],
    "required_global_resource": "*",
    "redshift_auth_action": "redshift-serverless:GetCredentials",
    "redshift_auth_resource_binding": "exact_owned_workgroup_arn_after_apply",
}
_LEGACY_FARGATE_LAUNCHER_REQUIREMENTS: dict[str, object] = {
    "runtime_cpu_architecture": "ARM64",
    "candidate_image_architecture": "arm64",
    "task_entrypoint": ["/bin/sh", "-c"],
    "candidate_python_executable": "python",
    "candidate_cli_executable": "dander",
    "forbidden_candidate_executable_prefix": "/app/.venv/bin/",
}
_LEGACY_CANDIDATE_COMMAND = (
    "dander qualification-run /tmp/harness/scripts/benchmarks/redshift_incremental_phase8.py"
)
_CANDIDATE_COMMAND = (
    "cd /tmp/harness && PYTHONPATH=/tmp/harness dander qualification-run "
    "scripts/benchmarks/redshift_incremental_phase8.py --defer-cost-attribution"
)


class RedshiftIncrementalQualificationError(RuntimeError):
    """Raised with a credential-free Redshift incremental summary."""


@dataclass(frozen=True, slots=True)
class RedshiftIncrementalConfig:
    """Non-secret Redshift coordinates and the accepted incremental workload."""

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
    seed_rows: int = 300_000
    delta_rows: int = 3_000
    payload_bytes: int = 128
    copy_part_rows: int = 50_000
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
            "seed_rows",
            "delta_rows",
            "payload_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
            "cost_observation_delay_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.delta_rows % 2:
            raise ValueError("delta_rows must be even for the accepted half-update workload")
        if self.seed_rows < self.delta_rows * 100:
            raise ValueError("seed_rows must be at least 100 times delta_rows")
        if self.copy_part_rows > self.seed_rows:
            raise ValueError("copy_part_rows must not exceed the seed workload")
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
            "benchmark_class": BenchmarkClass.INCREMENTAL.value,
            "seed_rows": self.seed_rows,
            "delta_rows": self.delta_rows,
            "payload_bytes": self.payload_bytes,
            "delta_shape": "half_updates_half_inserts",
            "copy_part_rows": self.copy_part_rows,
            "copy_part_logical_bytes": self.copy_part_logical_bytes,
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
class _IncrementalResult:
    duration_ms: int
    peak_rss_bytes: int
    seed_duration_ms: int
    seed_rows: int
    seed_logical_bytes: int
    delta_duration_ms: int
    delta_rows: int
    delta_rows_affected: int
    delta_logical_bytes: int
    final_rows: int
    updated_rows: int
    inserted_rows: int
    cursor_initial: int
    cursor_final: int
    cursor_regressions_rejected: int
    regression_rows_affected: int
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
    config: RedshiftIncrementalConfig,
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
    task_role = _mapping(configuration.get("task_role"), "task role configuration")
    expected_task_role = (
        _TASK_ROLE_REQUIREMENTS if canonical_rc32 else _LEGACY_TASK_ROLE_REQUIREMENTS
    )
    if task_role != expected_task_role:
        raise ValueError("objective approval omits the exact Redshift Serverless task-role access")
    execution = _mapping(configuration.get("execution"), "execution configuration")
    if execution.get("harness_sha256") != _file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    if execution.get("shared_harness_sha256") != _file_sha256(Path(shared.__file__)):
        raise ValueError("objective approval does not match the shared Redshift harness")
    if execution.get("bulk_harness_sha256") != _file_sha256(Path(bulk.__file__)):
        raise ValueError("objective approval does not match the reused Redshift runtime helpers")
    if canonical_rc32:
        if execution.get("manual_candidate_executions") != 1:
            raise ValueError("objective approval must allow exactly one candidate execution")
    elif (
        execution.get("prior_failed_candidate_executions") != 1
        or execution.get("manual_candidate_executions") != 1
        or execution.get("corrective_candidate_executions") != 1
    ):
        raise ValueError("objective approval must allow exactly one corrective candidate execution")
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
        raise ValueError("objective approval does not bind the corrected candidate command")
    fargate = _mapping(configuration.get("fargate_harness"), "Fargate harness configuration")
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
        raise ValueError(
            "objective approval must use the exact zero-retry Fargate 2-vCPU/4-GiB shape"
        )
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
        raise ValueError("objective approval names do not match Redshift incremental qualification")
    if objectives.benchmark_class is not BenchmarkClass.INCREMENTAL:
        raise ValueError("objective approval benchmark class is not incremental")
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


def run_phase8_redshift_incremental(
    config: RedshiftIncrementalConfig,
    *,
    identity: CandidateIdentity,
    approval: _Approval,
    defer_cost_attribution: bool = False,
) -> QualificationReport | _IncrementalResult:
    """Run the accepted incremental class in one disposable Redshift schema."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_no_provider_retries()
    if approval.objectives.configuration_sha256 != config.configuration_sha256():
        raise ValueError("objective approval does not match the requested workload")

    cursor = _advance_cursor(None, 1)
    suffix = uuid.uuid4().hex[:12]
    schema_name = f"dander_p8_incremental_{suffix}"
    staging_prefix = f"{config.staging_prefix}/incremental/{suffix}"
    runtime = bulk._warehouse_runtime(  # noqa: SLF001
        _bulk_config(config), schema_name=schema_name, staging_prefix=staging_prefix
    )
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    result: _IncrementalResult | None = None
    failure: Exception | None = None
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
                pipeline_id="phase8_redshift_incremental",
                run_id="phase8-redshift-incremental-one",
                token=1,
                authority_id=_AUTHORITY_ID,
            ),
        )
        writer = runtime.writers.build_ingestion_writer(
            sandbox=False,
            batch_rows=config.copy_part_rows,
            schema_evolution=SchemaEvolution.STRICT,
            mode=WriteMode.INCREMENTAL,
            cursor_field="cursor_value",
        )
        if not isinstance(writer, RedshiftStagedWriter):
            raise RedshiftIncrementalQualificationError(
                "Redshift incremental qualification did not select the staged writer"
            )
        target = WriteTarget(
            relation=relation,
            business_key=("id",),
            schema=(
                WriteField(name="id", data_type="STRING", mode="REQUIRED"),
                WriteField(name="payload", data_type="STRING", mode="REQUIRED"),
                WriteField(name="cursor_value", data_type="INT64", mode="REQUIRED"),
            ),
            publication_fence=publication,
        )

        seed_started = time.perf_counter()
        seed_rows = writer.write(_seed_records(config), target)
        seed_ms = _elapsed_ms(seed_started)
        seed_operations = writer.drain_telemetry()
        _require_copy_operations(seed_operations, workload="incremental seed")
        if seed_rows != config.seed_rows:
            raise RedshiftIncrementalQualificationError(
                "Redshift incremental seed affected an unexpected row count"
            )

        cursor = _advance_cursor(cursor, 2)
        delta_started = time.perf_counter()
        delta_rows_affected = writer.write(_delta_records(config), target)
        delta_ms = _elapsed_ms(delta_started)
        delta_operations = writer.drain_telemetry()
        _require_copy_operations(delta_operations, workload="incremental delta")
        # Redshift's MERGE command tag is retained as provider telemetry. The exact
        # update/insert split and final target state are verified by the readback below.

        regressions_rejected = 0
        try:
            _advance_cursor(cursor, 1)
        except RedshiftIncrementalQualificationError:
            regressions_rejected = 1
        if regressions_rejected != 1:
            raise RedshiftIncrementalQualificationError(
                "Redshift incremental cursor regression was not rejected"
            )

        final_rows = config.seed_rows + (config.delta_rows // 2)
        updated_rows, inserted_rows = _require_incremental_result(
            runtime,
            config=config,
            schema=schema_name,
            expected_rows=final_rows,
        )
        staging_tables = bulk._staging_table_count(runtime, schema_name)  # noqa: SLF001
        staging_objects = shared._prefix_object_count(  # noqa: SLF001
            _shared_config(config), staging_prefix
        )
        if staging_tables or staging_objects:
            raise RedshiftIncrementalQualificationError(
                "Redshift incremental qualification left run-scoped staging objects"
            )
        charged = compute = capacity = provider_cost = Decimal(0)
        if not defer_cost_attribution:
            time.sleep(config.cost_observation_delay_seconds)
            charged, compute, capacity = bulk._serverless_usage(runtime)  # noqa: SLF001
            if charged <= 0:
                raise RedshiftIncrementalQualificationError(
                    "Redshift Serverless did not report charged provider usage"
                )
            provider_cost = (
                charged * config.on_demand_rate_usd_per_rpu_hour / Decimal(3600)
            ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
        operations = (*seed_operations, *delta_operations)
        row_width = config.payload_bytes + 32
        result = _IncrementalResult(
            duration_ms=_elapsed_ms(started),
            peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
            seed_duration_ms=seed_ms,
            seed_rows=seed_rows,
            seed_logical_bytes=seed_rows * row_width,
            delta_duration_ms=delta_ms,
            delta_rows=config.delta_rows,
            delta_rows_affected=delta_rows_affected,
            delta_logical_bytes=config.delta_rows * row_width,
            final_rows=final_rows,
            updated_rows=updated_rows,
            inserted_rows=inserted_rows,
            cursor_initial=1,
            cursor_final=cursor,
            cursor_regressions_rejected=regressions_rejected,
            regression_rows_affected=0,
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
            raise RedshiftIncrementalQualificationError(
                "Redshift incremental qualification could not remove all owned resources"
            ) from cleanup_error
    cleanup = not shared._schema_exists(runtime, schema_name) and (  # noqa: SLF001
        shared._prefix_object_count(_shared_config(config), staging_prefix) == 0  # noqa: SLF001
    )
    if not cleanup:
        raise RedshiftIncrementalQualificationError(
            "Redshift incremental qualification cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, (RedshiftIncrementalQualificationError, ValueError)):
            raise failure
        raise RedshiftIncrementalQualificationError(
            "Redshift incremental qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    completed = replace(result, cleanup_verified=True)
    if defer_cost_attribution:
        return completed
    return _report(config, identity, approval, completed)


def _require_incremental_result(
    runtime: WarehouseRuntime,
    *,
    config: RedshiftIncrementalConfig,
    schema: str,
    expected_rows: int,
) -> tuple[int, int]:
    updated = config.delta_rows // 2
    inserted = config.delta_rows - updated
    statement = (
        'SELECT COUNT(*) AS row_count, COUNT(DISTINCT "id") AS distinct_ids, '
        'SUM(CASE WHEN "cursor_value" = 2 AND CAST("id" AS BIGINT) < %s THEN 1 ELSE 0 END), '
        'SUM(CASE WHEN "cursor_value" = 2 AND CAST("id" AS BIGINT) >= %s THEN 1 ELSE 0 END), '
        'SUM(CASE WHEN "cursor_value" = 2 AND "payload" = %s THEN 1 ELSE 0 END), '
        'SUM(CASE WHEN "cursor_value" = 1 AND "payload" = %s THEN 1 ELSE 0 END), '
        'SUM(CASE WHEN "cursor_value" NOT IN (1, 2) THEN 1 ELSE 0 END) '
        f"FROM {bulk._qualified(schema, 'incremental_records')}"  # noqa: SLF001
    )
    with open_connection(bulk._connection_factory(runtime)) as connection:  # noqa: SLF001
        row = execute(
            connection,
            statement,
            (updated, config.seed_rows, "d" * config.payload_bytes, "s" * config.payload_bytes),
            fetch="one",
        ).row
    if not isinstance(row, (tuple, list)) or len(row) != 7:
        raise RedshiftIncrementalQualificationError(
            "Redshift incremental verification returned a malformed row"
        )
    try:
        observed = tuple(int(value) for value in row)
    except (TypeError, ValueError) as error:
        raise RedshiftIncrementalQualificationError(
            "Redshift incremental verification returned a non-integer"
        ) from error
    expected = (
        expected_rows,
        expected_rows,
        updated,
        inserted,
        config.delta_rows,
        config.seed_rows - updated,
        0,
    )
    if observed != expected:
        raise RedshiftIncrementalQualificationError(
            "Redshift incremental readback differs from the accepted workload"
        )
    return observed[2], observed[3]


def _advance_cursor(current: int | None, proposed: int) -> int:
    if current is not None and proposed < current:
        raise RedshiftIncrementalQualificationError(
            "Redshift incremental cursor regression rejected before provider mutation"
        )
    return proposed


def _require_copy_operations(operations: tuple[OperationTelemetry, ...], *, workload: str) -> None:
    if not operations or any(
        operation.transport is not WriteTransport.COPY for operation in operations
    ):
        raise RedshiftIncrementalQualificationError(
            f"Redshift {workload} did not use COPY for the complete workload"
        )
    if any(operation.retry_count != 0 for operation in operations):
        raise RedshiftIncrementalQualificationError(
            f"Redshift {workload} observed a provider-operation retry"
        )


def _seed_records(config: RedshiftIncrementalConfig) -> Iterator[dict[str, object]]:
    padding = "s" * config.payload_bytes
    for index in range(config.seed_rows):
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 1}


def _delta_records(config: RedshiftIncrementalConfig) -> Iterator[dict[str, object]]:
    updated = config.delta_rows // 2
    padding = "d" * config.payload_bytes
    for index in range(updated):
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 2}
    for offset in range(config.delta_rows - updated):
        index = config.seed_rows + offset
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 2}


def _report(
    config: RedshiftIncrementalConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _IncrementalResult,
) -> QualificationReport:
    ratio = Decimal(result.seed_rows) / Decimal(result.delta_rows)
    if ratio < 100:
        raise RedshiftIncrementalQualificationError(
            "Redshift incremental target is less than 100 times its delta"
        )
    if result.cursor_final < result.cursor_initial or result.cursor_regressions_rejected != 1:
        raise RedshiftIncrementalQualificationError(
            "Redshift incremental cursor evidence is incomplete"
        )
    cost_status = (
        ObjectiveStatus.PASSED
        if result.provider_cost_usd <= approval.cost_ceiling.amount_usd
        else ObjectiveStatus.FAILED
    )
    objectives = tuple(
        ObjectiveResult(
            name,
            cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
            f"phase8/aws/redshift/incremental/{name}",
        )
        for name in approval.objectives.names
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
            benchmark_class=BenchmarkClass.INCREMENTAL,
            input_rows=result.delta_rows,
            logical_input_bytes=result.delta_logical_bytes,
            row_width_bytes=config.payload_bytes + 32,
            schema_depth=1,
            source_rate_limit="unlimited_container_generator",
            transform_complexity="cursor_merge_small_delta",
            concurrency=1,
            batch_rows=config.copy_part_rows,
            batch_bytes=config.copy_part_logical_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.delta_rows),
            logical_bytes=measured("logical_bytes", "bytes", result.delta_logical_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(result.delta_rows, result.delta_duration_ms),
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
                measured("cursor_final", "cursor", result.cursor_final),
                measured("cursor_initial", "cursor", result.cursor_initial),
                measured(
                    "cursor_regressions_rejected", "count", result.cursor_regressions_rejected
                ),
                measured("delta_duration_ms", "milliseconds", result.delta_duration_ms),
                measured("delta_rows_affected", "rows", result.delta_rows_affected),
                measured("delta_target_ratio", "ratio", ratio),
                measured("final_target_rows", "rows", result.final_rows),
                measured("inserted_rows", "rows", result.inserted_rows),
                measured("maximum_compute_capacity", "rpu", result.maximum_compute_capacity_rpu),
                measured("provider_operation_retries", "count", 0),
                measured("regression_rows_affected", "rows", result.regression_rows_affected),
                measured("seed_duration_ms", "milliseconds", result.seed_duration_ms),
                measured("seed_logical_bytes", "bytes", result.seed_logical_bytes),
                measured("seed_rows", "rows", result.seed_rows),
                measured("spill_bytes", "bytes", result.spill_bytes),
                measured("staging_objects", "count", result.staging_objects),
                measured("staging_tables", "count", result.staging_tables),
                measured("updated_rows", "rows", result.updated_rows),
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
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=status,
    )


def _bulk_config(config: RedshiftIncrementalConfig) -> bulk.RedshiftBulkConfig:
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
        narrow_rows=config.seed_rows,
        narrow_payload_bytes=config.payload_bytes,
        wide_rows=config.seed_rows,
        wide_payload_bytes=config.payload_bytes,
        copy_part_rows=config.copy_part_rows,
        copy_part_logical_bytes=config.copy_part_logical_bytes,
        cost_observation_delay_seconds=config.cost_observation_delay_seconds,
        on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
    )


def _shared_config(config: RedshiftIncrementalConfig) -> shared.RedshiftQualificationConfig:
    return bulk._shared_config(_bulk_config(config))  # noqa: SLF001


def _require_no_provider_retries() -> None:
    if os.environ.get("AWS_MAX_ATTEMPTS") != "1":
        raise RedshiftIncrementalQualificationError("AWS_MAX_ATTEMPTS must be exactly 1")
    if os.environ.get("AWS_RETRY_MODE") != "standard":
        raise RedshiftIncrementalQualificationError("AWS_RETRY_MODE must be standard")


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
    config = RedshiftIncrementalConfig(
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
        result = run_phase8_redshift_incremental(
            config,
            identity=identity,
            approval=approval,
            defer_cost_attribution=True,
        )
        assert isinstance(result, _IncrementalResult)
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
            result_type=_IncrementalResult,
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
        report_candidate = run_phase8_redshift_incremental(
            config, identity=identity, approval=approval
        )
        assert isinstance(report_candidate, QualificationReport)
        report = report_candidate
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (RedshiftIncrementalQualificationError, ValueError, OSError) as error:
        print(f"Redshift incremental qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
