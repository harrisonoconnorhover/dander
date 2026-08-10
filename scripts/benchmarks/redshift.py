#!/usr/bin/env python3
"""Opt-in Redshift warehouse qualification using one schema and S3 prefix."""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import time
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal, Protocol, cast

from dander.concurrency import FencingToken, TargetFenceLostError
from dander.ingestion import Endpoint, RawField, SourceConfig
from dander.pipeline.graph import PipelineGraph
from dander.pipeline.runtime import GraphExecutionPlan, plan_graph_execution
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.redshift.session import execute, open_connection
from dander.warehouse import ProviderExtension, RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dander.providers.redshift.fence import RedshiftTargetFence
    from dander.providers.redshift.session import RedshiftConnectionFactory
    from dander.telemetry import OperationTelemetry
    from dander.writer import WritePattern


_REPORT_SCHEMA = "io.dander.benchmark.redshift/v1"
_AUTHORITY_ID = "redshift:qualification"


class RedshiftQualificationError(RuntimeError):
    """Raised with a sanitized qualification failure summary."""


class _S3Inspector(Protocol):
    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...
    def delete_objects(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class RedshiftQualificationConfig:
    """Non-secret live-profile coordinates and bounded workload controls."""

    deployment: Literal["provisioned", "serverless"]
    host: str
    database: str
    region: str
    copy_role_arn: str
    staging_bucket: str
    cluster_identifier: str | None = None
    db_user: str | None = None
    workgroup_name: str | None = None
    port: int = 5439
    staging_prefix: str = "dander/staging"
    direct_max_rows: int = 2
    direct_max_logical_bytes: int = 1_024 * 1_024
    copy_part_rows: int = 2
    copy_part_logical_bytes: int = 1_024 * 1_024

    def __post_init__(self) -> None:
        for name in (
            "direct_max_rows",
            "direct_max_logical_bytes",
            "copy_part_rows",
            "copy_part_logical_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        normalized_prefix = self.staging_prefix.strip("/")
        if (
            not normalized_prefix
            or ".." in normalized_prefix.split("/")
            or any(ord(char) < 32 for char in normalized_prefix)
        ):
            raise ValueError("staging_prefix must be a safe non-empty S3 key prefix")
        object.__setattr__(self, "staging_prefix", normalized_prefix)
        default_provider_registry().parse(
            ProviderKind.WAREHOUSE,
            _provider_values(
                self,
                direct=True,
                schema_name="dander_qualification_check",
                staging_prefix=f"{self.staging_prefix}/qualification/check",
            ),
        )


@dataclass(frozen=True, slots=True)
class RedshiftQualificationReport:
    """Sanitized correctness and bounded-work evidence from one disposable schema."""

    schema: str
    provider: str
    provider_version: str
    python_version: str
    deployment: str
    disposable_schema: str
    duration_seconds: float
    direct_rows: int
    copy_rows: int
    write_modes: tuple[str, ...]
    super_rows: int
    table_model_rows: int
    incremental_model_rows: int
    graph_rows: int
    replay_duplicate_free: bool
    cursor_monotonic: bool
    stale_publication_rejected: bool
    concurrent_claim_attempts: int
    direct_operations: int
    copy_operations: int
    query_ids: tuple[str, ...]
    staging_tables: int
    staging_objects: int
    schema_cleanup_verified: bool
    s3_cleanup_verified: bool
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


def run_redshift_qualification(
    config: RedshiftQualificationConfig,
) -> RedshiftQualificationReport:
    """Exercise Redshift writes, transforms, fencing, readback, and cleanup."""
    suffix = uuid.uuid4().hex[:12]
    schema_name = f"dander_qual_{suffix}"
    staging_prefix = f"{config.staging_prefix}/qualification/{suffix}"
    try:
        direct = _warehouse_runtime(
            config,
            schema_name=schema_name,
            staging_prefix=staging_prefix,
            direct=True,
        )
        copy = _warehouse_runtime(
            config,
            schema_name=schema_name,
            staging_prefix=staging_prefix,
            direct=False,
        )
    except Exception as error:
        raise RedshiftQualificationError(
            "Redshift qualification connection validation failed"
        ) from error

    started = time.perf_counter()
    try:
        report = _run_profile(
            config,
            direct=direct,
            copy=copy,
            schema_name=schema_name,
            staging_prefix=staging_prefix,
        )
    except (TargetFenceLostError, RedshiftQualificationError):
        raise
    except Exception as error:
        raise RedshiftQualificationError(
            f"Redshift qualification failed in disposable schema {schema_name}"
        ) from error
    finally:
        cleanup_errors: list[Exception] = []
        try:
            _drop_schema(direct, schema_name)
        except Exception as error:
            cleanup_errors.append(error)
        try:
            _delete_prefix(config, staging_prefix)
        except Exception as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            raise RedshiftQualificationError(
                "Redshift qualification could not remove all owned resources"
            ) from ExceptionGroup("Redshift qualification cleanup failures", cleanup_errors)

    schema_exists = _schema_exists(direct, schema_name)
    staging_objects = _prefix_object_count(config, staging_prefix)
    if schema_exists or staging_objects:
        raise RedshiftQualificationError("Redshift qualification cleanup left owned resources")
    return replace(
        report,
        duration_seconds=round(max(time.perf_counter() - started, 1e-9), 6),
        schema_cleanup_verified=True,
        s3_cleanup_verified=True,
    )


def _run_profile(
    config: RedshiftQualificationConfig,
    *,
    direct: WarehouseRuntime,
    copy: WarehouseRuntime,
    schema_name: str,
    staging_prefix: str,
) -> RedshiftQualificationReport:
    database = config.database
    all_operations: list[OperationTelemetry] = []

    direct_relation = RelationRef(catalog=database, namespace=schema_name, name="direct_records")
    direct_writer, direct_target = _writer_target(
        direct,
        relation=direct_relation,
        mode=WriteMode.SCD1,
        pipeline_id="redshift_qualification_direct",
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

    copy_relation = RelationRef(catalog=database, namespace=schema_name, name="scd1_records")
    copy_writer, copy_target = _writer_target(
        copy,
        relation=copy_relation,
        mode=WriteMode.SCD1,
        pipeline_id="redshift_qualification_scd1",
        run_id="scd1-1",
        token=1,
        business_key=("id",),
        fields=_base_fields(),
    )
    copy_writer.write(
        (
            {"id": "one", "label": "older"},
            {"id": "two", "label": "second"},
            {"id": "three", "label": "third"},
            {"id": "four", "label": "fourth"},
            {"id": "one", "label": "newer"},
        ),
        copy_target,
    )
    copy_operations = copy_writer.drain_telemetry()
    _require_transport(copy_operations, WriteTransport.COPY)
    all_operations.extend(copy_operations)
    expected_copy = (
        ("four", "fourth"),
        ("one", "newer"),
        ("three", "third"),
        ("two", "second"),
    )
    _require_rows(copy, copy_relation, ("id", "label"), expected_copy)
    copy_writer.write(
        (
            {"id": "one", "label": "older"},
            {"id": "two", "label": "second"},
            {"id": "three", "label": "third"},
            {"id": "four", "label": "fourth"},
            {"id": "one", "label": "newer"},
        ),
        copy_target,
    )
    all_operations.extend(copy_writer.drain_telemetry())
    _require_rows(copy, copy_relation, ("id", "label"), expected_copy)

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
        pipeline_id="redshift_qualification_incremental",
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
        pipeline_id="redshift_qualification_incremental",
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
        pipeline_id="redshift_qualification_snapshot",
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
        pipeline_id="redshift_qualification_scd2",
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
        pipeline_id="redshift_qualification_scd2",
        run_id="scd2-2",
        token=2,
        business_key=("id",),
        fields=_base_fields(),
    )
    scd2_update.write(({"id": "one", "label": "second"},), scd2_update_target)
    all_operations.extend(scd2_update.drain_telemetry())
    scd2_rows = _select(copy, scd2_relation, ("label", "is_current"), order_by=("valid_from",))
    if len(scd2_rows) != 2 or sum(bool(row[1]) for row in scd2_rows) != 1:
        raise RedshiftQualificationError("Redshift SCD2 readback did not retain one current row")

    replace_relation = RelationRef(catalog=database, namespace=schema_name, name="replace_records")
    replace_writer, replace_target = _writer_target(
        copy,
        relation=replace_relation,
        mode=WriteMode.REPLACE,
        pipeline_id="redshift_qualification_replace",
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
        pipeline_id="redshift_qualification_replace",
        run_id="replace-2",
        token=2,
        fields=_base_fields(),
    )
    replacement_writer.write(({"id": "three", "label": "replacement"},), replacement_target)
    all_operations.extend(replacement_writer.drain_telemetry())
    _require_rows(copy, replace_relation, ("id", "label"), (("three", "replacement"),))

    super_rows, super_operations = _exercise_super(copy, schema_name)
    all_operations.extend(super_operations)
    table_rows, incremental_model_rows, model_operations = _exercise_models(
        copy,
        schema_name,
    )
    all_operations.extend(model_operations)
    stale_rejected, concurrent_attempts = _exercise_concurrent_fence(copy, schema_name)
    graph_rows, graph_operations = _exercise_graph(copy, schema_name, source=direct_relation)
    all_operations.extend(graph_operations)

    staging_tables = _staging_table_count(copy, database)
    staging_objects = _prefix_object_count(config, staging_prefix)
    if staging_tables or staging_objects:
        raise RedshiftQualificationError("Redshift qualification left run-scoped staging objects")
    query_ids = tuple(
        dict.fromkeys(
            operation.query_id
            for operation in all_operations
            if isinstance(operation.query_id, str) and operation.query_id
        )
    )[:100]
    if not query_ids:
        raise RedshiftQualificationError("Redshift qualification captured no warehouse query IDs")
    return RedshiftQualificationReport(
        schema=_REPORT_SCHEMA,
        provider="redshift",
        provider_version=str(_scalar(copy, "SELECT version()")),
        python_version=platform.python_version(),
        deployment=config.deployment,
        disposable_schema=schema_name,
        duration_seconds=0.0,
        direct_rows=len(expected_direct),
        copy_rows=len(expected_copy),
        write_modes=tuple(mode.value for mode in WriteMode),
        super_rows=super_rows,
        table_model_rows=table_rows,
        incremental_model_rows=incremental_model_rows,
        graph_rows=graph_rows,
        replay_duplicate_free=True,
        cursor_monotonic=True,
        stale_publication_rejected=stale_rejected,
        concurrent_claim_attempts=concurrent_attempts,
        direct_operations=sum(
            operation.transport is WriteTransport.DIRECT for operation in all_operations
        ),
        copy_operations=sum(
            operation.transport is WriteTransport.COPY for operation in all_operations
        ),
        query_ids=query_ids,
        staging_tables=staging_tables,
        staging_objects=staging_objects,
        schema_cleanup_verified=False,
        s3_cleanup_verified=False,
    )


def _exercise_super(
    runtime: WarehouseRuntime,
    schema_name: str,
) -> tuple[int, tuple[OperationTelemetry, ...]]:
    relation = RelationRef(
        catalog=_database(runtime),
        namespace=schema_name,
        name="super_records",
    )
    fallback = ProviderExtension(provider="redshift", name="fallback", value="super")
    writer, target = _writer_target(
        runtime,
        relation=relation,
        mode=WriteMode.SCD1,
        pipeline_id="redshift_qualification_super",
        run_id="super-1",
        token=1,
        business_key=("id",),
        fields=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="payload", data_type="JSON", extensions=(fallback,)),
        ),
    )
    writer.write(({"id": "one", "payload": {"nested": {"ready": True}}},), target)
    operations = writer.drain_telemetry()
    _require_transport(operations, WriteTransport.COPY)
    rows = _query(
        runtime,
        f'SELECT "id", JSON_SERIALIZE("payload") FROM {_relation(relation)}',
    )
    if len(rows) != 1 or rows[0][0] != "one":
        raise RedshiftQualificationError("Redshift SUPER readback returned the wrong row")
    try:
        payload = json.loads(str(rows[0][1]))
    except (TypeError, ValueError) as error:
        raise RedshiftQualificationError("Redshift SUPER readback was not valid JSON") from error
    if payload != {"nested": {"ready": True}}:
        raise RedshiftQualificationError("Redshift SUPER readback changed the JSON value")
    return len(rows), operations


