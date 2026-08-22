#!/usr/bin/env python3
"""Exact-candidate Redshift bulk-throughput Phase 8 qualification."""

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
from dander.providers import ProviderKind, default_provider_registry
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

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from dander.providers.redshift.fence import RedshiftTargetFence
    from dander.providers.redshift.session import RedshiftConnectionFactory


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.redshift-bulk/v1"
_AUTHORITY_ID = "redshift:phase8-bulk"
_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "narrow_copy_completion",
    "narrow_throughput_measurement",
    "wide_copy_completion",
    "wide_throughput_measurement",
)


class RedshiftBulkQualificationError(RuntimeError):
    """Raised with a credential-free Redshift bulk-qualification summary."""


@dataclass(frozen=True, slots=True)
class RedshiftBulkConfig:
    """Non-secret Redshift coordinates and the accepted bulk workload."""

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
    narrow_rows: int = 500_000
    narrow_payload_bytes: int = 32
    wide_rows: int = 200_000
    wide_payload_bytes: int = 1_024
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
            "narrow_rows",
            "narrow_payload_bytes",
            "wide_rows",
            "wide_payload_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
            "cost_observation_delay_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.copy_part_rows > min(self.narrow_rows, self.wide_rows):
            raise ValueError("copy_part_rows must not exceed the smaller workload")
        if self.cost_observation_delay_seconds < 60:
            raise ValueError("cost observation must wait for one complete provider interval")
        if (
            not self.on_demand_rate_usd_per_rpu_hour.is_finite()
            or self.on_demand_rate_usd_per_rpu_hour <= 0
        ):
            raise ValueError("Redshift on-demand rate must be a positive Decimal")
        _provider_values(self, schema_name="dander_p8_bulk_check")

    def workload_payload(self) -> dict[str, object]:
        """Return the exact workload covered by objective approval."""
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
    secret_provider: str
    service_shapes: tuple[str, ...]
    provider_job_ids: tuple[str, ...]


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
    config: RedshiftBulkConfig,
    identity: CandidateIdentity,
) -> _Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != _APPROVAL_SCHEMA:
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
        raise ValueError("objective approval names do not match Redshift bulk qualification")
    if objectives.benchmark_class is not BenchmarkClass.BULK_THROUGHPUT:
        raise ValueError("objective approval benchmark class is not bulk throughput")
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


