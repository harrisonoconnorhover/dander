#!/usr/bin/env python3
"""Opt-in Snowflake warehouse qualification using one disposable schema."""

from __future__ import annotations

import argparse
import json
import platform
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dander.concurrency import FencingToken, TargetFenceLostError
from dander.ingestion import Endpoint, RawField, SourceConfig
from dander.pipeline.graph import PipelineGraph
from dander.pipeline.runtime import GraphExecutionPlan, plan_graph_execution
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.snowflake.session import execute, open_connection
from dander.warehouse import RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dander.providers.snowflake.fence import SnowflakeTargetFence
    from dander.providers.snowflake.session import SnowflakeConnectionFactory
    from dander.telemetry import OperationTelemetry
    from dander.writer import WritePattern


_REPORT_SCHEMA = "io.dander.benchmark.snowflake/v1"
_AUTHORITY_ID = "snowflake:qualification"


class SnowflakeQualificationError(RuntimeError):
    """Raised with a sanitized qualification failure summary."""


@dataclass(frozen=True, slots=True)
class SnowflakeQualificationConfig:
    """Non-secret live-profile coordinates and bounded workload controls."""

    account: str
    user: str
    database: str
    warehouse: str
    role: str | None = None
    auth_method: str = "key_pair"
    token_env: str = "DANDER_SNOWFLAKE_OAUTH_TOKEN"
    private_key_file_env: str = "DANDER_SNOWFLAKE_PRIVATE_KEY_FILE"
    private_key_password_env: str | None = None
    direct_max_rows: int = 2
    direct_max_logical_bytes: int = 1_048_576
    copy_part_rows: int = 2
    copy_part_logical_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if self.auth_method not in {"key_pair", "oauth"}:
            raise ValueError("auth_method must be key_pair or oauth")
        for name in (
            "direct_max_rows",
            "direct_max_logical_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        # Reuse the actual provider parser so identifiers and credential references fail before I/O.
        default_provider_registry().parse(
            ProviderKind.WAREHOUSE,
            _provider_values(self, direct=True, schema_name="DANDER_QUALIFICATION_CHECK"),
        )


@dataclass(frozen=True, slots=True)
class SnowflakeQualificationReport:
    """Sanitized correctness and bounded-work evidence from one disposable schema."""

    schema: str
    provider: str
    provider_version: str
    python_version: str
    warehouse: str
    disposable_schema: str
    duration_seconds: float
    direct_rows: int
    copy_rows: int
    write_modes: tuple[str, ...]
    graph_rows: int
    replay_duplicate_free: bool
    cursor_monotonic: bool
    stale_publication_rejected: bool
    concurrent_claim_attempts: int
    direct_operations: int
    copy_operations: int
    query_ids: tuple[str, ...]
    staging_tables: int
    staging_stages: int
    schema_cleanup_verified: bool
    qualification_status: str = "passed"
    support_status: str = "experimental"
    cost_status: str = "not_measured"

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


@dataclass(slots=True)
class _Ownership:
    fence: FencingToken
    verifications: int = 0

    def verify(self) -> None:
        self.verifications += 1


def run_snowflake_qualification(
    config: SnowflakeQualificationConfig,
) -> SnowflakeQualificationReport:
    """Exercise direct/COPY writes, replay, fencing, graph execution, and cleanup."""
    suffix = uuid.uuid4().hex[:12].upper()
    schema_name = f"DANDER_QUAL_{suffix}"
    direct = _warehouse_runtime(config, schema_name=schema_name, direct=True)
    copy = _warehouse_runtime(config, schema_name=schema_name, direct=False)
    started = time.perf_counter()
    try:
        report = _run_profile(config, direct=direct, copy=copy, schema_name=schema_name)
    except (TargetFenceLostError, SnowflakeQualificationError):
        raise
    except Exception as error:
        raise SnowflakeQualificationError(
            f"Snowflake qualification failed in disposable schema {schema_name}"
        ) from error
    finally:
        _drop_schema(direct, config.database, schema_name)
    if _schema_exists(direct, config.database, schema_name):
        raise SnowflakeQualificationError(
            f"Snowflake qualification cleanup left disposable schema {schema_name}"
        )
    return replace(
        report,
        duration_seconds=round(max(time.perf_counter() - started, 1e-9), 6),
        schema_cleanup_verified=True,
    )


def _run_profile(
    config: SnowflakeQualificationConfig,
    *,
    direct: WarehouseRuntime,
    copy: WarehouseRuntime,
    schema_name: str,
) -> SnowflakeQualificationReport:
    database = config.database
    all_operations: list[OperationTelemetry] = []

    direct_relation = RelationRef(
        catalog=database,
        namespace=schema_name,
        name="direct_records",
    )
    direct_writer, direct_target = _writer_target(
        direct,
        relation=direct_relation,
        mode=WriteMode.SCD1,
        pipeline_id="snowflake_qualification_direct",
        run_id="direct-1",
        token=1,
        business_key=("id",),
        fields=_base_fields(),
    )
    direct_rows = (
        {"id": "one", "label": "first"},
        {"id": "two", "label": "second"},
    )
    direct_writer.write(direct_rows, direct_target)
    direct_operations = direct_writer.drain_telemetry()
    _require_transport(direct_operations, WriteTransport.DIRECT)
    all_operations.extend(direct_operations)
    expected_direct = (("one", "first"), ("two", "second"))
    _require_rows(direct, direct_relation, ("id", "label"), expected_direct)
    direct_writer.write(direct_rows, direct_target)
    all_operations.extend(direct_writer.drain_telemetry())
    _require_rows(direct, direct_relation, ("id", "label"), expected_direct)

    overflow_relation = RelationRef(
        catalog=database,
        namespace=schema_name,
        name="threshold_fallback_records",
    )
    overflow_writer, overflow_target = _writer_target(
        direct,
        relation=overflow_relation,
        mode=WriteMode.SCD1,
        pipeline_id="snowflake_qualification_threshold",
        run_id="threshold-1",
        token=1,
        business_key=("id",),
        fields=_base_fields(),
    )
    overflow_writer.write(
        (
            {"id": "one", "label": "first"},
            {"id": "two", "label": "second"},
            {"id": "three", "label": "third"},
        ),
        overflow_target,
    )
    overflow_operations = overflow_writer.drain_telemetry()
    _require_transport(overflow_operations, WriteTransport.COPY)
    all_operations.extend(overflow_operations)

    copy_relation = RelationRef(catalog=database, namespace=schema_name, name="scd1_records")
    copy_writer, copy_target = _writer_target(
        copy,
        relation=copy_relation,
        mode=WriteMode.SCD1,
        pipeline_id="snowflake_qualification_scd1",
        run_id="scd1-1",
        token=1,
        business_key=("id",),
        fields=_base_fields(),
    )
    copy_writer.write(
        (
            {"id": "one", "label": "older"},
            {"id": "two", "label": "second"},
            {"id": "one", "label": "newer"},
        ),
        copy_target,
    )
    copy_operations = copy_writer.drain_telemetry()
    _require_transport(copy_operations, WriteTransport.COPY)
    all_operations.extend(copy_operations)
    _require_rows(copy, copy_relation, ("id", "label"), (("one", "newer"), ("two", "second")))

    incremental_relation = RelationRef(
        catalog=database,
        namespace=schema_name,
        name="incremental_records",
    )
    incremental_fields = (*_base_fields(), WriteField(name="updated_at", data_type="INT64"))
    incremental_writer, incremental_target = _writer_target(
        copy,
        relation=incremental_relation,
        mode=WriteMode.INCREMENTAL,
        pipeline_id="snowflake_qualification_incremental",
        run_id="incremental-1",
        token=1,
        business_key=("id",),
        fields=incremental_fields,
        cursor_field="updated_at",
    )
    incremental_writer.write(
        (
            {"id": "one", "label": "newest", "updated_at": 2},
            {"id": "one", "label": "stale", "updated_at": 1},
        ),
        incremental_target,
    )
    all_operations.extend(incremental_writer.drain_telemetry())
    _require_rows(
        copy,
        incremental_relation,
        ("id", "label", "updated_at"),
        (("one", "newest", 2),),
    )
    regression_writer, regression_target = _writer_target(
        copy,
        relation=incremental_relation,
        mode=WriteMode.INCREMENTAL,
        pipeline_id="snowflake_qualification_incremental",
        run_id="incremental-2",
        token=2,
        business_key=("id",),
        fields=incremental_fields,
        cursor_field="updated_at",
    )
    regression_writer.write(
        ({"id": "one", "label": "regressed", "updated_at": 1},),
        regression_target,
    )
    all_operations.extend(regression_writer.drain_telemetry())
    _require_rows(
        copy,
        incremental_relation,
        ("id", "label", "updated_at"),
        (("one", "newest", 2),),
    )

    snapshot_relation = RelationRef(
        catalog=database,
        namespace=schema_name,
        name="snapshot_records",
    )
    snapshot_fields = (*_base_fields(), WriteField(name="snapshot_at", data_type="STRING"))
    snapshot_writer, snapshot_target = _writer_target(
        copy,
        relation=snapshot_relation,
        mode=WriteMode.SNAPSHOT,
        pipeline_id="snowflake_qualification_snapshot",
        run_id="snapshot-1",
        token=1,
        fields=snapshot_fields,
        snapshot_field="snapshot_at",
    )
    snapshot_rows = (
        {"id": "one", "label": "first", "snapshot_at": "2026-08-10"},
        {"id": "two", "label": "second", "snapshot_at": "2026-08-10"},
    )
    snapshot_writer.write(snapshot_rows, snapshot_target)
    all_operations.extend(snapshot_writer.drain_telemetry())
    snapshot_writer.write(snapshot_rows, snapshot_target)
    all_operations.extend(snapshot_writer.drain_telemetry())
    _require_count(copy, snapshot_relation, 2)

    scd2_relation = RelationRef(catalog=database, namespace=schema_name, name="scd2_records")
    scd2_writer, scd2_target = _writer_target(
        copy,
        relation=scd2_relation,
        mode=WriteMode.SCD2,
        pipeline_id="snowflake_qualification_scd2",
        run_id="scd2-1",
        token=1,
        business_key=("id",),
        fields=_base_fields(),
    )
    scd2_writer.write(({"id": "one", "label": "first"},), scd2_target)
    all_operations.extend(scd2_writer.drain_telemetry())
    scd2_update, scd2_update_target = _writer_target(
        copy,
        relation=scd2_relation,
        mode=WriteMode.SCD2,
        pipeline_id="snowflake_qualification_scd2",
        run_id="scd2-2",
        token=2,
        business_key=("id",),
        fields=_base_fields(),
    )
    scd2_update.write(({"id": "one", "label": "second"},), scd2_update_target)
    all_operations.extend(scd2_update.drain_telemetry())
    scd2_rows = _select(
        copy,
        scd2_relation,
        ("label", "is_current"),
        order_by=("valid_from",),
    )
    if len(scd2_rows) != 2 or sum(bool(row[1]) for row in scd2_rows) != 1:
        raise SnowflakeQualificationError("Snowflake SCD2 readback did not retain one current row")

    replace_relation = RelationRef(
        catalog=database,
        namespace=schema_name,
        name="replace_records",
    )
    replace_writer, replace_target = _writer_target(
        copy,
        relation=replace_relation,
        mode=WriteMode.REPLACE,
        pipeline_id="snowflake_qualification_replace",
        run_id="replace-1",
        token=1,
        fields=_base_fields(),
    )
    replace_writer.write(direct_rows, replace_target)
    all_operations.extend(replace_writer.drain_telemetry())
    replace_writer.write(direct_rows, replace_target)
    all_operations.extend(replace_writer.drain_telemetry())
    replacement_writer, replacement_target = _writer_target(
        copy,
        relation=replace_relation,
        mode=WriteMode.REPLACE,
        pipeline_id="snowflake_qualification_replace",
        run_id="replace-2",
        token=2,
        fields=_base_fields(),
    )
    replacement_writer.write(
        ({"id": "three", "label": "replacement"},),
        replacement_target,
    )
    all_operations.extend(replacement_writer.drain_telemetry())
    _require_rows(copy, replace_relation, ("id", "label"), (("three", "replacement"),))

    stale_rejected, concurrent_attempts = _exercise_concurrent_fence(copy, schema_name)
    graph_rows, graph_operations = _exercise_graph(copy, schema_name, source=direct_relation)
    all_operations.extend(graph_operations)
    staging_tables, staging_stages = _staging_residue(copy, database, schema_name)
    if staging_tables or staging_stages:
        raise SnowflakeQualificationError("Snowflake qualification left run-scoped staging objects")

    provider_version = _scalar(copy, "SELECT CURRENT_VERSION()")
    query_ids = tuple(
        dict.fromkeys(
            operation.query_id
            for operation in all_operations
            if isinstance(operation.query_id, str) and operation.query_id
        )
    )[:100]
    copy_count = sum(operation.transport is WriteTransport.COPY for operation in all_operations)
    direct_count = sum(operation.transport is WriteTransport.DIRECT for operation in all_operations)
    return SnowflakeQualificationReport(
        schema=_REPORT_SCHEMA,
        provider="snowflake",
        provider_version=str(provider_version),
        python_version=platform.python_version(),
        warehouse=config.warehouse,
        disposable_schema=schema_name,
        duration_seconds=0.0,
        direct_rows=len(expected_direct),
        copy_rows=2,
        write_modes=tuple(mode.value for mode in WriteMode),
        graph_rows=graph_rows,
        replay_duplicate_free=True,
        cursor_monotonic=True,
        stale_publication_rejected=stale_rejected,
        concurrent_claim_attempts=concurrent_attempts,
        direct_operations=direct_count,
        copy_operations=copy_count,
        query_ids=query_ids,
        staging_tables=staging_tables,
        staging_stages=staging_stages,
        schema_cleanup_verified=False,
    )


def _writer_target(
    runtime: WarehouseRuntime,
    *,
    relation: RelationRef,
    mode: WriteMode,
    pipeline_id: str,
    run_id: str,
    token: int,
    fields: Sequence[WriteField],
    business_key: tuple[str, ...] = (),
    cursor_field: str | None = None,
    snapshot_field: str | None = None,
) -> tuple[WritePattern, WriteTarget]:
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=pipeline_id,
            run_id=run_id,
            token=token,
            authority_id=_AUTHORITY_ID,
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=mode,
        cursor_field=cursor_field,
        snapshot_field=snapshot_field,
    )
    return writer, WriteTarget(
        relation=relation,
        business_key=business_key,
        schema=tuple(fields),
        publication_fence=publication,
    )