def _exercise_models(
    runtime: WarehouseRuntime,
    schema_name: str,
) -> tuple[int, int, tuple[OperationTelemetry, ...]]:
    database = _database(runtime)
    # ``raw_records`` is the transform reference; the project resolver strips the
    # conventional ``raw_`` prefix before selecting the physical source relation.
    source = RelationRef(catalog=database, namespace=schema_name, name="records")
    source_fields = (*_base_fields(), WriteField(name="updated_at", data_type="INT64"))
    source_writer, source_target = _writer_target(
        runtime,
        relation=source,
        mode=WriteMode.SCD1,
        pipeline_id="redshift_qualification_model_source",
        run_id="source-1",
        token=1,
        business_key=("id",),
        fields=source_fields,
    )
    source_writer.write(
        (
            {"id": "one", "label": "older", "updated_at": 1},
            {"id": "one", "label": "newer", "updated_at": 2},
            {"id": "two", "label": "second", "updated_at": 1},
        ),
        source_target,
    )
    operations: list[OperationTelemetry] = list(source_writer.drain_telemetry())
    with TemporaryDirectory(prefix="dander-redshift-models-") as directory:
        models = Path(directory)
        _write_model(models, "table_model", "table", schema_name, with_tests=True)
        _write_model(models, "incremental_model", "incremental", schema_name)
        runner = runtime.transforms.build_transform_runner(
            graph_plan=None,
            build_models=True,
            raw_namespace=schema_name,
        )
        if runner is None:
            raise RedshiftQualificationError("Redshift model runner is unavailable")
        table_result = runner.build(
            models,
            selected=("table_model",),
            ownership=_ownership("redshift_qualification_table", "table-1", 1),
        )
        operations.extend(table_result.telemetry)
        table_relation = RelationRef(
            catalog=database,
            namespace=schema_name,
            name="table_model",
        )
        _require_rows(
            runtime,
            table_relation,
            ("id", "label", "updated_at"),
            (("one", "newer", 2), ("two", "second", 1)),
        )
        incremental_result = runner.build(
            models,
            selected=("incremental_model",),
            ownership=_ownership("redshift_qualification_incremental_model", "model-1", 1),
        )
        operations.extend(incremental_result.telemetry)
        incremental_relation = RelationRef(
            catalog=database,
            namespace=schema_name,
            name="incremental_model",
        )
        _require_rows(
            runtime,
            incremental_relation,
            ("id", "label", "updated_at"),
            (("one", "newer", 2), ("two", "second", 1)),
        )

        update_writer, update_target = _writer_target(
            runtime,
            relation=source,
            mode=WriteMode.SCD1,
            pipeline_id="redshift_qualification_model_source",
            run_id="source-2",
            token=2,
            business_key=("id",),
            fields=source_fields,
        )
        update_writer.write(
            ({"id": "one", "label": "latest", "updated_at": 3},),
            update_target,
        )
        operations.extend(update_writer.drain_telemetry())
        update_result = runner.build(
            models,
            selected=("incremental_model",),
            ownership=_ownership("redshift_qualification_incremental_model", "model-2", 2),
        )
        operations.extend(update_result.telemetry)

        stale_writer, stale_target = _writer_target(
            runtime,
            relation=source,
            mode=WriteMode.SCD1,
            pipeline_id="redshift_qualification_model_source",
            run_id="source-3",
            token=3,
            business_key=("id",),
            fields=source_fields,
        )
        stale_writer.write(
            ({"id": "one", "label": "stale", "updated_at": 1},),
            stale_target,
        )
        operations.extend(stale_writer.drain_telemetry())
        replay_result = runner.build(
            models,
            selected=("incremental_model",),
            ownership=_ownership("redshift_qualification_incremental_model", "model-3", 3),
        )
        operations.extend(replay_result.telemetry)
        _require_rows(
            runtime,
            incremental_relation,
            ("id", "label", "updated_at"),
            (("one", "latest", 3), ("two", "second", 1)),
        )
        return (
            _count_relation(runtime, table_relation),
            _count_relation(runtime, incremental_relation),
            tuple(operations),
        )


