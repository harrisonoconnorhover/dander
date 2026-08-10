"""Bounded Parquet stage/COPY and transactionally fenced Snowflake writes."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from dander.concurrency import TargetFenceLostError
from dander.providers.snowflake.session import (
    SnowflakeConnection,
    SnowflakeConnectionFactory,
    execute,
    open_connection,
)
from dander.warehouse import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    ParquetStagingSession,
    RelationSchema,
    StagingManifest,
)
from dander.writer import SchemaEvolution, WriteMode, WritePattern, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from dander.concurrency import TargetFence
    from dander.providers.snowflake.fence import SnowflakeTargetFence

_ORDINAL = "_dander_ordinal"
_SCD2_VALID_FROM = "valid_from"
_SCD2_VALID_TO = "valid_to"
_SCD2_IS_CURRENT = "is_current"
_SCD2_SYSTEM_FIELDS = frozenset({_SCD2_VALID_FROM, _SCD2_VALID_TO, _SCD2_IS_CURRENT})


class SnowflakeWriteError(ValueError):
    """Raised when staged rows or destination schema violate the Snowflake contract."""


@dataclass(frozen=True, slots=True)
class SnowflakeStagingSettings:
    """Bounded local artifact and Snowflake session controls."""

    root: Path
    max_rows_per_file: int
    max_logical_bytes_per_file: int
    compression: str


class SnowflakeStagedWriter(WritePattern):
    """Stage bounded Parquet parts once and publish one selected logical write mode."""

    requires_publication_fence = True

    def __init__(
        self,
        *,
        database: str,
        connection_factory: SnowflakeConnectionFactory,
        target_fence: SnowflakeTargetFence,
        schema_evolution: SchemaEvolution,
        staging: SnowflakeStagingSettings,
        mode: WriteMode,
        cursor_field: str | None = None,
        snapshot_field: str | None = None,
    ) -> None:
        self._database = database
        self._connection_factory = connection_factory
        self._target_fence = target_fence
        self._schema_evolution = schema_evolution
        self._staging = staging
        self.mode = mode
        self._cursor_field = cursor_field
        self._snapshot_field = snapshot_field
        self.supports_batched_writes = mode not in {WriteMode.REPLACE, WriteMode.SCD2}
        self.accepts_streaming_input = mode in {WriteMode.REPLACE, WriteMode.SCD2}

    def write(self, records: Iterable[Mapping[str, object]], target: WriteTarget) -> int:
        """Consume records once, upload checksummed parts, and publish idempotently."""
        _validate_target(
            target,
            database=self._database,
            mode=self.mode,
            cursor_field=self._cursor_field,
            snapshot_field=self._snapshot_field,
        )
        publication = target.publication_fence
        assert publication is not None
        target_schema = target.canonical_schema
        reserved = {_ORDINAL}
        if self.mode is WriteMode.SCD2:
            reserved.update(_SCD2_SYSTEM_FIELDS)
        if collision := sorted(
            field.name for field in target_schema.fields if field.name in reserved
        ):
            raise SnowflakeWriteError(f"Declared schema reserves Dander field {collision[0]!r}")
        _validate_schema(target_schema)
        staging_schema = RelationSchema(
            fields=(
                *target_schema.fields,
                CanonicalField(
                    name=_ORDINAL,
                    data_type=CanonicalType(kind=LogicalTypeKind.INTEGER, bit_width=64),
                    cardinality=FieldCardinality.REQUIRED,
                ),
            )
        )
        local_id = f"snowflake-{uuid4().hex}"
        with ParquetStagingSession(
            self._staging.root,
            run_id=local_id,
            max_rows_per_file=self._staging.max_rows_per_file,
            max_logical_bytes_per_file=self._staging.max_logical_bytes_per_file,
            compression=self._staging.compression,
        ) as local:
            manifest = local.stage(
                _with_ordinals(
                    records,
                    business_key=target.business_key,
                    cursor_field=self._cursor_field,
                    snapshot_field=self._snapshot_field,
                ),
                staging_schema,
            )
            return self._load_and_publish(
                target,
                publication,
                target_schema,
                staging_schema,
                manifest,
            )

    def _load_and_publish(
        self,
        target: WriteTarget,
        publication: TargetFence,
        target_schema: RelationSchema,
        staging_schema: RelationSchema,
        manifest: object,
    ) -> int:
        if not isinstance(manifest, StagingManifest):
            raise TypeError("Snowflake writer received an invalid staging manifest")
        _validate_manifest_bounds(
            manifest,
            max_logical_bytes_per_file=self._staging.max_logical_bytes_per_file,
        )
        suffix = hashlib.sha256(
            f"{publication.target_id}:{publication.run_id}:{uuid4().hex}".encode()
        ).hexdigest()[:20]
        stage_name = f"dander_files_{suffix}"
        staging_table = f"dander_stage_{suffix}"
        relation = target.relation_ref
        stage = _qualified(relation.catalog, relation.namespace, stage_name)
        staging_relation = _qualified(relation.catalog, relation.namespace, staging_table)
        cleanup_started = False
        with open_connection(self._connection_factory) as connection:
            try:
                _set_query_tag(connection, publication)
                deployed_schema = _target_schema_for_mode(target_schema, self.mode)
                _ensure_target(
                    connection,
                    target,
                    deployed_schema,
                    evolution=self._schema_evolution,
                )
                _ensure_load_history(connection, target)
                if manifest.rows == 0:
                    result = self._target_fence.execute_dml(
                        connection,
                        _empty_publication_sql(target, self.mode),
                        publication,
                    )
                    return 0 if self.mode is WriteMode.REPLACE else result.rowcount
                execute(connection, _create_stage_sql(stage))
                execute(
                    connection,
                    _create_staging_table_sql(
                        staging_relation,
                        staging_schema,
                    ),
                )
                cleanup_started = True
                pending = tuple(
                    artifact
                    for artifact in manifest.artifacts
                    if not _artifact_committed(connection, target, publication, artifact.sha256)
                )
                if not pending:
                    result = self._target_fence.execute_dml(
                        connection,
                        _no_op_sql(target),
                        publication,
                    )
                    return result.rowcount
                artifacts_to_load = (
                    manifest.artifacts if self.mode is WriteMode.REPLACE else pending
                )
                for artifact in artifacts_to_load:
                    execute(connection, _put_sql(artifact.path, stage))
                    execute(connection, _copy_sql(staging_relation, stage, artifact.path.name))
                if self.mode is WriteMode.SCD2:
                    execute(
                        connection,
                        "SET DANDER_SCD2_EFFECTIVE_AT = CURRENT_TIMESTAMP()",
                    )
                statements: list[tuple[str, Sequence[object]]] = []
                statements.extend(
                    (
                        _load_history_insert_sql(target),
                        (
                            publication.target_id,
                            publication.pipeline_id,
                            publication.run_id,
                            artifact.sha256,
                            artifact.rows,
                            artifact.compressed_bytes,
                            publication.target_id,
                            publication.pipeline_id,
                            publication.run_id,
                            artifact.sha256,
                        ),
                    )
                    for artifact in pending
                )
                statements.extend(
                    _publication_statements(
                        target,
                        staging_relation,
                        target_schema.fields,
                        mode=self.mode,
                        cursor_field=self._cursor_field,
                        snapshot_field=self._snapshot_field,
                    )
                )
                result = self._target_fence.execute_statements(
                    connection,
                    statements,
                    publication,
                )
                return result.rowcount
            except (SnowflakeWriteError, TargetFenceLostError):
                raise
            except Exception as error:
                raise SnowflakeWriteError(
                    f"Snowflake staged {self.mode.value.upper()} write failed"
                ) from error
            finally:
                if cleanup_started:
                    _cleanup_remote(connection, stage=stage, staging_relation=staging_relation)


class SnowflakeScd1Writer(SnowflakeStagedWriter):
    """Backward-compatible SCD1 constructor for existing provider integrations."""

    mode = WriteMode.SCD1

    def __init__(
        self,
        *,
        database: str,
        connection_factory: SnowflakeConnectionFactory,
        target_fence: SnowflakeTargetFence,
        schema_evolution: SchemaEvolution,
        staging: SnowflakeStagingSettings,
    ) -> None:
        super().__init__(
            database=database,
            connection_factory=connection_factory,
            target_fence=target_fence,
            schema_evolution=schema_evolution,
            staging=staging,
            mode=WriteMode.SCD1,
        )


def default_staging_settings(
    *,
    max_rows_per_file: int,
    max_logical_bytes_per_file: int,
    compression: str,
) -> SnowflakeStagingSettings:
    """Return container-safe local staging defaults without creating directories."""
    return SnowflakeStagingSettings(
        root=Path(tempfile.gettempdir()) / "dander-snowflake-staging",
        max_rows_per_file=max_rows_per_file,
        max_logical_bytes_per_file=max_logical_bytes_per_file,
        compression=compression,
    )


def _with_ordinals(
    records: Iterable[Mapping[str, object]],
    *,
    business_key: Sequence[str],
    cursor_field: str | None,
    snapshot_field: str | None,
) -> Iterable[Mapping[str, object]]:
    for ordinal, record in enumerate(records):
        if any(record.get(field) is None for field in business_key):
            raise SnowflakeWriteError(f"Record {ordinal} has a null business-key value")
        if cursor_field is not None and record.get(cursor_field) is None:
            raise SnowflakeWriteError(f"Record {ordinal} has a null incremental cursor value")
        if snapshot_field is not None and record.get(snapshot_field) is None:
            raise SnowflakeWriteError(f"Record {ordinal} has a null snapshot value")
        yield {**record, _ORDINAL: ordinal}


def _validate_target(
    target: WriteTarget,
    *,
    database: str,
    mode: WriteMode,
    cursor_field: str | None,
    snapshot_field: str | None,
) -> None:
    relation = target.relation_ref
    if relation.catalog != database:
        raise SnowflakeWriteError(
            f"Writer database {database!r} does not match target catalog {relation.catalog!r}"
        )
    if not target.schema:
        raise SnowflakeWriteError("Snowflake writes require a declared schema")
    field_names = {field.name for field in target.schema}
    if mode in {WriteMode.SCD1, WriteMode.SCD2, WriteMode.INCREMENTAL}:
        if not target.business_key:
            raise SnowflakeWriteError(f"Snowflake {mode.value} writes require a business key")
        if missing := sorted(set(target.business_key) - field_names):
            raise SnowflakeWriteError(f"Snowflake business-key field {missing[0]!r} is undeclared")
    if mode is WriteMode.INCREMENTAL:
        if cursor_field is None or not cursor_field.strip():
            raise SnowflakeWriteError("Snowflake incremental writes require cursor_field")
        if cursor_field not in field_names:
            raise SnowflakeWriteError("Snowflake incremental cursor field is undeclared")
    elif cursor_field is not None:
        raise SnowflakeWriteError("cursor_field is valid only for Snowflake incremental writes")
    if mode is WriteMode.SNAPSHOT:
        if snapshot_field is None or not snapshot_field.strip():
            raise SnowflakeWriteError("Snowflake snapshot writes require snapshot_field")
        if snapshot_field not in field_names:
            raise SnowflakeWriteError("Snowflake snapshot field is undeclared")
    elif snapshot_field is not None:
        raise SnowflakeWriteError("snapshot_field is valid only for Snowflake snapshot writes")
    publication = target.publication_fence
    if publication is None:
        raise SnowflakeWriteError("Snowflake hosted writes require a destination target fence")
    expected_target = ".".join(target.relation_ref.coordinates)
    expected_fence = f"{relation.catalog}.{relation.namespace}.dander_target_commits"
    if publication.target_id != expected_target or publication.fence_table != expected_fence:
        raise SnowflakeWriteError("Snowflake destination target fence does not match the target")


def _validate_schema(schema: RelationSchema) -> None:
    for field in schema.fields:
        _snowflake_type(field.data_type)


def _target_schema_for_mode(schema: RelationSchema, mode: WriteMode) -> RelationSchema:
    if mode is not WriteMode.SCD2:
        return schema
    return RelationSchema(
        fields=(
            *schema.fields,
            CanonicalField(
                name=_SCD2_VALID_FROM,
                data_type=CanonicalType(
                    kind=LogicalTypeKind.TIMESTAMP,
                    fractional_second_precision=9,
                    with_timezone=True,
                ),
                cardinality=FieldCardinality.REQUIRED,
            ),
            CanonicalField(
                name=_SCD2_VALID_TO,
                data_type=CanonicalType(
                    kind=LogicalTypeKind.TIMESTAMP,
                    fractional_second_precision=9,
                    with_timezone=True,
                ),
            ),
            CanonicalField(
                name=_SCD2_IS_CURRENT,
                data_type=CanonicalType(kind=LogicalTypeKind.BOOLEAN),
                cardinality=FieldCardinality.REQUIRED,
            ),
        )
    )


def _validate_manifest_bounds(
    manifest: StagingManifest,
    *,
    max_logical_bytes_per_file: int,
) -> None:
    if any(artifact.logical_bytes > max_logical_bytes_per_file for artifact in manifest.artifacts):
        raise SnowflakeWriteError("Snowflake staged artifact exceeds max_logical_bytes_per_file")


def _set_query_tag(connection: SnowflakeConnection, publication: TargetFence) -> None:
    tag = json.dumps(
        {
            "dander": {
                "pipeline_id": publication.pipeline_id,
                "run_id": publication.run_id,
            }
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    execute(connection, f"ALTER SESSION SET QUERY_TAG = {_literal(tag)}")


def _ensure_target(
    connection: SnowflakeConnection,
    target: WriteTarget,
    schema: RelationSchema,
    *,
    evolution: SchemaEvolution,
) -> None:
    relation = target.relation_ref
    execute(
        connection,
        f"CREATE SCHEMA IF NOT EXISTS {_qualified(relation.catalog, relation.namespace)}",
    )
    definitions = ", ".join(
        _column_definition(
            field,
            required=field.cardinality is FieldCardinality.REQUIRED
            or field.name in target.business_key,
        )
        for field in schema.fields
    )
    execute(
        connection,
        f"CREATE TABLE IF NOT EXISTS {_target(target)} ({definitions})",
    )
    described = execute(connection, f"DESCRIBE TABLE {_target(target)}", fetch="all")
    deployed = _described_columns(described.rows)
    expected = {field.name: field for field in schema.fields}
    extras = sorted(set(deployed) - set(expected))
    if extras:
        raise SnowflakeWriteError(f"Deployed target has undeclared column {extras[0]!r}")
    for field in schema.fields:
        current = deployed.get(field.name)
        required = (
            field.cardinality is FieldCardinality.REQUIRED or field.name in target.business_key
        )
        expected_type = _normalize_type(_snowflake_type(field.data_type))
        if current is None:
            if evolution is not SchemaEvolution.ADDITIVE:
                raise SnowflakeWriteError(f"Deployed target is missing column {field.name!r}")
            if required:
                raise SnowflakeWriteError(
                    f"Cannot add required Snowflake column {field.name!r} automatically"
                )
            execute(
                connection,
                f"ALTER TABLE {_target(target)} ADD COLUMN {_quote(field.name)} {expected_type}",
            )
            continue
        deployed_type, deployed_required = current
        if _normalize_type(deployed_type) != expected_type:
            raise SnowflakeWriteError(f"Snowflake type drift for column {field.name!r}")
        if deployed_required != required:
            raise SnowflakeWriteError(f"Snowflake nullability drift for column {field.name!r}")


def _described_columns(rows: Sequence[object]) -> dict[str, tuple[str, bool]]:
    deployed: dict[str, tuple[str, bool]] = {}
    for raw in rows:
        if isinstance(raw, Mapping):
            name = raw.get("name")
            data_type = raw.get("type")
            nullable = raw.get("null?")
        elif isinstance(raw, (tuple, list)) and len(raw) >= 4:
            name, data_type, nullable = raw[0], raw[1], raw[3]
        else:
            raise SnowflakeWriteError("Snowflake DESCRIBE TABLE returned an invalid shape")
        if not isinstance(name, str) or not isinstance(data_type, str):
            raise SnowflakeWriteError("Snowflake DESCRIBE TABLE returned an invalid column")
        if isinstance(nullable, str):
            is_required = nullable.upper() == "N"
        elif isinstance(nullable, bool):
            is_required = not nullable
        else:
            raise SnowflakeWriteError("Snowflake DESCRIBE TABLE returned invalid nullability")
        deployed[name] = (data_type, is_required)
    return deployed


def _ensure_load_history(connection: SnowflakeConnection, target: WriteTarget) -> None:
    execute(
        connection,
        f"CREATE TABLE IF NOT EXISTS {_load_history(target)} ("
        '"target_id" VARCHAR NOT NULL, "pipeline_id" VARCHAR NOT NULL, '
        '"run_id" VARCHAR NOT NULL, "sha256" VARCHAR NOT NULL, '
        '"rows" NUMBER(38,0) NOT NULL, "compressed_bytes" NUMBER(38,0) NOT NULL, '
        '"loaded_at" TIMESTAMP_TZ NOT NULL)',
    )


def _artifact_committed(
    connection: SnowflakeConnection,
    target: WriteTarget,
    publication: TargetFence,
    checksum: str,
) -> bool:
    result = execute(
        connection,
        f'SELECT 1 FROM {_load_history(target)} WHERE "target_id" = ? '
        'AND "pipeline_id" = ? AND "run_id" = ? AND "sha256" = ? LIMIT 1',
        (publication.target_id, publication.pipeline_id, publication.run_id, checksum),
        fetch="one",
    )
    return result.row is not None


def _load_history_insert_sql(target: WriteTarget) -> str:
    return (
        f"INSERT INTO {_load_history(target)} "
        '("target_id", "pipeline_id", "run_id", "sha256", "rows", '
        '"compressed_bytes", "loaded_at") SELECT ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP() '
        f"WHERE NOT EXISTS (SELECT 1 FROM {_load_history(target)} WHERE "
        '"target_id" = ? AND "pipeline_id" = ? AND "run_id" = ? AND "sha256" = ?)'
    )


def _create_stage_sql(stage: str) -> str:
    return f"CREATE OR REPLACE TEMPORARY STAGE {stage} FILE_FORMAT = ({_parquet_file_format()})"


def _create_staging_table_sql(
    relation: str,
    schema: RelationSchema,
) -> str:
    definitions = ", ".join(
        _column_definition(
            field,
            required=field.cardinality is FieldCardinality.REQUIRED,
        )
        for field in schema.fields
    )
    return f"CREATE OR REPLACE TEMPORARY TABLE {relation} ({definitions})"


def _put_sql(path: Path, stage: str) -> str:
    return (
        f"PUT {_literal(path.resolve().as_uri())} @{stage} AUTO_COMPRESS = FALSE OVERWRITE = FALSE"
    )


def _copy_sql(staging_relation: str, stage: str, filename: str) -> str:
    return (
        f"COPY INTO {staging_relation} FROM @{stage} FILES = ({_literal(filename)}) "
        f"FILE_FORMAT = ({_parquet_file_format()}) "
        "MATCH_BY_COLUMN_NAME = CASE_SENSITIVE "
        "ON_ERROR = ABORT_STATEMENT FORCE = FALSE"
    )


def _parquet_file_format() -> str:
    return "TYPE = PARQUET USE_LOGICAL_TYPE = TRUE BINARY_AS_TEXT = FALSE"


def _publication_statements(
    target: WriteTarget,
    staging_relation: str,
    fields: Sequence[CanonicalField],
    *,
    mode: WriteMode,
    cursor_field: str | None,
    snapshot_field: str | None,
) -> tuple[tuple[str, Sequence[object]], ...]:
    if mode is WriteMode.REPLACE:
        return _replace_statements(target, staging_relation, fields)
    if mode is WriteMode.SNAPSHOT:
        assert snapshot_field is not None
        return ((_snapshot_sql(target, staging_relation, fields, snapshot_field), ()),)
    if mode is WriteMode.SCD2:
        return _scd2_statements(target, staging_relation, fields)
    return (
        (
            _merge_sql(
                target,
                staging_relation,
                fields,
                cursor_field=cursor_field if mode is WriteMode.INCREMENTAL else None,
            ),
            (),
        ),
    )


def _deduplicated_source(
    target: WriteTarget,
    staging_relation: str,
    fields: Sequence[CanonicalField],
    *,
    cursor_field: str | None = None,
) -> str:
    names = tuple(field.name for field in fields)
    partition = ", ".join(_quote(key) for key in target.business_key)
    ordering = (
        f"{_quote(cursor_field)} DESC NULLS LAST, {_quote(_ORDINAL)} DESC"
        if cursor_field is not None
        else f"{_quote(_ORDINAL)} DESC"
    )
    return (
        f"SELECT {', '.join(_quote(name) for name in names)} FROM {staging_relation} "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition} "
        f"ORDER BY {ordering}) = 1"
    )


def _merge_sql(
    target: WriteTarget,
    staging_relation: str,
    fields: Sequence[CanonicalField],
    *,
    cursor_field: str | None,
) -> str:
    names = tuple(field.name for field in fields)
    selected = ", ".join(f"incoming.{_quote(name)}" for name in names)
    target_names = ", ".join(_quote(name) for name in names)
    match = " AND ".join(
        f"target.{_quote(key)} = incoming.{_quote(key)}" for key in target.business_key
    )
    mutable = tuple(name for name in names if name not in target.business_key)
    condition = (
        f" AND incoming.{_quote(cursor_field)} >= target.{_quote(cursor_field)}"
        if cursor_field is not None
        else ""
    )
    matched = (
        f" WHEN MATCHED{condition} THEN UPDATE SET "
        + ", ".join(f"target.{_quote(name)} = incoming.{_quote(name)}" for name in mutable)
        if mutable
        else ""
    )
    source = _deduplicated_source(
        target,
        staging_relation,
        fields,
        cursor_field=cursor_field,
    )
    return (
        f"MERGE INTO {_target(target)} AS target USING (SELECT "
        f"* FROM ({source})) "
        f"AS incoming ON {match}"
        f"{matched} WHEN NOT MATCHED THEN INSERT ({target_names}) VALUES ({selected})"
    )


def _replace_statements(
    target: WriteTarget,
    staging_relation: str,
    fields: Sequence[CanonicalField],
) -> tuple[tuple[str, Sequence[object]], ...]:
    columns = ", ".join(_quote(field.name) for field in fields)
    return (
        (f"DELETE FROM {_target(target)}", ()),
        (
            f"INSERT INTO {_target(target)} ({columns}) SELECT {columns} FROM {staging_relation}",
            (),
        ),
    )


def _snapshot_sql(
    target: WriteTarget,
    staging_relation: str,
    fields: Sequence[CanonicalField],
    snapshot_field: str,
) -> str:
    names = tuple(field.name for field in fields)
    columns = ", ".join(_quote(name) for name in names)
    selected = ", ".join(f"incoming.{_quote(name)}" for name in names)
    identical = " AND ".join(
        f"EQUAL_NULL(existing.{_quote(name)}, incoming.{_quote(name)})" for name in names
    )
    return (
        f"INSERT INTO {_target(target)} ({columns}) SELECT DISTINCT {selected} "
        f"FROM {staging_relation} AS incoming WHERE incoming.{_quote(snapshot_field)} IS NOT NULL "
        f"AND NOT EXISTS (SELECT 1 FROM {_target(target)} AS existing WHERE {identical})"
    )


def _scd2_statements(
    target: WriteTarget,
    staging_relation: str,
    fields: Sequence[CanonicalField],
) -> tuple[tuple[str, Sequence[object]], ...]:
    names = tuple(field.name for field in fields)
    incoming = _deduplicated_source(target, staging_relation, fields)
    match = " AND ".join(
        f"current.{_quote(key)} = incoming.{_quote(key)}" for key in target.business_key
    )
    mutable = tuple(name for name in names if name not in target.business_key)
    changed = (
        " OR ".join(
            f"NOT EQUAL_NULL(current.{_quote(name)}, incoming.{_quote(name)})" for name in mutable
        )
        or "FALSE"
    )
    close = (
        f"UPDATE {_target(target)} AS current SET "
        f"{_quote(_SCD2_VALID_TO)} = $DANDER_SCD2_EFFECTIVE_AT, "
        f"{_quote(_SCD2_IS_CURRENT)} = FALSE FROM ({incoming}) AS incoming WHERE {match} "
        f"AND current.{_quote(_SCD2_IS_CURRENT)} = TRUE AND ({changed})"
    )
    columns = ", ".join(
        [
            *(_quote(name) for name in names),
            _quote(_SCD2_VALID_FROM),
            _quote(_SCD2_VALID_TO),
            _quote(_SCD2_IS_CURRENT),
        ]
    )
    values = ", ".join(
        [
            *(f"incoming.{_quote(name)}" for name in names),
            "$DANDER_SCD2_EFFECTIVE_AT",
            "NULL",
            "TRUE",
        ]
    )
    current_match = " AND ".join(
        f"current.{_quote(key)} = incoming.{_quote(key)}" for key in target.business_key
    )
    insert = (
        f"INSERT INTO {_target(target)} ({columns}) SELECT {values} FROM ({incoming}) AS incoming "
        f"WHERE NOT EXISTS (SELECT 1 FROM {_target(target)} AS current WHERE {current_match} "
        f"AND current.{_quote(_SCD2_IS_CURRENT)} = TRUE)"
    )
    return ((close, ()), (insert, ()))


def _no_op_sql(target: WriteTarget) -> str:
    column = _quote(target.schema[0].name)
    return f"UPDATE {_target(target)} SET {column} = {column} WHERE FALSE"


def _empty_publication_sql(target: WriteTarget, mode: WriteMode) -> str:
    if mode is WriteMode.REPLACE:
        return f"DELETE FROM {_target(target)}"
    return _no_op_sql(target)


def _cleanup_remote(
    connection: SnowflakeConnection,
    *,
    stage: str,
    staging_relation: str,
) -> None:
    active_error = sys.exc_info()[0] is not None
    failures = 0
    for statement in (
        f"DROP TABLE IF EXISTS {staging_relation}",
        f"DROP STAGE IF EXISTS {stage}",
    ):
        try:
            execute(connection, statement)
        except Exception:
            failures += 1
    if failures and not active_error:
        raise SnowflakeWriteError("Snowflake run-scoped staging cleanup failed")


def _column_definition(
    field: CanonicalField,
    *,
    required: bool,
) -> str:
    data_type = _snowflake_type(field.data_type)
    return f"{_quote(field.name)} {data_type}{' NOT NULL' if required else ''}"


def _snowflake_type(data_type: CanonicalType) -> str:
    match data_type.kind:
        case LogicalTypeKind.BOOLEAN:
            return "BOOLEAN"
        case LogicalTypeKind.INTEGER:
            return "NUMBER(38,0)"
        case LogicalTypeKind.DECIMAL:
            if data_type.precision is None or data_type.scale is None:
                raise SnowflakeWriteError("Snowflake decimal requires precision and scale")
            if data_type.precision > 38:
                raise SnowflakeWriteError("Snowflake decimal precision cannot exceed 38")
            return f"NUMBER({data_type.precision},{data_type.scale})"
        case LogicalTypeKind.FLOAT:
            return "FLOAT"
        case LogicalTypeKind.STRING:
            return "VARCHAR"
        case LogicalTypeKind.BINARY:
            return "BINARY"
        case LogicalTypeKind.DATE:
            return "DATE"
        case LogicalTypeKind.TIME:
            return f"TIME({min(data_type.fractional_second_precision or 0, 9)})"
        case LogicalTypeKind.TIMESTAMP:
            flavor = "TZ" if data_type.with_timezone else "NTZ"
            return f"TIMESTAMP_{flavor}({min(data_type.fractional_second_precision or 0, 9)})"
        case LogicalTypeKind.JSON | LogicalTypeKind.ARRAY | LogicalTypeKind.RECORD:
            raise SnowflakeWriteError(
                "Snowflake semi-structured fields are not supported in this experimental slice"
            )
    raise AssertionError("Unhandled canonical Snowflake type")


def _normalize_type(value: str) -> str:
    compact = "".join(value.upper().split())
    if compact.startswith("VARCHAR("):
        return "VARCHAR"
    if compact.startswith("BINARY("):
        return "BINARY"
    return compact


def _target(target: WriteTarget) -> str:
    return _qualified(*target.relation_ref.coordinates)


def _load_history(target: WriteTarget) -> str:
    relation = target.relation_ref
    return _qualified(relation.catalog, relation.namespace, "dander_stage_loads")


def _qualified(*coordinates: str) -> str:
    return ".".join(_quote(coordinate) for coordinate in coordinates)


def _quote(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


__all__ = [
    "SnowflakeScd1Writer",
    "SnowflakeStagedWriter",
    "SnowflakeStagingSettings",
    "SnowflakeWriteError",
    "default_staging_settings",
]