def _exercise_concurrent_fence(
    runtime: WarehouseRuntime,
    schema_name: str,
) -> tuple[bool, int]:
    target_fence = cast("SnowflakeTargetFence", runtime.target_fence)
    relation = RelationRef(
        catalog=target_fence.database,
        namespace=schema_name,
        name="concurrent_records",
    )
    old_token = FencingToken(
        lease_table=None,
        pipeline_id="snowflake_qualification_concurrency",
        run_id="concurrent-old",
        token=20,
        authority_id=_AUTHORITY_ID,
    )
    old_publication = target_fence.claim(relation, old_token)
    newer_token = replace(old_token, run_id="concurrent-new", token=21)
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
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    try:
        writer.write(
            ({"id": "stale", "label": "must-not-publish"},),
            WriteTarget(
                relation=relation,
                business_key=("id",),
                schema=_base_fields(),
                publication_fence=old_publication,
            ),
        )
    except TargetFenceLostError:
        _require_count(runtime, relation, 0)
        return True, len(futures)
    raise SnowflakeQualificationError("Snowflake accepted a stale concurrent publication")


def _exercise_graph(
    runtime: WarehouseRuntime,
    schema_name: str,
    *,
    source: RelationRef,
) -> tuple[int, tuple[OperationTelemetry, ...]]:
    plan = _graph_plan(source=source, target_schema=schema_name)
    runner = runtime.transforms.build_transform_runner(graph_plan=plan, build_models=True)
    if runner is None:
        raise SnowflakeQualificationError("Snowflake graph runner is unavailable")
    ownership = _Ownership(
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_qualification_graph",
            run_id="graph-1",
            token=1,
            authority_id=_AUTHORITY_ID,
        )
    )
    result = runner.build(Path("."), ownership=ownership)
    if result.models != ("target",) or ownership.verifications < 2:
        raise SnowflakeQualificationError("Snowflake graph ownership verification was incomplete")
    target = RelationRef(
        catalog=source.catalog,
        namespace=schema_name,
        name="graph_records",
    )
    rows = _select(runtime, target, ("id", "label"), order_by=("id",))
    if rows != (("one", "first"), ("two", "second")):
        raise SnowflakeQualificationError("Snowflake graph result differs from its source fixture")
    return len(rows), result.telemetry