def _exercise_concurrent_fence(
    runtime: WarehouseRuntime,
    schema_name: str,
) -> tuple[bool, int]:
    target_fence = cast("RedshiftTargetFence", runtime.target_fence)
    relation = RelationRef(
        catalog=target_fence.database,
        namespace=schema_name,
        name="concurrent_records",
    )
    old_token = FencingToken(
        lease_table=None,
        pipeline_id="redshift_qualification_concurrency",
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
    current_publication = target_fence.claim(relation, newer_token)
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    current_target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=_base_fields(),
        publication_fence=current_publication,
    )
    writer.write(({"id": "current", "label": "accepted"},), current_target)
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
        _require_rows(runtime, relation, ("id", "label"), (("current", "accepted"),))
        return True, len(futures)
    raise RedshiftQualificationError("Redshift accepted a stale concurrent publication")


def _exercise_graph(
    runtime: WarehouseRuntime,
    schema_name: str,
    *,
    source: RelationRef,
) -> tuple[int, tuple[OperationTelemetry, ...]]:
    runner = runtime.transforms.build_transform_runner(
        graph_plan=_graph_plan(source=source, target_schema=schema_name),
        build_models=True,
    )
    if runner is None:
        raise RedshiftQualificationError("Redshift graph runner is unavailable")
    ownership = _ownership("redshift_qualification_graph", "graph-1", 1)
    result = runner.build(Path("."), ownership=ownership)
    if result.models != ("target",) or ownership.verifications < 2:
        raise RedshiftQualificationError("Redshift graph ownership verification was incomplete")
    target = RelationRef(
        catalog=source.catalog,
        namespace=schema_name,
        name="graph_records",
    )
    _require_rows(
        runtime,
        target,
        ("id", "label"),
        (("one", "first"), ("two", "second")),
    )
    return _count_relation(runtime, target), result.telemetry


