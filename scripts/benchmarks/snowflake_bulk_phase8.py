#!/usr/bin/env python3
"""Exact-candidate Snowflake bulk-throughput Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dander import __version__
from dander.concurrency import FencingToken
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.snowflake.session import execute, open_connection
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
from dander.writer import SchemaEvolution, WriteField, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from dander.providers.snowflake.fence import SnowflakeTargetFence
    from dander.providers.snowflake.session import SnowflakeConnectionFactory


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.snowflake-bulk/v1"
_AUTHORITY_ID = "snowflake:phase8-bulk"
_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "narrow_copy_completion",
    "narrow_throughput_measurement",
    "wide_copy_completion",
    "wide_throughput_measurement",
)


class SnowflakeBulkQualificationError(RuntimeError):
    """Raised with a sanitized Snowflake bulk-qualification summary."""


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


def load_approval(path: Path, *, config: SnowflakeBulkConfig) -> _Approval:
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
    if tuple(raw_objectives.get("names", ())) != _OBJECTIVES:
        raise ValueError("objective manifest does not contain the required objective set")
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
    if objectives.benchmark_class is not BenchmarkClass.BULK_THROUGHPUT:
        raise ValueError("objective manifest benchmark class does not match")
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
        query_ids=tuple(
            dict.fromkeys(
                operation.query_id
                for operation in operations
                if isinstance(operation.query_id, str) and operation.query_id
            )
        )[:100],
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


def _require_identity_match(identity: CandidateIdentity, approval: _Approval) -> None:
    objectives = approval.objectives
    if (
        objectives.release_version != identity.release_version
        or objectives.git_commit != identity.git_commit
        or objectives.image_digest != identity.image_digest
        or objectives.approval_reference != identity.approval_reference
    ):
        raise ValueError("objective approval does not match the exact candidate identity")


def _require_provider_match(config: SnowflakeBulkConfig, approval: _Approval) -> None:
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


def _warehouse_runtime(config: SnowflakeBulkConfig, *, schema_name: str) -> WarehouseRuntime:
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


def _provider_values(config: SnowflakeBulkConfig, *, schema_name: str) -> dict[str, object]:
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
) -> tuple[int, int, tuple[OperationTelemetry, ...]]:
    relation = RelationRef(catalog=database, namespace=schema, name=table)
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


def _records(rows: int, payload_bytes: int) -> Iterator[dict[str, object]]:
    padding = "x" * payload_bytes
    for index in range(rows):
        yield {"id": f"{index:012d}", "payload": padding}


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
            "AND REGEXP_LIKE(TABLE_NAME, '^dander_stage_[0-9a-f]{20}$')",
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


def _throughput(rows: int, duration_ms: int) -> Decimal:
    return (Decimal(rows) * 1_000 / Decimal(max(duration_ms, 1))).quantize(Decimal("0.001"))


def _elapsed_ms(started: float) -> int:
    return max(round((time.perf_counter() - started) * 1_000), 1)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1_024)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        config = SnowflakeBulkConfig(
            account=arguments.account,
            user=arguments.user,
            database=arguments.database,
            warehouse=arguments.warehouse,
            role=arguments.role,
            auth_method=arguments.auth_method,
            token_env=arguments.token_env,
            private_key_file_env=arguments.private_key_file_env,
            private_key_password_env=arguments.private_key_password_env,
            narrow_rows=arguments.narrow_rows,
            narrow_payload_bytes=arguments.narrow_payload_bytes,
            wide_rows=arguments.wide_rows,
            wide_payload_bytes=arguments.wide_payload_bytes,
            copy_part_rows=arguments.copy_part_rows,
            copy_part_logical_bytes=arguments.copy_part_logical_bytes,
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
        report = run_phase8_snowflake_bulk(
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
                    "benchmark_class": BenchmarkClass.BULK_THROUGHPUT.value,
                    "status": QualificationStatus.FAILED.value,
                    "summary": (
                        "Snowflake bulk qualification failed; inspect provider logs and verify "
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
                "benchmark_class": BenchmarkClass.BULK_THROUGHPUT.value,
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