def _graph_plan(*, source: RelationRef, target_schema: str) -> GraphExecutionPlan:
    source_config = SourceConfig(
        name="snowflake_qualification",
        base_url="https://qualification.invalid",
        auth_strategy="none",
        endpoints=[
            Endpoint(
                name="records",
                path="/records",
                primary_key=["id"],
                raw_schema=[
                    RawField(name="id", data_type="STRING"),
                    RawField(name="label", data_type="STRING"),
                ],
            )
        ],
    )
    graph = PipelineGraph.model_validate(
        {
            "name": "snowflake_qualification_graph",
            "nodes": [
                {
                    "id": "records",
                    "type": "source",
                    "name": "Records",
                    "config": {"connector": "snowflake_qualification", "endpoint": "records"},
                    "fields": [
                        {"name": "id", "type": "STRING"},
                        {"name": "label", "type": "STRING"},
                    ],
                },
                {
                    "id": "target",
                    "type": "target",
                    "name": "Target",
                    "config": {
                        "writer": {
                            "write_mode": "replace",
                            "destination": {
                                "dataset": target_schema,
                                "table": "graph_records",
                                "business_key": [],
                            },
                        }
                    },
                    "fields": [
                        {"name": "id", "type": "STRING"},
                        {"name": "label", "type": "STRING"},
                    ],
                },
            ],
            "edges": [
                {
                    "from": "records",
                    "to": "target",
                    "mappings": [
                        {"source": "id", "target": "id"},
                        {"source": "label", "target": "label"},
                    ],
                }
            ],
        }
    )
    return plan_graph_execution(graph, source_config, endpoint_relations={"records": source})


