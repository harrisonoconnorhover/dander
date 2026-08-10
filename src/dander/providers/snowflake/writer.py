"""Bounded Parquet stage/COPY and transactionally fenced Snowflake writes."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from base64 import b64encode
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from itertools import chain
from pathlib import Path
from time import perf_counter_ns
from typing import TYPE_CHECKING
from uuid import uuid4

from dander.concurrency import TargetFenceLostError
from dander.providers.snowflake.session import (
    SnowflakeConnection,
    SnowflakeConnectionFactory,
    SnowflakeStatementResult,
    execute,
    execute_many,
    open_connection,
)
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.warehouse import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    ParquetStagingSession,
    RelationSchema,
    StagingManifest,
    normalize_staging_record,
    staging_logical_size,
)
from dander.writer import (
    SchemaEvolution,
    WriteMode,
    WritePattern,
    WriteTarget,
    WriteTransport,
)

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
    direct_max_rows: int = 0
    direct_max_logical_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _DirectBatch:
    rows: tuple[dict[str, object], ...]
    logical_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _LoadOutcome:
    affected: int
    operations: tuple[OperationTelemetry, ...]


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
        warehouse: str | None = None,
        cursor_field: str | None = None,
        snapshot_field: str | None = None,
    ) -> None:
        self._database = database
        self._connection_factory = connection_factory
        self._target_fence = target_fence
        self._schema_evolution = schema_evolution
        self._staging = staging
        self._warehouse = warehouse
        self.mode = mode
        self._cursor_field = cursor_field
        self._snapshot_field = snapshot_field
        # Transport selection must see the complete logical endpoint stream. The writer
        # remains bounded because it retains at most the direct threshold plus one row.
        self.supports_batched_writes = False
        self.accepts_streaming_input = True
        self._telemetry: list[OperationTelemetry] = []

    def drain_telemetry(self) -> tuple[OperationTelemetry, ...]:
        """Return completed load/publication operations exactly once."""
        operations = tuple(self._telemetry)
        self._telemetry.clear()
        return operations

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
        validate_snowflake_schema(target_schema)
        normalization_schema = RelationSchema(
            fields=(
                *target_schema.fields,
                CanonicalField(
                    name=_ORDINAL,
                    data_type=CanonicalType(kind=LogicalTypeKind.INTEGER, bit_width=64),
                    cardinality=FieldCardinality.REQUIRED,
                ),
            )
        )
        staging_schema = RelationSchema(
            fields=tuple(_staging_field(field) for field in normalization_schema.fields)
        )
        self._telemetry.clear()
        prepared = iter(
            _with_ordinals(
                records,
                business_key=target.business_key,
                cursor_field=self._cursor_field,
                snapshot_field=self._snapshot_field,
            )
        )
        direct, remaining = _select_direct_batch(
            prepared,
            normalization_schema,
            max_rows=self._staging.direct_max_rows,
            max_logical_bytes=self._staging.direct_max_logical_bytes,
        )
        if direct is not None:
            outcome = self._load_and_publish(
                target,
                publication,
                target_schema,
                staging_schema,
                transport=WriteTransport.DIRECT,
                direct=direct,
            )
            self._telemetry.extend(outcome.operations)
            return outcome.affected

        local_id = f"snowflake-{uuid4().hex}"
        with ParquetStagingSession(
            self._staging.root,
            run_id=local_id,
            max_rows_per_file=self._staging.max_rows_per_file,
            max_logical_bytes_per_file=self._staging.max_logical_bytes_per_file,
            compression=self._staging.compression,
        ) as local:
            manifest = local.stage(
                _normalized_for_staging(remaining, normalization_schema),
                staging_schema,
            )
            outcome = self._load_and_publish(
                target,
                publication,
                target_schema,
                staging_schema,
                transport=WriteTransport.COPY,
                manifest=manifest,
            )
        self._telemetry.extend(outcome.operations)
        return outcome.affected

    def _load_and_publish(
        self,
        target: WriteTarget,
        publication: TargetFence,
        target_schema: RelationSchema,
        staging_schema: RelationSchema,
        *,
        transport: WriteTransport,
        manifest: StagingManifest | None = None,
        direct: _DirectBatch | None = None,
    ) -> _LoadOutcome:
        if (manifest is None) == (direct is None):
            raise TypeError("Snowflake writer requires exactly one staged load input")
        if manifest is not None:
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
        stage = (
            _qualified(relation.catalog, relation.namespace, stage_name)
            if transport is WriteTransport.COPY
            else None
        )
        staging_relation = _qualified(relation.catalog, relation.namespace, staging_table)
        cleanup_started = False
        operations: list[OperationTelemetry] = []
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
                if manifest is not None:
                    rows = manifest.rows
                else:
                    assert direct is not None
                    rows = len(direct.rows)
                if rows == 0:
                    result, duration_ms = _timed_call(
                        self._target_fence.execute_dml,
                        connection,
                        _empty_publication_sql(target, self.mode),
                        publication,
                    )
                    operations.append(
                        self._operation(
                            result,
                            operation=TelemetryOperation.QUERY,
                            transport=transport,
                            duration_ms=duration_ms,
                        )
                    )
                    return _LoadOutcome(
                        affected=0 if self.mode is WriteMode.REPLACE else result.rowcount,
                        operations=tuple(operations),
                    )
                if stage is not None:
                    execute(connection, _create_stage_sql(stage))
                execute(
                    connection,
                    _create_staging_table_sql(
                        staging_relation,
                        staging_schema,
                    ),
                )
                cleanup_started = True
                if manifest is not None:
                    pending = tuple(
                        artifact
                        for artifact in manifest.artifacts
                        if not _artifact_committed(
                            connection,
                            target,
                            publication,
                            artifact.sha256,
                        )
                    )
                    committed = not pending
                else:
                    assert direct is not None
                    committed = _artifact_committed(
                        connection,
                        target,
                        publication,
                        direct.sha256,
                    )
                    pending = ()
                if committed:
                    result, duration_ms = _timed_call(
                        self._target_fence.execute_dml,
                        connection,
                        _no_op_sql(target),
                        publication,
                    )
                    operations.append(
                        self._operation(
                            result,
                            operation=TelemetryOperation.QUERY,
                            transport=transport,
                            duration_ms=duration_ms,
                        )
                    )
                    return _LoadOutcome(result.rowcount, tuple(operations))
                if manifest is not None:
                    assert stage is not None
                    artifacts_to_load = (
                        manifest.artifacts if self.mode is WriteMode.REPLACE else pending
                    )
                    for artifact in artifacts_to_load:
                        execute(connection, _put_sql(artifact.path, stage))
                        load, duration_ms = _timed_call(
                            execute,
                            connection,
                            _copy_sql(staging_relation, stage, artifact.path.name),
                        )
                        operations.append(
                            self._operation(
                                load,
                                operation=TelemetryOperation.LOAD,
                                transport=transport,
                                duration_ms=duration_ms,
                                rows_written=artifact.rows,
                                bytes_written=artifact.compressed_bytes,
                            )
                        )
                else:
                    assert direct is not None
                    load, duration_ms = _timed_call(
                        execute_many,
                        connection,
                        *_direct_insert(staging_relation, staging_schema, direct.rows),
                    )
                    operations.append(
                        self._operation(
                            load,
                            operation=TelemetryOperation.LOAD,
                            transport=transport,
                            duration_ms=duration_ms,
                            rows_written=len(direct.rows),
                            bytes_written=direct.logical_bytes,
                        )
                    )
                if self.mode is WriteMode.SCD2:
                    execute(
                        connection,
                        "SET DANDER_SCD2_EFFECTIVE_AT = CURRENT_TIMESTAMP()",
                    )
                statements: list[tuple[str, Sequence[object]]] = []
                if manifest is not None:
                    statements.extend(
                        (
                            _load_history_insert_sql(target),
                            _load_history_parameters(
                                publication,
                                sha256=artifact.sha256,
                                rows=artifact.rows,
                                compressed_bytes=artifact.compressed_bytes,
                            ),
                        )
                        for artifact in pending
                    )
                else:
                    assert direct is not None
                    statements.append(
                        (
                            _load_history_insert_sql(target),
                            _load_history_parameters(
                                publication,
                                sha256=direct.sha256,
                                rows=len(direct.rows),
                                compressed_bytes=0,
                            ),
                        )
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
                result, duration_ms = _timed_call(
                    self._target_fence.execute_statements,
                    connection,
                    statements,
                    publication,
                )
                operations.append(
                    self._operation(
                        result,
                        operation=TelemetryOperation.QUERY,
                        transport=transport,
                        duration_ms=duration_ms,
                    )
                )
                return _LoadOutcome(result.rowcount, tuple(operations))
            except (SnowflakeWriteError, TargetFenceLostError):
                raise
            except Exception as error:
                raise SnowflakeWriteError(
                    f"Snowflake staged {self.mode.value.upper()} write failed"
                ) from error
            finally:
                if cleanup_started:
                    _cleanup_remote(connection, stage=stage, staging_relation=staging_relation)

    def _operation(
        self,
        result: SnowflakeStatementResult,
        *,
        operation: TelemetryOperation,
        transport: WriteTransport,
        duration_ms: int,
        rows_written: int = 0,
        bytes_written: int = 0,
    ) -> OperationTelemetry:
        return OperationTelemetry(
            provider="snowflake",
            operation=operation,
            duration_ms=duration_ms,
            rows_written=rows_written,
            rows_affected=result.rowcount,
            bytes_written=bytes_written,
            query_id=result.query_id,
            resource_name=self._warehouse,
            transport=transport,
        )


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
        warehouse: str | None = None,
    ) -> None:
        super().__init__(
            database=database,
            connection_factory=connection_factory,
            target_fence=target_fence,
            schema_evolution=schema_evolution,
            staging=staging,
            warehouse=warehouse,
            mode=WriteMode.SCD1,
        )


def default_staging_settings(
    *,
    max_rows_per_file: int,
    max_logical_bytes_per_file: int,
    compression: str,
    direct_max_rows: int = 0,
    direct_max_logical_bytes: int = 0,
) -> SnowflakeStagingSettings:
    """Return container-safe local staging defaults without creating directories."""
    return SnowflakeStagingSettings(
        root=Path(tempfile.gettempdir()) / "dander-snowflake-staging",
        max_rows_per_file=max_rows_per_file,
        max_logical_bytes_per_file=max_logical_bytes_per_file,
        compression=compression,
        direct_max_rows=direct_max_rows,
        direct_max_logical_bytes=direct_max_logical_bytes,
    )


def _select_direct_batch(
    records: Iterable[Mapping[str, object]],
    schema: RelationSchema,
    *,
    max_rows: int,
    max_logical_bytes: int,
) -> tuple[_DirectBatch | None, Iterable[Mapping[str, object]]]:
    """Choose direct only after the complete endpoint fits both bounded limits."""
    if max_rows == 0 or max_logical_bytes == 0:
        return None, records
    iterator = iter(records)
    raw_prefix: list[dict[str, object]] = []
    normalized_prefix: list[dict[str, object]] = []
    logical_bytes = 0
    for row_index, record in enumerate(iterator):
        raw = dict(record)
        normalized = normalize_staging_record(raw, schema, row_index=row_index)
        raw_prefix.append(raw)
        normalized_prefix.append(normalized)
        logical_bytes += staging_logical_size(normalized)
        if len(raw_prefix) > max_rows or logical_bytes > max_logical_bytes:
            return None, chain(raw_prefix, iterator)
    rows = tuple(normalized_prefix)
    return (
        _DirectBatch(
            rows=rows,
            logical_bytes=logical_bytes,
            sha256=_direct_checksum(schema, rows),
        ),
        (),
    )


def _normalized_for_staging(
    records: Iterable[Mapping[str, object]],
    schema: RelationSchema,
) -> Iterable[Mapping[str, object]]:
    for row_index, record in enumerate(records):
        yield normalize_staging_record(record, schema, row_index=row_index)


def _direct_checksum(
    schema: RelationSchema,
    rows: tuple[dict[str, object], ...],
) -> str:
    payload = {
        "schema": schema.model_dump(mode="json", by_alias=True),
        "rows": [[_checksum_value(row[field.name]) for field in schema.fields] for row in rows],
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checksum_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return {"float": value.hex()}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, bytes):
        return {"binary": b64encode(value).decode("ascii")}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, time):
        return {"time": value.isoformat()}
    raise SnowflakeWriteError("Snowflake direct staging received an unsupported scalar value")


def _direct_insert(
    staging_relation: str,
    schema: RelationSchema,
    rows: tuple[dict[str, object], ...],
) -> tuple[str, tuple[tuple[object, ...], ...]]:
    columns = ", ".join(_quote(field.name) for field in schema.fields)
    placeholders = ", ".join("?" for _field in schema.fields)
    statement = f"INSERT INTO {staging_relation} ({columns}) VALUES ({placeholders})"
    parameters = tuple(tuple(row[field.name] for field in schema.fields) for row in rows)
    return statement, parameters


def _load_history_parameters(
    publication: TargetFence,
    *,
    sha256: str,
    rows: int,
    compressed_bytes: int,
) -> tuple[object, ...]:
    identity = (
        publication.target_id,
        publication.pipeline_id,
        publication.run_id,
        sha256,
    )
    return (*identity, rows, compressed_bytes, *identity)


def _timed_call(
    function: Callable[..., SnowflakeStatementResult],
    *arguments: object,
) -> tuple[SnowflakeStatementResult, int]:
    started = perf_counter_ns()
    result = function(*arguments)
    duration_ms = max((perf_counter_ns() - started) // 1_000_000, 0)
    return result, duration_ms


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
    target_schema = target.canonical_schema
    field_names = {field.name for field in target_schema.fields}
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


def validate_snowflake_schema(schema: RelationSchema) -> None:
    """Validate scalar mappings plus the one explicit JSON-to-VARIANT fallback."""
    for field in schema.fields:
        _snowflake_field_type(field)


def _staging_field(field: CanonicalField) -> CanonicalField:
    if _is_variant_fallback(field):
        return field.model_copy(
            update={
                "data_type": CanonicalType(kind=LogicalTypeKind.STRING),
                "extensions": tuple(
                    extension for extension in field.extensions if extension.provider != "snowflake"
                ),
            }
        )
    return field


def _is_variant_fallback(field: CanonicalField) -> bool:
    snowflake_extensions = tuple(
        extension for extension in field.extensions if extension.provider == "snowflake"
    )
    expected = (
        len(snowflake_extensions) == 1
        and snowflake_extensions[0].name == "fallback"
        and snowflake_extensions[0].value == "variant"
    )
    if field.data_type.kind is LogicalTypeKind.JSON:
        if not expected:
            raise SnowflakeWriteError("Snowflake JSON fields require snowflake/fallback=variant")
        return True
    if field.data_type.kind in {LogicalTypeKind.ARRAY, LogicalTypeKind.RECORD}:
        raise SnowflakeWriteError(
            "Snowflake ARRAY and RECORD fields have no canonical fallback in this slice"
        )
    if snowflake_extensions:
        raise SnowflakeWriteError("Snowflake field extension is unsupported for this type")
    return False


def _snowflake_field_type(field: CanonicalField) -> str:
    if _is_variant_fallback(field):
        return "VARIANT"
    return _snowflake_type(field.data_type)


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
        expected_type = _normalize_type(_snowflake_field_type(field))
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
    source = _projected_staging_source(staging_relation, fields, include_ordinal=True)
    return (
        f"SELECT {', '.join(_quote(name) for name in names)} FROM {source} AS normalized "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition} "
        f"ORDER BY {ordering}) = 1"
    )


def _projected_staging_source(
    staging_relation: str,
    fields: Sequence[CanonicalField],
    *,
    include_ordinal: bool = False,
) -> str:
    projections = [_staging_projection(field) for field in fields]
    if include_ordinal:
        projections.append(f"staged.{_quote(_ORDINAL)} AS {_quote(_ORDINAL)}")
    return f"(SELECT {', '.join(projections)} FROM {staging_relation} AS staged)"


def _staging_projection(field: CanonicalField) -> str:
    reference = f"staged.{_quote(field.name)}"
    value = f"PARSE_JSON({reference})" if _is_variant_fallback(field) else reference
    return f"{value} AS {_quote(field.name)}"


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
    source = _projected_staging_source(staging_relation, fields)
    return (
        (f"DELETE FROM {_target(target)}", ()),
        (
            f"INSERT INTO {_target(target)} ({columns}) SELECT {columns} FROM {source}",
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
    source = _projected_staging_source(staging_relation, fields)
    return (
        f"INSERT INTO {_target(target)} ({columns}) SELECT DISTINCT {selected} "
        f"FROM {source} AS incoming WHERE incoming.{_quote(snapshot_field)} IS NOT NULL "
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
    column = _quote(target.canonical_schema.fields[0].name)
    return f"UPDATE {_target(target)} SET {column} = {column} WHERE FALSE"


def _empty_publication_sql(target: WriteTarget, mode: WriteMode) -> str:
    if mode is WriteMode.REPLACE:
        return f"DELETE FROM {_target(target)}"
    return _no_op_sql(target)


def _cleanup_remote(
    connection: SnowflakeConnection,
    *,
    stage: str | None,
    staging_relation: str,
) -> None:
    active_error = sys.exc_info()[0] is not None
    failures = 0
    statements = [f"DROP TABLE IF EXISTS {staging_relation}"]
    if stage is not None:
        statements.append(f"DROP STAGE IF EXISTS {stage}")
    for statement in statements:
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
    data_type = _snowflake_field_type(field)
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
    "validate_snowflake_schema",
]
