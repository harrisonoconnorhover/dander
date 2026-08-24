#!/usr/bin/env python3
"""Exact-RC31 Redshift failure-path Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import resource
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import psycopg

from dander import __version__
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
from dander.telemetry import CostAttribution, PerformanceMeasurement, RunPerformance
from scripts.benchmarks import redshift as shared
from scripts.benchmarks import redshift_bulk_phase8 as bulk

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from dander.telemetry import OperationTelemetry
    from dander.warehouse import WarehouseRuntime


class _RedshiftServerlessClient(Protocol):
    def get_credentials(self, **kwargs: object) -> Mapping[str, object]: ...


class _S3MutationClient(Protocol):
    def put_object(self, **kwargs: object) -> object: ...

    def delete_object(self, **kwargs: object) -> object: ...


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.redshift-failure/v1"
_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "credential_rejection",
    "failed_copy_cleanup",
    "provider_operation_recovery",
    "stale_publication_rejection",
)


class RedshiftFailureQualificationError(RuntimeError):
    """Raised with a credential-free Redshift failure-path summary."""


def _redshift_database_error() -> type[Exception]:
    """Load the selected Redshift driver's database-error boundary lazily."""
    error_module = importlib.import_module("redshift_connector.error")
    database_error = getattr(error_module, "DatabaseError", None)
    if not isinstance(database_error, type) or not issubclass(database_error, Exception):
        raise RedshiftFailureQualificationError(
            "Redshift connector database-error boundary is unavailable"
        )
    return database_error