def _warehouse_runtime(
    config: SnowflakeQualificationConfig,
    *,
    schema_name: str,
    direct: bool,
) -> WarehouseRuntime:
    registry = default_provider_registry()
    parsed = registry.parse(
        ProviderKind.WAREHOUSE,
        _provider_values(config, direct=direct, schema_name=schema_name),
    )
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        parsed,
        context={"catalog": config.database},
    )
    if not isinstance(runtime, WarehouseRuntime):
        raise TypeError("Snowflake provider returned an invalid warehouse runtime")
    return runtime


def _provider_values(
    config: SnowflakeQualificationConfig,
    *,
    direct: bool,
    schema_name: str,
) -> dict[str, object]:
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
        "direct_max_rows": config.direct_max_rows if direct else 0,
        "direct_max_logical_bytes": config.direct_max_logical_bytes if direct else 0,
    }


def _base_fields() -> tuple[WriteField, ...]:
    return (
        WriteField(name="id", data_type="STRING"),
        WriteField(name="label", data_type="STRING"),
    )


def _require_transport(
    operations: Sequence[OperationTelemetry],
    transport: WriteTransport,
) -> None:
    if not operations or any(operation.transport is not transport for operation in operations):
        raise SnowflakeQualificationError(
            f"Snowflake qualification did not use required {transport.value} transport"
        )