def _graph_plan(*, source: RelationRef, target_schema: str) -> GraphExecutionPlan:
    source_config = SourceConfig(
        name="redshift_qualification",
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
            "name": "redshift_qualification_graph",
            "nodes": [
                {
                    "id": "records",
                    "type": "source",
                    "name": "Records",
                    "config": {
                        "connector": "redshift_qualification",
                        "endpoint": "records",
                    },
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
    config: RedshiftQualificationConfig,
    *,
    schema_name: str,
    staging_prefix: str,
    direct: bool,
) -> WarehouseRuntime:
    registry = default_provider_registry()
    parsed = registry.parse(
        ProviderKind.WAREHOUSE,
        _provider_values(
            config,
            direct=direct,
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
    config: RedshiftQualificationConfig,
    *,
    direct: bool,
    schema_name: str,
    staging_prefix: str,
) -> dict[str, object]:
    values: dict[str, object] = {
        "provider": "redshift",
        "deployment": config.deployment,
        "host": config.host,
        "port": config.port,
        "database": config.database,
        "schema": schema_name,
        "region": config.region,
        "copy_role_arn": config.copy_role_arn,
        "staging_bucket": config.staging_bucket,
        "staging_prefix": staging_prefix,
        "max_rows_per_file": config.copy_part_rows,
        "max_logical_bytes_per_file": config.copy_part_logical_bytes,
        "direct_max_rows": config.direct_max_rows if direct else 0,
        "direct_max_logical_bytes": config.direct_max_logical_bytes if direct else 0,
    }
    if config.deployment == "provisioned":
        values.update(
            {
                "cluster_identifier": config.cluster_identifier,
                "db_user": config.db_user,
            }
        )
    else:
        values["workgroup_name"] = config.workgroup_name
    return values


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


def _base_fields() -> tuple[WriteField, ...]:
    return (
        WriteField(name="id", data_type="STRING"),
        WriteField(name="label", data_type="STRING"),
    )


def _ownership(pipeline_id: str, run_id: str, token: int) -> _Ownership:
    return _Ownership(
        FencingToken(
            lease_table=None,
            pipeline_id=pipeline_id,
            run_id=run_id,
            token=token,
            authority_id=_AUTHORITY_ID,
        )
    )


def _write_model(
    root: Path,
    name: str,
    materialization: str,
    namespace: str,
    *,
    with_tests: bool = False,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.sql").write_text("SELECT id, label, updated_at FROM {{ ref('raw_records') }}")
    incremental = (
        "unique_key: [id]\nincremental_cursor: updated_at\n"
        if materialization == "incremental"
        else ""
    )
    tests = (
        "tests:\n  - column: id\n    not_null: true\n    unique: true\n"
        if with_tests
        else "tests: []\n"
    )
    (root / f"{name}.yml").write_text(
        f"model: {name}\n"
        "description: Portable Redshift qualification model.\n"
        "owner: data-eng\n"
        "dialect: portable\n"
        f"materialization: {materialization}\n"
        f"dataset: {namespace}\n"
        "source_system: qualification\n"
        "sensitivity: public\n"
        f"{incremental}"
        "columns:\n"
        "  - name: id\n"
        "    type: STRING\n"
        "    description: Stable fixture identifier.\n"
        "  - name: label\n"
        "    type: STRING\n"
        "    description: Deterministic fixture value.\n"
        "  - name: updated_at\n"
        "    type: INT64\n"
        "    description: Monotonic fixture cursor.\n"
        f"{tests}"
    )


def _require_transport(
    operations: Sequence[OperationTelemetry],
    transport: WriteTransport,
) -> None:
    if not operations or any(operation.transport is not transport for operation in operations):
        raise RedshiftQualificationError(
            f"Redshift qualification did not use required {transport.value} transport"
        )


def _connection_factory(runtime: WarehouseRuntime) -> RedshiftConnectionFactory:
    return cast("RedshiftTargetFence", runtime.target_fence).connection_factory


def _database(runtime: WarehouseRuntime) -> str:
    return cast("RedshiftTargetFence", runtime.target_fence).database


def _query(runtime: WarehouseRuntime, statement: str) -> tuple[tuple[object, ...], ...]:
    with open_connection(_connection_factory(runtime)) as connection:
        rows = execute(connection, statement, fetch="all").rows
    return tuple(tuple(row) for row in rows if isinstance(row, (tuple, list)))


def _select(
    runtime: WarehouseRuntime,
    relation: RelationRef,
    fields: Sequence[str],
    *,
    order_by: Sequence[str] = (),
) -> tuple[tuple[object, ...], ...]:
    columns = ", ".join(_quote(field) for field in fields)
    ordering = f" ORDER BY {', '.join(_quote(field) for field in order_by)}" if order_by else ""
    return _query(runtime, f"SELECT {columns} FROM {_relation(relation)}{ordering}")


def _require_rows(
    runtime: WarehouseRuntime,
    relation: RelationRef,
    fields: Sequence[str],
    expected: tuple[tuple[object, ...], ...],
) -> None:
    actual = _select(runtime, relation, fields, order_by=(fields[0],))
    if actual != expected:
        raise RedshiftQualificationError(
            f"Redshift readback mismatch for qualification relation {relation.name}"
        )


def _count_relation(runtime: WarehouseRuntime, relation: RelationRef) -> int:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(connection, f"SELECT COUNT(*) FROM {_relation(relation)}", fetch="one").row
    return _count(row)


def _require_count(runtime: WarehouseRuntime, relation: RelationRef, expected: int) -> None:
    if _count_relation(runtime, relation) != expected:
        raise RedshiftQualificationError(
            f"Redshift row-count mismatch for qualification relation {relation.name}"
        )


def _scalar(runtime: WarehouseRuntime, statement: str) -> object:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(connection, statement, fetch="one").row
    if not isinstance(row, (tuple, list)) or not row:
        raise RedshiftQualificationError("Redshift qualification metadata query was malformed")
    return row[0]


def _staging_table_count(runtime: WarehouseRuntime, database: str) -> int:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(
            connection,
            "SELECT COUNT(*) FROM svv_tables WHERE table_catalog = %s "
            "AND table_name ~ '^dander_stage_[0-9a-f]{24}$'",
            (database,),
            fetch="one",
        ).row
    return _count(row)


def _drop_schema(runtime: WarehouseRuntime, schema_name: str) -> None:
    try:
        with open_connection(_connection_factory(runtime)) as connection:
            execute(connection, f"DROP SCHEMA IF EXISTS {_quote(schema_name)} CASCADE")
            connection.commit()
    except Exception as error:
        raise RedshiftQualificationError(
            f"Redshift qualification could not remove disposable schema {schema_name}"
        ) from error


def _schema_exists(runtime: WarehouseRuntime, schema_name: str) -> bool:
    with open_connection(_connection_factory(runtime)) as connection:
        row = execute(
            connection,
            "SELECT COUNT(*) FROM pg_namespace WHERE nspname = %s",
            (schema_name,),
            fetch="one",
        ).row
    return _count(row) != 0


def _s3(config: RedshiftQualificationConfig) -> _S3Inspector:
    try:
        boto3 = importlib.import_module("boto3")
    except ModuleNotFoundError as error:
        raise RedshiftQualificationError(
            "Redshift qualification requires the dander-platform[redshift] extra"
        ) from error
    return cast("_S3Inspector", boto3.client("s3", region_name=config.region))


def _prefix_keys(config: RedshiftQualificationConfig, staging_prefix: str) -> tuple[str, ...]:
    client = _s3(config)
    keys: list[str] = []
    token: str | None = None
    while True:
        arguments: dict[str, object] = {
            "Bucket": config.staging_bucket,
            "Prefix": f"{staging_prefix}/",
        }
        if token is not None:
            arguments["ContinuationToken"] = token
        response = client.list_objects_v2(**arguments)
        contents = response.get("Contents", [])
        if not isinstance(contents, list):
            raise RedshiftQualificationError("Redshift S3 cleanup returned malformed contents")
        for item in contents:
            if not isinstance(item, Mapping) or not isinstance(item.get("Key"), str):
                raise RedshiftQualificationError("Redshift S3 cleanup returned a malformed key")
            key = cast("str", item["Key"])
            if not key.startswith(f"{staging_prefix}/"):
                raise RedshiftQualificationError("Redshift S3 cleanup escaped its owned prefix")
            keys.append(key)
        if response.get("IsTruncated") is not True:
            break
        next_token = response.get("NextContinuationToken")
        if not isinstance(next_token, str) or not next_token:
            raise RedshiftQualificationError("Redshift S3 cleanup pagination was malformed")
        token = next_token
    return tuple(keys)


def _prefix_object_count(config: RedshiftQualificationConfig, staging_prefix: str) -> int:
    return len(_prefix_keys(config, staging_prefix))


def _delete_prefix(config: RedshiftQualificationConfig, staging_prefix: str) -> None:
    try:
        client = _s3(config)
        keys = _prefix_keys(config, staging_prefix)
        for index in range(0, len(keys), 1_000):
            response = client.delete_objects(
                Bucket=config.staging_bucket,
                Delete={"Objects": [{"Key": key} for key in keys[index : index + 1_000]]},
            )
            if not isinstance(response, Mapping):
                raise RedshiftQualificationError(
                    "Redshift S3 cleanup returned a malformed delete response"
                )
            errors = response.get("Errors", [])
            if not isinstance(errors, list):
                raise RedshiftQualificationError(
                    "Redshift S3 cleanup returned malformed delete errors"
                )
            if errors:
                raise RedshiftQualificationError(
                    "Redshift S3 cleanup reported one or more undeleted objects"
                )
    except RedshiftQualificationError:
        raise
    except Exception as error:
        raise RedshiftQualificationError(
            "Redshift qualification could not clean its S3 staging prefix"
        ) from error


def _count(row: object) -> int:
    if not isinstance(row, (tuple, list)) or not row:
        raise RedshiftQualificationError("Redshift qualification count query was malformed")
    value = row[0]
    if isinstance(value, bool):
        raise RedshiftQualificationError("Redshift qualification count was not an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise RedshiftQualificationError(
            "Redshift qualification count was not an integer"
        ) from error


def _relation(relation: RelationRef) -> str:
    return ".".join(_quote(part) for part in relation.coordinates[1:])


def _quote(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", required=True, choices=("provisioned", "serverless"))
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5439)
    parser.add_argument("--database", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--copy-role-arn", required=True)
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--staging-prefix", default="dander/staging")
    parser.add_argument("--cluster-identifier")
    parser.add_argument("--db-user")
    parser.add_argument("--workgroup-name")
    parser.add_argument("--direct-max-rows", type=int, default=2)
    parser.add_argument("--direct-max-logical-bytes", type=int, default=1_024 * 1_024)
    parser.add_argument("--copy-part-rows", type=int, default=2)
    parser.add_argument("--copy-part-logical-bytes", type=int, default=1_024 * 1_024)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = run_redshift_qualification(RedshiftQualificationConfig(**vars(arguments)))
    except Exception:
        print(
            json.dumps(
                {
                    "schema": _REPORT_SCHEMA,
                    "provider": "redshift",
                    "qualification_status": "failed",
                    "summary": (
                        "Redshift qualification failed; inspect provider logs and rerun after "
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