@dataclass(frozen=True, slots=True)
class RedshiftFailureConfig:
    """Owned provider coordinates and the accepted bounded failure probes."""

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
    copy_part_rows: int = 1
    copy_part_logical_bytes: int = 16 * 1_024 * 1_024
    cost_observation_delay_seconds: int = 70
    cost_observation_timeout_seconds: int = 300
    cost_observation_poll_seconds: int = 60
    on_demand_rate_usd_per_rpu_hour: Decimal = Decimal("0.375")

    def __post_init__(self) -> None:
        if len(self.account_id) != 12 or not self.account_id.isdigit():
            raise ValueError("account_id must be a 12-digit AWS account id")
        for name in (
            "port",
            "connect_timeout_seconds",
            "statement_timeout_ms",
            "copy_part_rows",
            "copy_part_logical_bytes",
            "cost_observation_delay_seconds",
            "cost_observation_timeout_seconds",
            "cost_observation_poll_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.copy_part_rows != 1:
            raise ValueError("failure qualification must use one-row COPY parts")
        if self.cost_observation_delay_seconds < 60:
            raise ValueError("cost observation must wait for one complete provider interval")
        if self.cost_observation_timeout_seconds < self.cost_observation_delay_seconds:
            raise ValueError("cost observation timeout must include the initial delay")
        if (
            not self.on_demand_rate_usd_per_rpu_hour.is_finite()
            or self.on_demand_rate_usd_per_rpu_hour <= 0
        ):
            raise ValueError("Redshift on-demand rate must be a positive Decimal")
        bulk._provider_values(_bulk_config(self), schema_name="dander_p8_failure_check")  # noqa: SLF001

    def workload_payload(self) -> dict[str, object]:
        """Return the exact failure probes covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.FAILURE.value,
            "probes": [
                "credential_rejection",
                "failed_copy_cleanup",
                "provider_operation_recovery",
                "stale_publication_rejection",
            ],
            "recovery_rows": 1,
            "copy_part_rows": self.copy_part_rows,
            "copy_part_logical_bytes": self.copy_part_logical_bytes,
        }

    def configuration_sha256(self) -> str:
        encoded = json.dumps(
            self.workload_payload(), separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _FailureResult:
    duration_ms: int
    peak_rss_bytes: int
    probe_count: int
    credential_rejection_duration_ms: int
    failed_copy_cleanup_duration_ms: int
    provider_operation_recovery_duration_ms: int
    stale_publications_rejected: int
    concurrent_claim_attempts: int
    copy_operations: int
    query_ids: tuple[str, ...]
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
    config: RedshiftFailureConfig,
    identity: bulk.CandidateIdentity,
) -> bulk._Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective approval schema is incompatible")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective approval workload does not match the requested run")
    configuration = bulk._mapping(payload.get("configuration"), "configuration")  # noqa: SLF001
    provider = bulk._mapping(configuration.get("redshift"), "Redshift configuration")  # noqa: SLF001
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
    execution = bulk._mapping(configuration.get("execution"), "execution configuration")  # noqa: SLF001
    if execution.get("harness_sha256") != _file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    if execution.get("shared_harness_sha256") != _file_sha256(Path(shared.__file__)):
        raise ValueError("objective approval does not match the shared Redshift harness")
    if execution.get("bulk_harness_sha256") != _file_sha256(Path(bulk.__file__)):
        raise ValueError("objective approval does not match the protected Redshift dependency")
    if execution.get("manual_candidate_executions") != 1:
        raise ValueError("objective approval must allow exactly one candidate execution")
    if execution.get("automatic_candidate_retry") is not False:
        raise ValueError("objective approval must disable automatic candidate retry")
    if execution.get("provider_operation_retries") != 0:
        raise ValueError("objective approval must disable provider-operation retries")
    if execution.get("cost_observation_delay_seconds") != config.cost_observation_delay_seconds:
        raise ValueError("objective approval changed the provider cost observation")
    if execution.get("cost_observation_timeout_seconds") != config.cost_observation_timeout_seconds:
        raise ValueError("objective approval changed the provider cost observation timeout")
    if execution.get("cost_observation_poll_seconds") != config.cost_observation_poll_seconds:
        raise ValueError("objective approval changed the provider cost observation interval")
    fargate = bulk._mapping(  # noqa: SLF001
        configuration.get("fargate_harness"), "Fargate harness configuration"
    )
    expected_fargate = {
        "task_cpu_units": 2_048,
        "task_memory_mib": 4_096,
        "task_timeout_seconds": 900,
        "cluster_executions": 1,
        "state_machine_executions": 1,
        "state_machine_retry_states": 0,
        "ecs_task_retries": 0,
        "container_restarts": 0,
        "automatic_retry": False,
    }
    if any(fargate.get(name) != value for name, value in expected_fargate.items()):
        raise ValueError(
            "objective approval must use the exact zero-retry Fargate 2-vCPU/4-GiB shape"
        )
    objective_payload = bulk._mapping(  # noqa: SLF001
        payload.get("approved_objectives"), "approved objectives"
    )
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
        raise ValueError("objective approval names do not match Redshift failure qualification")
    if objectives.benchmark_class is not BenchmarkClass.FAILURE:
        raise ValueError("objective approval benchmark class is not failure")
    if (
        objectives.release_version != identity.release_version
        or objectives.git_commit != identity.git_commit
        or objectives.image_digest != identity.image_digest
        or objectives.approval_reference != identity.approval_reference
        or objectives.configuration_sha256 != config.configuration_sha256()
    ):
        raise ValueError("objective approval does not match the exact candidate")
    cost_payload = bulk._mapping(payload.get("cost_ceiling"), "cost ceiling")  # noqa: SLF001
    cost_ceiling = ApprovedCostCeiling(
        amount_usd=Decimal(str(cost_payload.get("amount_usd"))),
        approval_reference=str(cost_payload.get("approval_reference")),
    )
    if cost_ceiling.approval_reference != identity.approval_reference:
        raise ValueError("cost ceiling approval does not match the candidate approval")
    return bulk._Approval(  # noqa: SLF001
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


def run_phase8_redshift_failure(
    config: RedshiftFailureConfig,
    *,
    identity: bulk.CandidateIdentity,
    approval: bulk._Approval,
    credential_probe: Callable[[RedshiftFailureConfig], int] | None = None,
) -> QualificationReport:
    """Run the accepted failure class in one disposable Redshift schema."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    bulk._require_no_provider_retries()  # noqa: SLF001
    if approval.objectives.configuration_sha256 != config.configuration_sha256():
        raise ValueError("objective approval does not match the requested workload")
    suffix = uuid.uuid4().hex[:12]
    schema_name = f"dander_p8_failure_{suffix}"
    staging_prefix = f"{config.staging_prefix}/failure/{suffix}"
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    try:
        rejection_ms = (credential_probe or _probe_credential_rejection)(config)
    except RedshiftFailureQualificationError:
        raise
    except Exception as error:
        raise RedshiftFailureQualificationError(
            "Redshift credential-rejection probe failed before owned mutation"
        ) from error
    try:
        runtime = bulk._warehouse_runtime(  # noqa: SLF001
            _bulk_config(config), schema_name=schema_name, staging_prefix=staging_prefix
        )
    except Exception as error:
        try:
            shared._delete_prefix(_shared_config(config), staging_prefix)  # noqa: SLF001
        except Exception as prefix_cleanup_error:
            raise RedshiftFailureQualificationError(
                "Redshift failure qualification could not remove all owned resources"
            ) from prefix_cleanup_error
        raise RedshiftFailureQualificationError(
            "Redshift runtime construction failed after credential-rejection probe passed"
        ) from error
    result: _FailureResult | None = None
    failure: tuple[str, int, Exception] | None = None
    stage = "failed_copy_cleanup_and_recovery"
    stage_started = time.perf_counter()
    try:
        failed_copy_ms, recovery_ms, operations = _probe_failed_copy_cleanup_and_recovery(
            config, runtime, schema_name=schema_name, staging_prefix=staging_prefix
        )
        stage = "stale_publication_rejection"
        stage_started = time.perf_counter()
        stale_rejected, claim_attempts = shared._exercise_concurrent_fence(  # noqa: SLF001
            runtime, schema_name
        )
        if not stale_rejected or claim_attempts != 2:
            raise RedshiftFailureQualificationError(
                "Redshift stale-publication rejection differed from the accepted probe"
            )
        stage = "staging_residue_check"
        stage_started = time.perf_counter()
        staging_tables = bulk._staging_table_count(runtime, schema_name)  # noqa: SLF001
        staging_objects = shared._prefix_object_count(  # noqa: SLF001
            _shared_config(config), staging_prefix
        )
        if staging_tables or staging_objects:
            raise RedshiftFailureQualificationError(
                "Redshift failure qualification left run-scoped staging objects"
            )
        stage = "provider_cost_observation"
        stage_started = time.perf_counter()
        charged, compute, capacity = _observe_serverless_usage(runtime, config=config)
        if charged <= 0:
            raise RedshiftFailureQualificationError(
                "Redshift Serverless did not report charged provider usage"
            )
        provider_cost = (charged * config.on_demand_rate_usd_per_rpu_hour / Decimal(3600)).quantize(
            Decimal("0.000000000001"), rounding=ROUND_HALF_UP
        )
        result = _FailureResult(
            duration_ms=_elapsed_ms(started),
            peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
            probe_count=4,
            credential_rejection_duration_ms=rejection_ms,
            failed_copy_cleanup_duration_ms=failed_copy_ms,
            provider_operation_recovery_duration_ms=recovery_ms,
            stale_publications_rejected=1,
            concurrent_claim_attempts=claim_attempts,
            copy_operations=len(operations),
            query_ids=bulk._operation_query_ids(operations),  # noqa: SLF001
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
        failure = (stage, _elapsed_ms(stage_started), error)
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
            raise RedshiftFailureQualificationError(
                "Redshift failure qualification could not remove all owned resources"
            ) from cleanup_error
    cleanup = not shared._schema_exists(runtime, schema_name) and (  # noqa: SLF001
        shared._prefix_object_count(_shared_config(config), staging_prefix) == 0  # noqa: SLF001
    )
    if not cleanup:
        raise RedshiftFailureQualificationError(
            "Redshift failure qualification cleanup could not be verified"
        )
    if failure is not None:
        failed_stage, elapsed_ms, failure_error = failure
        raise RedshiftFailureQualificationError(
            f"stage={failed_stage}; elapsed_ms={elapsed_ms}; "
            f"exception_class={type(failure_error).__name__}"
        ) from None
    assert result is not None
    return _report(config, identity, approval, replace(result, cleanup_verified=True))


def _observe_serverless_usage(
    runtime: WarehouseRuntime,
    *,
    config: RedshiftFailureConfig,
) -> tuple[Decimal, Decimal, Decimal]:
    """Wait for delayed provider metadata without repeating the workload."""
    waited_seconds = 0
    next_delay = config.cost_observation_delay_seconds
    usage = (Decimal(0), Decimal(0), Decimal(0))
    while waited_seconds < config.cost_observation_timeout_seconds:
        delay = min(next_delay, config.cost_observation_timeout_seconds - waited_seconds)
        time.sleep(delay)
        waited_seconds += delay
        usage = bulk._serverless_usage(runtime)  # noqa: SLF001
        if usage[0] > 0:
            return usage
        next_delay = config.cost_observation_poll_seconds
    return usage


def _probe_credential_rejection(config: RedshiftFailureConfig) -> int:
    client = cast(
        "_RedshiftServerlessClient",
        _aws_client("redshift-serverless", region=config.region),
    )
    try:
        credentials = client.get_credentials(
            workgroupName=config.workgroup_name,
            dbName=config.database,
            durationSeconds=900,
        )
    except Exception as error:
        raise RedshiftFailureQualificationError(
            "Redshift failure qualification could not obtain ephemeral credentials"
        ) from error
    username = credentials.get("dbUser")
    password = credentials.get("dbPassword")
    if (
        not isinstance(username, str)
        or not username
        or not isinstance(password, str)
        or not password
    ):
        raise RedshiftFailureQualificationError(
            "Redshift failure qualification could not obtain ephemeral credentials"
        )
    rejected_password = hashlib.sha256(password.encode()).hexdigest()
    started = time.perf_counter()
    try:
        with psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=config.database,
            user=username,
            password=rejected_password,
            connect_timeout=min(config.connect_timeout_seconds, 15),
        ):
            pass
    except psycopg.OperationalError as error:
        message = str(error).lower()
        if (error.sqlstate is not None and error.sqlstate.startswith("28")) or (
            "authentication failed" in message
        ):
            return _elapsed_ms(started)
        raise RedshiftFailureQualificationError(
            "Redshift rejected credentials for an unexpected provider reason"
        ) from error
    raise RedshiftFailureQualificationError(
        "Redshift failure qualification accepted a rejected credential"
    )


def _probe_failed_copy_cleanup_and_recovery(
    config: RedshiftFailureConfig,
    runtime: WarehouseRuntime,
    *,
    schema_name: str,
    staging_prefix: str,
) -> tuple[int, int, tuple[OperationTelemetry, ...]]:
    invalid_key = f"{staging_prefix}/invalid/not-parquet.parquet"
    client = cast("_S3MutationClient", _aws_client("s3", region=config.region))
    client.put_object(
        Bucket=config.staging_bucket,
        Key=invalid_key,
        Body=b"not a parquet file",
        ServerSideEncryption="AES256",
    )
    failed_started = time.perf_counter()
    redshift_database_error = _redshift_database_error()
    try:
        with open_connection(bulk._connection_factory(runtime)) as connection:  # noqa: SLF001
            execute(
                connection,
                f"CREATE SCHEMA IF NOT EXISTS {bulk._qualified(schema_name)}",  # noqa: SLF001
            )
            execute(
                connection,
                f"CREATE TABLE {bulk._qualified(schema_name, 'failed_copy_records')} "  # noqa: SLF001
                '("id" VARCHAR(32) NOT NULL, "payload" VARCHAR(64) NOT NULL)',
            )
            connection.commit()
            try:
                execute(
                    connection,
                    f"COPY {bulk._qualified(schema_name, 'failed_copy_records')} "  # noqa: SLF001
                    f"FROM 's3://{config.staging_bucket}/{invalid_key}' "
                    f"IAM_ROLE '{config.copy_role_arn}' FORMAT AS PARQUET",
                )
            except (psycopg.DatabaseError, redshift_database_error):
                failed_ms = _elapsed_ms(failed_started)
                connection.rollback()
            else:
                raise RedshiftFailureQualificationError(
                    "Redshift failure qualification accepted an invalid COPY"
                )
            finally:
                execute(
                    connection,
                    f"DROP TABLE IF EXISTS {bulk._qualified(schema_name, 'failed_copy_records')}",  # noqa: SLF001
                )
                connection.commit()
            row = execute(
                connection,
                "SELECT COUNT(*) FROM svv_tables WHERE table_schema = %s AND table_name = %s",
                (schema_name, "failed_copy_records"),
                fetch="one",
            ).row
            if bulk._count(row) != 0:  # noqa: SLF001
                raise RedshiftFailureQualificationError(
                    "Redshift failed COPY left its disposable relation"
                )
    finally:
        client.delete_object(Bucket=config.staging_bucket, Key=invalid_key)
    if shared._prefix_object_count(_shared_config(config), staging_prefix):  # noqa: SLF001
        raise RedshiftFailureQualificationError(
            "Redshift failed COPY left a run-scoped staging object"
        )
    recovery_started = time.perf_counter()
    affected, _duration_ms, operations = bulk._write_table(  # noqa: SLF001
        runtime,
        config=_bulk_config(config),
        schema=schema_name,
        table="failure_records",
        pipeline_id="phase8_redshift_failure_recovery",
        rows=1,
        payload_bytes=8,
    )
    bulk._require_table_shape(  # noqa: SLF001
        runtime,
        config=_bulk_config(config),
        schema=schema_name,
        table="failure_records",
        rows=1,
        payload_bytes=8,
    )
    if affected != 1:
        raise RedshiftFailureQualificationError(
            "Redshift provider operation did not recover after the failed COPY"
        )
    return failed_ms, _elapsed_ms(recovery_started), operations


def _report(
    config: RedshiftFailureConfig,
    identity: bulk.CandidateIdentity,
    approval: bulk._Approval,
    result: _FailureResult,
) -> QualificationReport:
    if (
        result.probe_count != 4
        or result.stale_publications_rejected != 1
        or result.concurrent_claim_attempts != 2
        or result.copy_operations != 1
        or result.provider_operation_retries != 0
        or result.staging_tables != 0
        or result.staging_objects != 0
        or not result.cleanup_verified
    ):
        raise RedshiftFailureQualificationError("Redshift failure evidence is incomplete")
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
            benchmark_class=BenchmarkClass.FAILURE,
            input_rows=result.probe_count,
            logical_input_bytes=result.probe_count,
            row_width_bytes=1,
            schema_depth=1,
            source_rate_limit="controlled_provider_failure_injection",
            transform_complexity="credential_copy_recovery_and_fencing",
            concurrency=2,
            batch_rows=1,
            batch_bytes=config.copy_part_logical_bytes,
            memory_limit_bytes=None,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.probe_count),
            logical_bytes=measured("logical_bytes", "bytes", result.probe_count),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                bulk._throughput(result.probe_count, result.duration_ms),  # noqa: SLF001
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured("load_duration_ms", "milliseconds", 0),
            transform_duration_ms=measured("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measured("charged_seconds", "rpu_seconds", result.charged_seconds),
                measured("compute_seconds", "rpu_seconds", result.compute_seconds),
                measured("concurrent_claim_attempts", "count", result.concurrent_claim_attempts),
                measured("copy_operations", "count", result.copy_operations),
                measured(
                    "credential_rejection_duration_ms",
                    "milliseconds",
                    result.credential_rejection_duration_ms,
                ),
                measured(
                    "failed_copy_cleanup_duration_ms",
                    "milliseconds",
                    result.failed_copy_cleanup_duration_ms,
                ),
                measured(
                    "maximum_compute_capacity",
                    "rpu",
                    result.maximum_compute_capacity_rpu,
                ),
                measured("probe_count", "count", result.probe_count),
                measured(
                    "provider_operation_recovery_duration_ms",
                    "milliseconds",
                    result.provider_operation_recovery_duration_ms,
                ),
                measured(
                    "provider_operation_retries",
                    "count",
                    result.provider_operation_retries,
                ),
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
                f"phase8/aws/redshift/failure/{name}",
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


def _bulk_config(config: RedshiftFailureConfig) -> bulk.RedshiftBulkConfig:
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
        narrow_rows=1,
        narrow_payload_bytes=8,
        wide_rows=1,
        wide_payload_bytes=8,
        copy_part_rows=config.copy_part_rows,
        copy_part_logical_bytes=config.copy_part_logical_bytes,
        cost_observation_delay_seconds=config.cost_observation_delay_seconds,
        on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
    )


def _shared_config(config: RedshiftFailureConfig) -> shared.RedshiftQualificationConfig:
    return bulk._shared_config(_bulk_config(config))  # noqa: SLF001


def _peak_rss_bytes() -> int:
    return max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1_024, 1)


def _elapsed_ms(started: float) -> int:
    return max(int((time.perf_counter() - started) * 1_000), 1)


def _aws_client(service: str, *, region: str) -> object:
    boto3 = importlib.import_module("boto3")
    return cast("object", boto3.client(service, region_name=region))


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
    config = RedshiftFailureConfig(
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
    report = run_phase8_redshift_failure(config, identity=identity, approval=approval)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (RedshiftFailureQualificationError, ValueError, OSError) as error:
        print(f"Redshift failure qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