def _connection_factory(runtime: WarehouseRuntime) -> SnowflakeConnectionFactory:
    target_fence = cast("SnowflakeTargetFence", runtime.target_fence)
    return target_fence.connection_factory


def _select(
    runtime: WarehouseRuntime,
    relation: RelationRef,
    fields: Sequence[str],
    *,
    order_by: Sequence[str] = (),
) -> tuple[tuple[object, ...], ...]:
    columns = ", ".join(_quote(field) for field in fields)
    ordering = f" ORDER BY {', '.join(_quote(field) for field in order_by)}" if order_by else ""
    statement = f"SELECT {columns} FROM {_relation(relation)}{ordering}"
    with open_connection(_connection_factory(runtime)) as connection:
        rows = execute(connection, statement, fetch="all").rows
    return tuple(tuple(row) for row in rows if isinstance(row, (tuple, list)))


def _require_rows(
    runtime: WarehouseRuntime,
    relation: RelationRef,
    fields: Sequence[str],
    expected: tuple[tuple[object, ...], ...],
) -> None:
    actual = _select(runtime, relation, fields, order_by=(fields[0],))
    if actual != expected:
        raise SnowflakeQualificationError(
            f"Snowflake readback mismatch for qualification relation {relation.name}"
        )


def _require_count(runtime: WarehouseRuntime, relation: RelationRef, expected: int) -> None:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(connection, f"SELECT COUNT(*) FROM {_relation(relation)}", fetch="one").row
    value = row[0] if isinstance(row, (tuple, list)) and row else None
    if value != expected:
        raise SnowflakeQualificationError(
            f"Snowflake row-count mismatch for qualification relation {relation.name}"
        )