def run_phase8_redshift_bulk(
    config: RedshiftBulkConfig,
    *,
    identity: CandidateIdentity,
    approval: _Approval,
) -> QualificationReport:
    """Run the accepted bulk class in one disposable Redshift schema."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_no_provider_retries()
    if approval.objectives.configuration_sha256 != config.configuration_sha256():
        raise ValueError("objective approval does not match the requested workload")
    suffix = uuid.uuid4().hex[:12]
    schema_name = f"dander_p8_bulk_{suffix}"
    staging_prefix = f"{config.staging_prefix}/bulk/{suffix}"
    runtime = _warehouse_runtime(config, schema_name=schema_name, staging_prefix=staging_prefix)
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    result: _BulkResult | None = None
    failure: Exception | None = None
    try:
        narrow_rows, narrow_ms, narrow_operations = _write_table(
            runtime,
            config=config,
            schema=schema_name,
            table="narrow_records",
            pipeline_id="phase8_redshift_bulk_narrow",
            rows=config.narrow_rows,
            payload_bytes=config.narrow_payload_bytes,
        )
        wide_rows, wide_ms, wide_operations = _write_table(
            runtime,
            config=config,
            schema=schema_name,
            table="wide_records",
            pipeline_id="phase8_redshift_bulk_wide",
            rows=config.wide_rows,
            payload_bytes=config.wide_payload_bytes,
        )
        workload_duration_ms = _elapsed_ms(started)
        _require_table_shape(
            runtime,
            config=config,
            schema=schema_name,
            table="narrow_records",
            rows=config.narrow_rows,
            payload_bytes=config.narrow_payload_bytes,
        )
        _require_table_shape(
            runtime,
            config=config,
            schema=schema_name,
            table="wide_records",
            rows=config.wide_rows,
            payload_bytes=config.wide_payload_bytes,
        )
        staging_tables = _staging_table_count(runtime, schema_name)
        staging_objects = shared._prefix_object_count(  # noqa: SLF001
            _shared_config(config), staging_prefix
        )
        if staging_tables or staging_objects:
            raise RedshiftBulkQualificationError(
                "Redshift bulk qualification left run-scoped staging objects"
            )
        time.sleep(config.cost_observation_delay_seconds)
        charged, compute, capacity = _serverless_usage(runtime)
        if charged <= 0:
            raise RedshiftBulkQualificationError(
                "Redshift Serverless did not report charged provider usage"
            )
        provider_cost = (charged * config.on_demand_rate_usd_per_rpu_hour / Decimal(3600)).quantize(
            Decimal("0.000000000001"), rounding=ROUND_HALF_UP
        )
        operations = (*narrow_operations, *wide_operations)
        result = _BulkResult(
            duration_ms=workload_duration_ms,
            peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
            narrow_duration_ms=narrow_ms,
            narrow_rows=narrow_rows,
            narrow_logical_bytes=narrow_rows * (config.narrow_payload_bytes + 24),
            wide_duration_ms=wide_ms,
            wide_rows=wide_rows,
            wide_logical_bytes=wide_rows * (config.wide_payload_bytes + 24),
            copy_operations=sum(
                operation.operation is TelemetryOperation.LOAD for operation in operations
            ),
            query_ids=_operation_query_ids(operations),
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
            raise RedshiftBulkQualificationError(
                "Redshift bulk qualification could not remove all owned resources"
            ) from cleanup_error
    cleanup = not shared._schema_exists(runtime, schema_name) and (  # noqa: SLF001
        shared._prefix_object_count(_shared_config(config), staging_prefix) == 0  # noqa: SLF001
    )
    if not cleanup:
        raise RedshiftBulkQualificationError(
            "Redshift bulk qualification cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, (RedshiftBulkQualificationError, ValueError)):
            raise failure
        raise RedshiftBulkQualificationError(
            "Redshift bulk qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return _report(config, identity, approval, replace(result, cleanup_verified=True))


def _warehouse_runtime(
    config: RedshiftBulkConfig,
    *,
    schema_name: str,
    staging_prefix: str,
) -> WarehouseRuntime:
    registry = default_provider_registry()
    parsed = registry.parse(
        ProviderKind.WAREHOUSE,
        _provider_values(
            config,
            schema_name=schema_name,
            staging_prefix=staging_prefix,
        ),
    )
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        parsed,
        context={"catalog": config.database},
    )
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("Redshift provider returned an invalid warehouse runtime")
    return runtime


def _provider_values(
    config: RedshiftBulkConfig,
    *,
    schema_name: str,
    staging_prefix: str | None = None,
) -> dict[str, object]:
    return {
        "provider": "redshift",
        "deployment": "serverless",
        "host": config.host,
        "port": config.port,
        "database": config.database,
        "schema": schema_name,
        "region": config.region,
        "workgroup_name": config.workgroup_name,
        "copy_role_arn": config.copy_role_arn,
        "staging_bucket": config.staging_bucket,
        "staging_prefix": staging_prefix or config.staging_prefix,
        "connect_timeout_seconds": config.connect_timeout_seconds,
        "statement_timeout_ms": config.statement_timeout_ms,
        "max_rows_per_file": config.copy_part_rows,
        "max_logical_bytes_per_file": config.copy_part_logical_bytes,
        "direct_max_rows": 0,
        "direct_max_logical_bytes": 0,
    }


def _shared_config(config: RedshiftBulkConfig) -> shared.RedshiftQualificationConfig:
    return shared.RedshiftQualificationConfig(
        deployment="serverless",
        host=config.host,
        database=config.database,
        region=config.region,
        copy_role_arn=config.copy_role_arn,
        staging_bucket=config.staging_bucket,
        workgroup_name=config.workgroup_name,
        port=config.port,
        staging_prefix=config.staging_prefix,
        direct_max_rows=1,
        direct_max_logical_bytes=1,
        copy_part_rows=config.copy_part_rows,
        copy_part_logical_bytes=config.copy_part_logical_bytes,
    )


def _write_table(
    runtime: WarehouseRuntime,
    *,
    config: RedshiftBulkConfig,
    schema: str,
    table: str,
    pipeline_id: str,
    rows: int,
    payload_bytes: int,
) -> tuple[int, int, tuple[OperationTelemetry, ...]]:
    relation = RelationRef(catalog=config.database, namespace=schema, name=table)
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=pipeline_id,
            run_id=f"{pipeline_id}-one",
            token=1,
            authority_id=_AUTHORITY_ID,
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=config.copy_part_rows,
        schema_evolution=SchemaEvolution.STRICT,
        mode=WriteMode.REPLACE,
    )
    if not isinstance(writer, RedshiftStagedWriter):
        raise RedshiftBulkQualificationError(
            "Redshift bulk qualification did not select the staged writer"
        )
    target = WriteTarget(
        relation=relation,
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="payload", data_type="STRING", mode="REQUIRED"),
        ),
        publication_fence=publication,
    )
    started = time.perf_counter()
    affected = writer.write(_records(rows, payload_bytes), target)
    operations = writer.drain_telemetry()
    _require_copy_operations(operations)
    if affected != rows:
        raise RedshiftBulkQualificationError("Redshift bulk write affected an unexpected row count")
    return affected, _elapsed_ms(started), operations


def _require_copy_operations(operations: tuple[OperationTelemetry, ...]) -> None:
    if not operations or any(
        operation.transport is not WriteTransport.COPY for operation in operations
    ):
        raise RedshiftBulkQualificationError(
            "Redshift bulk qualification did not use COPY for the complete workload"
        )
    if any(operation.retry_count != 0 for operation in operations):
        raise RedshiftBulkQualificationError(
            "Redshift bulk qualification observed a provider-operation retry"
        )


def _records(rows: int, payload_bytes: int) -> Iterator[dict[str, object]]:
    padding = "x" * payload_bytes
    for index in range(rows):
        yield {"id": f"{index:012d}", "payload": padding}


def _require_table_shape(
    runtime: WarehouseRuntime,
    *,
    config: RedshiftBulkConfig,
    schema: str,
    table: str,
    rows: int,
    payload_bytes: int,
) -> None:
    with open_connection(_connection_factory(runtime)) as connection:
        result = execute(
            connection,
            f'SELECT COUNT(*), COUNT(DISTINCT "id"), '
            f'MIN(LEN("payload")), MAX(LEN("payload")) '
            f"FROM {_qualified(schema, table)}",
            fetch="one",
        ).row
    if not isinstance(result, (tuple, list)) or len(result) != 4:
        raise RedshiftBulkQualificationError("Redshift bulk readback was malformed")
    if tuple(int(value) for value in result) != (rows, rows, payload_bytes, payload_bytes):
        raise RedshiftBulkQualificationError("Redshift bulk readback differs from the workload")


def _staging_table_count(runtime: WarehouseRuntime, schema_name: str) -> int:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(
            connection,
            "SELECT COUNT(*) FROM svv_tables WHERE table_schema = %s "
            "AND table_name ~ '^dander_stage_[0-9a-f]{24}$'",
            (schema_name,),
            fetch="one",
        ).row
    return _count(row)


def _serverless_usage(runtime: WarehouseRuntime) -> tuple[Decimal, Decimal, Decimal]:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(
            connection,
            "SELECT COALESCE(SUM(charged_seconds), 0), "
            "COALESCE(SUM(compute_seconds), 0), "
            "COALESCE(MAX(compute_capacity), 0) FROM sys_serverless_usage",
            fetch="one",
        ).row
    if not isinstance(row, (tuple, list)) or len(row) != 3:
        raise RedshiftBulkQualificationError("Redshift provider usage readback was malformed")
    try:
        values = tuple(Decimal(str(value)) for value in row)
    except Exception as error:
        raise RedshiftBulkQualificationError(
            "Redshift provider usage readback was not numeric"
        ) from error
    if any(not value.is_finite() or value < 0 for value in values):
        raise RedshiftBulkQualificationError("Redshift provider usage readback was invalid")
    return cast("tuple[Decimal, Decimal, Decimal]", values)


def _report(
    config: RedshiftBulkConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _BulkResult,
) -> QualificationReport:
    rows = result.narrow_rows + result.wide_rows
    logical_bytes = result.narrow_logical_bytes + result.wide_logical_bytes
    cost_status = (
        ObjectiveStatus.PASSED
        if result.provider_cost_usd <= approval.cost_ceiling.amount_usd
        else ObjectiveStatus.FAILED
    )
    objectives = tuple(
        ObjectiveResult(
            name,
            cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
            f"phase8/aws/redshift/bulk/{name}",
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
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
            input_rows=rows,
            logical_input_bytes=logical_bytes,
            row_width_bytes=config.wide_payload_bytes + 24,
            schema_depth=1,
            source_rate_limit="unlimited_container_generator",
            transform_complexity="none_wide_and_narrow",
            concurrency=1,
            batch_rows=config.copy_part_rows,
            batch_bytes=config.copy_part_logical_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", rows),
            logical_bytes=measured("logical_bytes", "bytes", logical_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(rows, result.duration_ms),
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
                measured("narrow_duration_ms", "milliseconds", result.narrow_duration_ms),
                measured("narrow_logical_bytes", "bytes", result.narrow_logical_bytes),
                measured("narrow_rows", "rows", result.narrow_rows),
                measured(
                    "narrow_throughput_rows_per_second",
                    "rows_per_second",
                    _throughput(result.narrow_rows, result.narrow_duration_ms),
                ),
                measured("provider_operation_retries", "count", 0),
                measured("spill_bytes", "bytes", result.spill_bytes),
                measured("staging_objects", "count", result.staging_objects),
                measured("staging_tables", "count", result.staging_tables),
                measured("wide_duration_ms", "milliseconds", result.wide_duration_ms),
                measured("wide_logical_bytes", "bytes", result.wide_logical_bytes),
                measured("wide_rows", "rows", result.wide_rows),
                measured(
                    "wide_throughput_rows_per_second",
                    "rows_per_second",
                    _throughput(result.wide_rows, result.wide_duration_ms),
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
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=status,
    )


def _connection_factory(runtime: WarehouseRuntime) -> RedshiftConnectionFactory:
    return cast("RedshiftTargetFence", runtime.target_fence).connection_factory


def _operation_query_ids(operations: Sequence[OperationTelemetry]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            operation.query_id
            for operation in operations
            if isinstance(operation.query_id, str) and operation.query_id
        )
    )[:100]


def _require_no_provider_retries() -> None:
    if os.environ.get("AWS_MAX_ATTEMPTS") != "1":
        raise RedshiftBulkQualificationError("AWS_MAX_ATTEMPTS must be exactly 1")
    if os.environ.get("AWS_RETRY_MODE") != "standard":
        raise RedshiftBulkQualificationError("AWS_RETRY_MODE must be standard")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast("Mapping[str, object]", value)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _count(row: object) -> int:
    if not isinstance(row, (tuple, list)) or not row:
        raise RedshiftBulkQualificationError("Redshift count query was malformed")
    try:
        return int(row[0])
    except (TypeError, ValueError) as error:
        raise RedshiftBulkQualificationError(
            "Redshift count query returned a non-integer"
        ) from error


def _qualified(*parts: str) -> str:
    return ".".join(f'"{part.replace(chr(34), chr(34) * 2)}"' for part in parts)


def _throughput(rows: int, duration_ms: int) -> Decimal:
    return (Decimal(rows) * 1_000 / Decimal(max(duration_ms, 1))).quantize(Decimal("0.001"))


def _elapsed_ms(started: float) -> int:
    return max(round((time.perf_counter() - started) * 1_000), 1)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1_024)


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
    parser.add_argument("--launcher", default="docker_local")
    parser.add_argument("--secret-provider", default="aws_sso_profile")
    parser.add_argument("--service-shape", action="append", required=True)
    parser.add_argument("--provider-job-id", action="append", required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    config = RedshiftBulkConfig(
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
    report = run_phase8_redshift_bulk(config, identity=identity, approval=approval)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (RedshiftBulkQualificationError, ValueError, OSError) as error:
        print(f"Redshift bulk qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
