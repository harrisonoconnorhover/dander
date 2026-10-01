"""BigQuery execution and writer preparation for provider-neutral graph plans."""

from __future__ import annotations

import re
from functools import partial
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

from google.cloud import bigquery

from dander.concurrency import fenced_dml, fencing_job_config
from dander.identity import google_client_options
from dander.pipeline.compiler import (
    CompiledTarget,
    PipelineCompileError,
    PreparedTargetWriter,
)
from dander.pipeline.node_config import TargetNodeConfig
from dander.pipeline.runtime import GraphExecutionPlan, GraphRuntimeError
from dander.providers.bigquery.telemetry import BigQueryJobTelemetry
from dander.telemetry import TelemetryOperation
from dander.transform import SqlDialect, TransformRunResult
from dander.warehouse import RelationRef
from dander.writer import (
    BigQueryIncrementalWriter,
    BigQueryReplaceWriter,
    BigQueryScd1Writer,
    BigQueryScd2Writer,
    BigQuerySnapshotWriter,
    BigQueryStorageIncrementalWriter,
    BigQueryStorageScd1Writer,
    WriteField,
    WriteMode,
    WritePattern,
    WriteTarget,
    WriteTransport,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from dander.concurrency import OwnershipGuard
    from dander.pipeline.graph import Node
    from dander.pipeline.repair import GraphRepairWindow, GraphTargetRepair
    from dander.writer.bigquery import _BigQueryClient as _WriterClient

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _QueryJob(Protocol):
    def result(self) -> object:
        """Wait for BigQuery completion."""


class _BigQueryClient(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _QueryJob:
        """Submit BigQuery Standard SQL."""

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        """Delete one run-scoped staging table."""


class _RepairQueryJob:
    """Keep actual committed row counts from the repair script's final SELECT."""

    def __init__(self, job: _QueryJob) -> None:
        self._job = job
        self.output_rows = 0
        self.num_dml_affected_rows = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._job, name)

    def result(self) -> object:
        result = self._job.result()
        rows = iter(cast("Iterable[Mapping[str, object]]", result))
        row = next(rows, None)
        if row is None:
            raise GraphRuntimeError("Date repair did not return its committed row counts")
        written, affected = row["rows_written"], row["rows_affected"]
        if (
            not isinstance(written, int)
            or isinstance(written, bool)
            or not isinstance(affected, int)
            or isinstance(affected, bool)
            or written < 0
            or affected < written
        ):
            raise GraphRuntimeError("Date repair returned invalid committed row counts")
        self.output_rows = written
        self.num_dml_affected_rows = affected
        return result


class BigQueryGraphRunner:
    """Materialize compiled graph targets inside the transform stage."""

    def __init__(
        self,
        *,
        plan: GraphExecutionPlan,
        project: str,
        client: _BigQueryClient | None = None,
    ) -> None:
        for target in plan.targets:
            _validate_target(target, project)
        if plan.repair_window is not None and {item.node_id for item in plan.repair_targets} != {
            target.node_id for target in plan.targets
        }:
            raise GraphRuntimeError("Date repair requires an eligible partition for every output")
        self._plan = plan
        self._project = project
        self._client = client or cast(
            "_BigQueryClient",
            bigquery.Client(project=project, **google_client_options()),
        )

    def build(
        self,
        _models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Stage and transactionally publish selected replace-mode targets."""
        if self._plan.repair_window is not None and (
            ownership is None or ownership.fence is None or ownership.fence.lease_table is None
        ):
            raise GraphRuntimeError("Date repair requires active BigQuery lease fencing")
        selected_ids = set(selected) if selected is not None else None
        known = {target.node_id for target in self._plan.targets}
        if selected_ids is not None and (unknown := sorted(selected_ids - known)):
            raise GraphRuntimeError(f"Unknown graph target: {unknown[0]!r}")
        targets = tuple(
            target
            for target in self._plan.targets
            if selected_ids is None or target.node_id in selected_ids
        )
        if not targets:
            raise GraphRuntimeError("Graph execution selected no targets")
        telemetry = BigQueryJobTelemetry()
        for target in targets:
            self._materialize(target, ownership=ownership, telemetry=telemetry)
        return TransformRunResult(
            models=tuple(target.node_id for target in targets),
            assertions=0,
            telemetry=telemetry.drain(),
        )

    def _materialize(
        self,
        compiled: CompiledTarget,
        *,
        ownership: OwnershipGuard | None,
        telemetry: BigQueryJobTelemetry,
    ) -> None:
        target = compiled.target
        target_id = ".".join(target.relation_ref.coordinates)
        staging = RelationRef(
            catalog=target.relation_ref.catalog,
            namespace=target.relation_ref.namespace,
            name=f"_dander_stage_{target.relation_ref.name}_{uuid4().hex}",
        )
        staging_id = ".".join(staging.coordinates)
        columns = _target_columns(target)
        quoted_columns = ", ".join(f"`{column}`" for column in columns)
        query = compiled.render(SqlDialect.BIGQUERY)
        try:
            if ownership is not None:
                ownership.verify()
            telemetry.run(
                lambda: self._client.query(
                    f"CREATE TABLE `{staging_id}`\n"
                    "OPTIONS (expiration_timestamp="
                    "TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 1 DAY))\n"
                    f"AS\n{query}"
                ),
                operation=TelemetryOperation.TRANSFORM,
            )
            if self._plan.repair_window is None:
                telemetry.run(
                    lambda: self._client.query(
                        f"CREATE TABLE IF NOT EXISTS `{target_id}` AS "
                        f"SELECT {quoted_columns} FROM `{staging_id}` WHERE FALSE"
                    ),
                    operation=TelemetryOperation.TRANSFORM,
                )
            if ownership is not None:
                ownership.verify()
            replacement = (
                f"DELETE FROM `{target_id}` WHERE TRUE;\n"
                f"INSERT INTO `{target_id}` ({quoted_columns})\n"
                f"SELECT {quoted_columns} FROM `{staging_id}`"
            )
            if self._plan.repair_window is not None:
                repair = next(
                    item for item in self._plan.repair_targets if item.node_id == compiled.node_id
                )
                replacement = _repair_dml(
                    target_id, staging_id, columns, repair, self._plan.repair_window
                )
            fence = ownership.fence if ownership is not None else None
            if fence is not None:
                script = fenced_dml(replacement, fence)
                job_config = fencing_job_config(fence)
                submit = partial(self._client.query, script, job_config=job_config)
            else:
                script = f"BEGIN TRANSACTION;\n{replacement};\nCOMMIT TRANSACTION;"
                submit = partial(self._client.query, script)
            if self._plan.repair_window is not None:
                script = (
                    "DECLARE dander_repair_deleted INT64 DEFAULT 0;\n"
                    "DECLARE dander_repair_inserted INT64 DEFAULT 0;\n"
                    + script
                    + "\nSELECT dander_repair_inserted AS rows_written, "
                    "dander_repair_deleted + dander_repair_inserted AS rows_affected;"
                )
                repair_config = fencing_job_config(fence) if fence is not None else None
                telemetry.run(
                    lambda: _RepairQueryJob(self._client.query(script, job_config=repair_config)),
                    operation=TelemetryOperation.TRANSFORM,
                    retry_mutation=True,
                )
            else:
                telemetry.run(submit, operation=TelemetryOperation.TRANSFORM, retry_mutation=True)
        finally:
            self._client.delete_table(staging_id, not_found_ok=True)


def _repair_dml(
    target_id: str,
    staging_id: str,
    columns: tuple[str, ...],
    repair: GraphTargetRepair,
    window: GraphRepairWindow,
) -> str:
    """Validate the selected keys against both retained sides before changing any target row."""

    def selected(alias: str) -> str:
        value = f"{alias}.`{repair.partition_field}`"
        if repair.partition_type == "TIMESTAMP":
            value = f"DATE({value}, 'UTC')"
        return (
            f"({value} >= DATE '{window.start_date.isoformat()}' "
            f"AND {value} < DATE '{window.end_date.isoformat()}')"
        )

    invalid = " OR ".join(f"`{column}` IS NULL" for column in repair.business_key)
    keys = ", ".join(f"`{key}`" for key in repair.business_key)
    join = " AND ".join(f"s.`{key}` = t.`{key}`" for key in repair.business_key)
    quoted_columns = ", ".join(f"`{column}`" for column in columns)
    validations: list[str] = []
    for relation in (staging_id, target_id):
        validations.extend(
            (
                f"ASSERT NOT EXISTS (SELECT 1 FROM `{relation}` s "
                f"WHERE {selected('s')} AND ({invalid})) "
                "AS 'Selected repair business keys must not be null';",
                f"ASSERT NOT EXISTS (SELECT 1 FROM `{relation}` s "
                f"WHERE {selected('s')} GROUP BY {keys} "
                "HAVING COUNT(*) > 1) AS 'Repair business keys must be unique';",
            )
        )
    validations.extend(
        (
            f"ASSERT NOT EXISTS (SELECT 1 FROM `{staging_id}` s JOIN `{target_id}` t ON {join} "
            f"WHERE {selected('s')} AND NOT COALESCE({selected('t')}, FALSE)) "
            "AS 'Repair staged keys overlap target rows outside the selected dates';",
            f"ASSERT NOT EXISTS (SELECT 1 FROM `{target_id}` t JOIN `{staging_id}` s ON {join} "
            f"WHERE {selected('t')} AND NOT COALESCE({selected('s')}, FALSE)) "
            "AS 'Repair target keys moved outside the selected dates in retained raw data';",
            f"DELETE FROM `{target_id}` t WHERE {selected('t')};",
            "SET dander_repair_deleted = @@row_count;",
            f"INSERT INTO `{target_id}` ({quoted_columns})\n"
            f"SELECT {quoted_columns} FROM `{staging_id}` s WHERE {selected('s')};",
            "SET dander_repair_inserted = @@row_count",
        )
    )
    return "\n".join(validations)


def prepare_bigquery_target_writer(
    target_node: Node,
    *,
    default_catalog: str,
    client: object | None = None,
) -> PreparedTargetWriter:
    """Resolve target-node configuration to one concrete BigQuery writer."""
    config = target_node.config
    if target_node.type != "target" or not isinstance(config, TargetNodeConfig):
        raise PipelineCompileError(f"Node {target_node.id!r} is not a configured target")
    if config.writer is None:
        raise PipelineCompileError(f"Target node {target_node.id!r} has no writer configuration")
    writer_config = config.writer
    if writer_config.transport is WriteTransport.DIRECT:
        raise PipelineCompileError(
            "BigQuery target execution does not support the provider-selected direct transport"
        )
    destination = writer_config.destination
    try:
        relation = destination.relation_ref(default_catalog=default_catalog)
    except ValueError as error:
        raise PipelineCompileError(
            f"Target node {target_node.id!r} has an invalid destination"
        ) from error
    project = relation.catalog
    typed_client = cast("_WriterClient | None", client)
    match writer_config.write_mode:
        case WriteMode.SCD1:
            if writer_config.transport is WriteTransport.STORAGE_WRITE:
                writer: WritePattern = BigQueryStorageScd1Writer(
                    project=project,
                    client=typed_client,
                    max_batch_rows=writer_config.max_batch_rows,
                    schema_evolution=writer_config.schema_evolution,
                )
            else:
                writer = BigQueryScd1Writer(
                    project=project,
                    client=typed_client,
                    max_batch_rows=writer_config.max_batch_rows,
                    schema_evolution=writer_config.schema_evolution,
                )
        case WriteMode.SCD2:
            writer = BigQueryScd2Writer(
                project=project,
                client=typed_client,
                max_batch_rows=writer_config.max_batch_rows,
                schema_evolution=writer_config.schema_evolution,
            )
        case WriteMode.INCREMENTAL:
            assert writer_config.cursor_field is not None
            if writer_config.transport is WriteTransport.STORAGE_WRITE:
                writer = BigQueryStorageIncrementalWriter(
                    project=project,
                    cursor_field=writer_config.cursor_field,
                    client=typed_client,
                    max_batch_rows=writer_config.max_batch_rows,
                    schema_evolution=writer_config.schema_evolution,
                )
            else:
                writer = BigQueryIncrementalWriter(
                    project=project,
                    cursor_field=writer_config.cursor_field,
                    client=typed_client,
                    max_batch_rows=writer_config.max_batch_rows,
                    schema_evolution=writer_config.schema_evolution,
                )
        case WriteMode.SNAPSHOT:
            partitioning = writer_config.partitioning
            if partitioning is None or partitioning.field is None:
                raise PipelineCompileError(
                    "Snapshot target execution requires field-based partitioning"
                )
            writer = BigQuerySnapshotWriter(
                project=project,
                snapshot_field=partitioning.field,
                client=typed_client,
                max_batch_rows=writer_config.max_batch_rows,
                schema_evolution=writer_config.schema_evolution,
            )
        case WriteMode.REPLACE:
            writer = BigQueryReplaceWriter(
                project=project,
                client=typed_client,
                max_batch_rows=writer_config.max_batch_rows,
            )
    return PreparedTargetWriter(
        writer=writer,
        target=WriteTarget(
            relation=relation,
            business_key=tuple(destination.business_key),
            schema=tuple(
                WriteField(
                    name=field.name,
                    data_type=field.cast_to or field.type,
                    extensions=field.extensions,
                )
                for field in target_node.fields
            ),
        ),
    )


def _target_columns(target: WriteTarget) -> tuple[str, ...]:
    fields = target.schema or (target.declared_schema.fields if target.declared_schema else ())
    return tuple(field.name for field in fields)


def _validate_target(compiled: CompiledTarget, project: str) -> None:
    target = compiled.target
    if target.relation_ref.catalog != project:
        raise GraphRuntimeError(
            f"Target node {compiled.node_id!r} must write inside the runtime project"
        )
    coordinates = (target.relation_ref.namespace, target.relation_ref.name)
    if any(not _IDENTIFIER.fullmatch(value) for value in coordinates):
        raise GraphRuntimeError(f"Target node {compiled.node_id!r} has an invalid destination")
    columns = _target_columns(target)
    if not columns or any(not _IDENTIFIER.fullmatch(column) for column in columns):
        raise GraphRuntimeError(
            f"Target node {compiled.node_id!r} must declare valid output fields"
        )
    if len(columns) != len(set(columns)):
        raise GraphRuntimeError(f"Target node {compiled.node_id!r} has duplicate output fields")


__all__ = ["BigQueryGraphRunner", "prepare_bigquery_target_writer"]