def _scalar(runtime: WarehouseRuntime, statement: str) -> object:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(connection, statement, fetch="one").row
    if not isinstance(row, (tuple, list)) or not row:
        raise SnowflakeQualificationError("Snowflake qualification metadata query was malformed")
    return row[0]


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
        raise SnowflakeQualificationError(
            f"Snowflake qualification could not remove disposable schema {schema_name}"
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


def _count(row: object) -> int:
    if not isinstance(row, (tuple, list)) or not row:
        raise SnowflakeQualificationError("Snowflake qualification count query was malformed")
    value = row[0]
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise SnowflakeQualificationError(
                "Snowflake qualification count query returned a non-integer"
            ) from error
    return value


def _relation(relation: RelationRef) -> str:
    return _qualified(*relation.coordinates)


def _qualified(*parts: str) -> str:
    return ".".join(_quote(part) for part in parts)


def _quote(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--role")
    parser.add_argument("--auth-method", choices=("key_pair", "oauth"), default="key_pair")
    parser.add_argument("--token-env", default="DANDER_SNOWFLAKE_OAUTH_TOKEN")
    parser.add_argument("--private-key-file-env", default="DANDER_SNOWFLAKE_PRIVATE_KEY_FILE")
    parser.add_argument("--private-key-password-env")
    parser.add_argument("--direct-max-rows", type=int, default=2)
    parser.add_argument("--direct-max-logical-bytes", type=int, default=1_048_576)
    parser.add_argument("--copy-part-rows", type=int, default=2)
    parser.add_argument("--copy-part-logical-bytes", type=int, default=1_048_576)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = run_snowflake_qualification(SnowflakeQualificationConfig(**vars(arguments)))
    except (ValueError, SnowflakeQualificationError):
        print(
            json.dumps(
                {
                    "schema": _REPORT_SCHEMA,
                    "provider": "snowflake",
                    "qualification_status": "failed",
                    "summary": (
                        "Snowflake qualification failed; inspect provider logs and rerun after "
                        "cleanup."
                    ),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 1
    print(report.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
