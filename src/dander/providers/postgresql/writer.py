"""PostgreSQL schema management and bounded, fenced COPY-backed writes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from itertools import chain
from time import perf_counter_ns
from typing import TYPE_CHECKING
from uuid import uuid4

from psycopg import Connection, sql
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.warehouse import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    RelationSchema,
    StagingArtifactError,
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
    from collections.abc import Iterable, Mapping, Sequence

    from dander.providers.postgresql.fence import PostgreSQLTargetFence

PostgreSQLRow = dict[str, object]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]

_ORDINAL = "_dander_ordinal"
_SCD2_VALID_FROM = "valid_from"
_SCD2_VALID_TO = "valid_to"
_SCD2_IS_CURRENT = "is_current"
_SCD2_SYSTEM_FIELDS = frozenset({_SCD2_VALID_FROM, _SCD2_VALID_TO, _SCD2_IS_CURRENT})


class PostgreSQLWriteError(ValueError):
    """Raised when records or a deployed relation violate the PostgreSQL contract."""


@dataclass(frozen=True, slots=True)
class PostgreSQLTimeouts:
    """Transaction-local PostgreSQL timeout values in milliseconds."""

    statement_ms: int
    lock_ms: int
    idle_transaction_ms: int


@dataclass(frozen=True, slots=True)
class _DirectBatch:
    records: tuple[dict[str, object], ...]
    logical_bytes: int


class PostgreSQLCopyWriter(WritePattern):
    """Select bounded direct inserts or COPY and publish transactionally."""

    requires_publication_fence = True

    def __init__(
        self,
        *,
        database: str,
        pool: PostgreSQLPool,
        target_fence: PostgreSQLTargetFence,
        schema_evolution: SchemaEvolution = SchemaEvolution.STRICT,
        timeouts: PostgreSQLTimeouts,
        mode: WriteMode,
        cursor_field: str | None = None,
        snapshot_field: str | None = None,
        direct_max_rows: int = 0,
        direct_max_logical_bytes: int = 0,
    ) -> None:
        self._database = database
        self._pool = pool
        self._target_fence = target_fence
        self._schema_evolution = schema_evolution
        self._timeouts = timeouts
        self.mode = mode
        self._cursor_field = cursor_field
        self._snapshot_field = snapshot_field
        if (direct_max_rows == 0) != (direct_max_logical_bytes == 0):
            raise ValueError("PostgreSQL direct row and byte limits must both be zero or positive")
        if direct_max_rows < 0 or direct_max_logical_bytes < 0:
            raise ValueError("PostgreSQL direct row and byte limits must be non-negative")
        self._direct_max_rows = direct_max_rows
        self._direct_max_logical_bytes = direct_max_logical_bytes
        direct_enabled = direct_max_rows > 0
        self.supports_batched_writes = not direct_enabled and mode in {
            WriteMode.SCD1,
            WriteMode.INCREMENTAL,
            WriteMode.SNAPSHOT,
        }
        self.accepts_streaming_input = not self.supports_batched_writes
        self._telemetry: list[OperationTelemetry] = []

    def drain_telemetry(self) -> tuple[OperationTelemetry, ...]:
        """Return the selected physical load operation exactly once."""
        operations = tuple(self._telemetry)
        self._telemetry.clear()
        return operations

    def write(self, records: Iterable[Mapping[str, object]], target: WriteTarget) -> int:
        """Load one bounded endpoint and publish it behind the target fence."""
        self._telemetry.clear()
        _validate_target(
            target,
            database=self._database,
            mode=self.mode,
            cursor_field=self._cursor_field,
            snapshot_field=self._snapshot_field,
        )
        publication_fence = target.publication_fence
        assert publication_fence is not None
        schema = target.canonical_schema
        target_schema = _target_schema_for_mode(schema, self.mode)
        try:
            direct, remaining = _select_direct_batch(
                records,
                schema,
                max_rows=self._direct_max_rows,
                max_logical_bytes=self._direct_max_logical_bytes,
            )
        except StagingArtifactError as error:
            raise PostgreSQLWriteError(str(error)) from error

        with self._pool.connection() as connection, connection.transaction():
            _set_timeouts(connection, self._timeouts)
            _ensure_target(
                connection,
                target,
                target_schema,
                evolution=self._schema_evolution,
                mode=self.mode,
            )
            staging = f"dander_stage_{uuid4().hex}"
            _create_staging(connection, staging, schema, target.business_key)
            started = perf_counter_ns()
            if direct is None:
                transport = WriteTransport.COPY
                logical_bytes = 0
                written = _copy_records(
                    connection,
                    staging,
                    remaining,
                    schema.fields,
                    target.business_key,
                    cursor_field=self._cursor_field,
                    snapshot_field=self._snapshot_field,
                )
            else:
                transport = WriteTransport.DIRECT
                logical_bytes = direct.logical_bytes
                written = _insert_records(
                    connection,
                    staging,
                    direct.records,
                    schema.fields,
                    target.business_key,
                    cursor_field=self._cursor_field,
                    snapshot_field=self._snapshot_field,
                )
            load_duration_ms = max((perf_counter_ns() - started) // 1_000_000, 0)
            statements = _publication_statements(
                target,
                staging,
                schema.fields,
                mode=self.mode,
                cursor_field=self._cursor_field,
                has_rows=written > 0,
            )
            cursor = self._target_fence.execute_statements(
                connection,
                statements,
                publication_fence,
            )
            affected = max(cursor.rowcount, 0)
            operation = OperationTelemetry(
                provider="postgresql",
                operation=TelemetryOperation.LOAD,
                duration_ms=load_duration_ms,
                rows_written=written,
                bytes_written=logical_bytes,
                transport=transport,
            )
        self._telemetry.append(operation)
        return affected


class PostgreSQLScd1Writer(PostgreSQLCopyWriter):
    """Compatibility name for the original PostgreSQL SCD1 writer."""

    def __init__(
        self,
        *,
        database: str,
        pool: PostgreSQLPool,
        target_fence: PostgreSQLTargetFence,
        schema_evolution: SchemaEvolution = SchemaEvolution.STRICT,
        timeouts: PostgreSQLTimeouts,
    ) -> None:
        super().__init__(
            database=database,
            pool=pool,
            target_fence=target_fence,
            schema_evolution=schema_evolution,
            timeouts=timeouts,
            mode=WriteMode.SCD1,
        )


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
        raise PostgreSQLWriteError(
            f"Writer database {database!r} does not match target catalog {relation.catalog!r}"
        )
    if not target.canonical_schema.fields:
        raise PostgreSQLWriteError("PostgreSQL writes require a declared schema")
    fields = {field.name for field in target.canonical_schema.fields}
    missing_keys = sorted(set(target.business_key) - fields)
    if missing_keys:
        raise PostgreSQLWriteError(
            f"Business-key column {missing_keys[0]!r} is absent from the declared schema"
        )
    if mode in {WriteMode.SCD1, WriteMode.SCD2, WriteMode.INCREMENTAL} and not target.business_key:
        raise PostgreSQLWriteError(f"PostgreSQL {mode.value.upper()} writes require a business key")
    if mode is WriteMode.INCREMENTAL:
        if cursor_field is None:
            raise PostgreSQLWriteError("PostgreSQL incremental writes require cursor_field")
        if cursor_field not in fields:
            raise PostgreSQLWriteError("PostgreSQL incremental cursor field is undeclared")
    elif cursor_field is not None:
        raise PostgreSQLWriteError("cursor_field is valid only for PostgreSQL incremental writes")
    if mode is WriteMode.SNAPSHOT:
        if snapshot_field is None:
            raise PostgreSQLWriteError("PostgreSQL snapshot writes require snapshot_field")
        if snapshot_field not in fields:
            raise PostgreSQLWriteError("PostgreSQL snapshot field is undeclared")
    elif snapshot_field is not None:
        raise PostgreSQLWriteError("snapshot_field is valid only for PostgreSQL snapshot writes")
    if mode is WriteMode.SCD2 and (collision := sorted(fields & _SCD2_SYSTEM_FIELDS)):
        raise PostgreSQLWriteError(f"Declared schema reserves Dander field {collision[0]!r}")
    publication_fence = target.publication_fence
    if publication_fence is None:
        raise PostgreSQLWriteError("PostgreSQL hosted writes require a destination target fence")
    expected_target = ".".join(target.relation_ref.coordinates)
    expected_fence_table = f"{relation.namespace}.dander_target_commits"
    if (
        publication_fence.target_id != expected_target
        or publication_fence.fence_table != expected_fence_table
    ):
        raise PostgreSQLWriteError("PostgreSQL destination target fence does not match the target")


def _set_timeouts(
    connection: Connection[PostgreSQLRow],
    timeouts: PostgreSQLTimeouts,
) -> None:
    for name, value in (
        ("statement_timeout", timeouts.statement_ms),
        ("lock_timeout", timeouts.lock_ms),
        ("idle_in_transaction_session_timeout", timeouts.idle_transaction_ms),
    ):
        connection.execute("SELECT set_config(%s, %s, true)", (name, str(value)))


def _ensure_target(
    connection: Connection[PostgreSQLRow],
    target: WriteTarget,
    schema: RelationSchema,
    *,
    evolution: SchemaEvolution,
    mode: WriteMode,
) -> None:
    connection.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
            sql.Identifier(target.relation_ref.namespace)
        )
    )
    definitions = [
        sql.SQL("{} {}{}").format(
            sql.Identifier(field.name),
            sql.SQL(_postgresql_type(field.data_type)),
            sql.SQL(" NOT NULL" if _requires_value(field, target.business_key) else ""),
        )
        for field in schema.fields
    ]
    constraints: list[sql.SQL | sql.Composed] = list(definitions)
    if mode in {WriteMode.SCD1, WriteMode.INCREMENTAL}:
        constraints.append(
            sql.SQL("PRIMARY KEY ({})").format(
                sql.SQL(", ").join(sql.Identifier(key) for key in target.business_key)
            )
        )
    connection.execute(
        sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
            _target_identifier(target),
            sql.SQL(", ").join(constraints),
        )
    )
    deployed = _deployed_columns(connection, target)
    expected = {field.name: field for field in schema.fields}
    extras = sorted(set(deployed) - set(expected))
    if extras:
        raise PostgreSQLWriteError(f"Deployed target has undeclared column {extras[0]!r}")
    for field in schema.fields:
        current = deployed.get(field.name)
        if current is None:
            if evolution is not SchemaEvolution.ADDITIVE:
                raise PostgreSQLWriteError(f"Deployed target is missing column {field.name!r}")
            if _requires_value(field, target.business_key):
                raise PostgreSQLWriteError(
                    f"Cannot add required PostgreSQL column {field.name!r} automatically"
                )
            connection.execute(
                sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
                    _target_identifier(target),
                    sql.Identifier(field.name),
                    sql.SQL(_postgresql_type(field.data_type)),
                )
            )
            continue
        deployed_type, deployed_required = current
        expected_type = _normalize_type(_postgresql_type(field.data_type))
        expected_required = _requires_value(field, target.business_key)
        if _normalize_type(deployed_type) != expected_type:
            raise PostgreSQLWriteError(f"PostgreSQL type drift for column {field.name!r}")
        if deployed_required != expected_required:
            raise PostgreSQLWriteError(f"PostgreSQL nullability drift for column {field.name!r}")

    if mode in {WriteMode.SCD1, WriteMode.INCREMENTAL, WriteMode.SCD2}:
        where = (
            sql.SQL(" WHERE {} IS TRUE").format(sql.Identifier(_SCD2_IS_CURRENT))
            if mode is WriteMode.SCD2
            else sql.SQL("")
        )
        connection.execute(
            sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} ({}){}").format(
                sql.Identifier(_unique_index_name(target, mode=mode)),
                _target_identifier(target),
                sql.SQL(", ").join(sql.Identifier(key) for key in target.business_key),
                where,
            )
        )


def _deployed_columns(
    connection: Connection[PostgreSQLRow],
    target: WriteTarget,
) -> dict[str, tuple[str, bool]]:
    rows = connection.execute(
        "SELECT attribute.attname AS name, "
        "pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) AS data_type, "
        "attribute.attnotnull AS required "
        "FROM pg_catalog.pg_attribute AS attribute "
        "JOIN pg_catalog.pg_class AS relation ON relation.oid = attribute.attrelid "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
        "WHERE namespace.nspname = %s AND relation.relname = %s "
        "AND attribute.attnum > 0 AND NOT attribute.attisdropped",
        (target.relation_ref.namespace, target.relation_ref.name),
    ).fetchall()
    deployed: dict[str, tuple[str, bool]] = {}
    for row in rows:
        name = row["name"]
        data_type = row["data_type"]
        required = row["required"]
        if (
            not isinstance(name, str)
            or not isinstance(data_type, str)
            or not isinstance(required, bool)
        ):
            raise PostgreSQLWriteError("PostgreSQL returned invalid relation metadata")
        deployed[name] = (data_type, required)
    return deployed


def _create_staging(
    connection: Connection[PostgreSQLRow],
    staging: str,
    schema: RelationSchema,
    business_key: Sequence[str],
) -> None:
    definitions = [
        sql.SQL("{} {}{}").format(
            sql.Identifier(field.name),
            sql.SQL(_postgresql_type(field.data_type)),
            sql.SQL(" NOT NULL" if _requires_value(field, business_key) else ""),
        )
        for field in schema.fields
    ]
    definitions.append(
        sql.SQL("{} BIGINT GENERATED ALWAYS AS IDENTITY").format(sql.Identifier("_dander_ordinal"))
    )
    connection.execute(
        sql.SQL("CREATE TEMP TABLE {} ({}) ON COMMIT DROP").format(
            sql.Identifier(staging),
            sql.SQL(", ").join(definitions),
        )
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
    prefix: list[dict[str, object]] = []
    logical_bytes = 0
    for row_index, record in enumerate(iterator):
        raw = dict(record)
        normalized = normalize_staging_record(raw, schema, row_index=row_index)
        prefix.append(raw)
        logical_bytes += staging_logical_size(normalized)
        if len(prefix) > max_rows or logical_bytes > max_logical_bytes:
            return None, chain(prefix, iterator)
    return _DirectBatch(records=tuple(prefix), logical_bytes=logical_bytes), ()


def _insert_records(
    connection: Connection[PostgreSQLRow],
    staging: str,
    records: Iterable[Mapping[str, object]],
    fields: Sequence[CanonicalField],
    business_key: Sequence[str],
    *,
    cursor_field: str | None,
    snapshot_field: str | None,
) -> int:
    names = tuple(field.name for field in fields)
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(staging),
        sql.SQL(", ").join(sql.Identifier(name) for name in names),
        sql.SQL(", ").join(sql.Placeholder() for _ in names),
    )
    values = tuple(
        _record_values(
            records,
            fields,
            business_key,
            cursor_field=cursor_field,
            snapshot_field=snapshot_field,
        )
    )
    if not values:
        return 0
    with connection.cursor() as cursor:
        cursor.executemany(statement, values)
    return len(values)


def _copy_records(
    connection: Connection[PostgreSQLRow],
    staging: str,
    records: Iterable[Mapping[str, object]],
    fields: Sequence[CanonicalField],
    business_key: Sequence[str],
    *,
    cursor_field: str | None,
    snapshot_field: str | None,
) -> int:
    names = tuple(field.name for field in fields)
    copied = 0
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(staging),
        sql.SQL(", ").join(sql.Identifier(name) for name in names),
    )
    with connection.cursor().copy(statement) as copy:
        for values in _record_values(
            records,
            fields,
            business_key,
            cursor_field=cursor_field,
            snapshot_field=snapshot_field,
        ):
            copy.write_row(values)
            copied += 1
    return copied


def _record_values(
    records: Iterable[Mapping[str, object]],
    fields: Sequence[CanonicalField],
    business_key: Sequence[str],
    *,
    cursor_field: str | None,
    snapshot_field: str | None,
) -> Iterable[tuple[object, ...]]:
    expected = {field.name for field in fields}
    for index, record in enumerate(records):
        if set(record) != expected:
            raise PostgreSQLWriteError(
                f"Record {index} does not match the declared PostgreSQL schema"
            )
        if any(record[key] is None for key in business_key):
            raise PostgreSQLWriteError(f"Record {index} has a null business-key value")
        if cursor_field is not None and record[cursor_field] is None:
            raise PostgreSQLWriteError(f"Record {index} has a null incremental cursor value")
        if snapshot_field is not None and record[snapshot_field] is None:
            raise PostgreSQLWriteError(f"Record {index} has a null snapshot value")
        yield tuple(_postgresql_value(record[field.name], field.data_type) for field in fields)


def _publication_statements(
    target: WriteTarget,
    staging: str,
    fields: Sequence[CanonicalField],
    *,
    mode: WriteMode,
    cursor_field: str | None,
    has_rows: bool,
) -> tuple[sql.SQL | sql.Composed, ...]:
    if not has_rows and mode is not WriteMode.REPLACE:
        return (_no_op_sql(target),)
    if mode is WriteMode.SCD1:
        return (_merge_sql(target, staging, fields, cursor_field=None),)
    if mode is WriteMode.INCREMENTAL:
        assert cursor_field is not None
        return (_merge_sql(target, staging, fields, cursor_field=cursor_field),)
    if mode is WriteMode.SNAPSHOT:
        return (_snapshot_sql(target, staging, fields),)
    if mode is WriteMode.SCD2:
        return _scd2_sql(target, staging, fields)
    if mode is WriteMode.REPLACE:
        return _replace_sql(target, staging, fields)
    raise AssertionError("Unhandled PostgreSQL write mode")


def _merge_sql(
    target: WriteTarget,
    staging: str,
    fields: Sequence[CanonicalField],
    *,
    cursor_field: str | None,
) -> sql.Composed:
    names = tuple(field.name for field in fields)
    mutable = tuple(name for name in names if name not in target.business_key)
    conflict = (
        sql.SQL("DO UPDATE SET {}").format(
            sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(
                    sql.Identifier(name),
                    sql.Identifier(name),
                )
                for name in mutable
            )
        )
        if mutable
        else sql.SQL("DO NOTHING")
    )
    if cursor_field is not None and mutable:
        conflict = sql.SQL("{} WHERE current.{} IS NULL OR EXCLUDED.{} >= current.{}").format(
            conflict,
            sql.Identifier(cursor_field),
            sql.Identifier(cursor_field),
            sql.Identifier(cursor_field),
        )
    selected = sql.SQL(", ").join(sql.Identifier(name) for name in names)
    keys = sql.SQL(", ").join(sql.Identifier(key) for key in target.business_key)
    source = _deduplicated_source(
        target,
        staging,
        fields,
        cursor_field=cursor_field,
    )
    return sql.SQL(
        "INSERT INTO {} AS current ({}) SELECT {} FROM ({}) AS incoming "
        "ON CONFLICT ({}) {} RETURNING 1"
    ).format(
        _target_identifier(target),
        selected,
        selected,
        source,
        keys,
        conflict,
    )


def _deduplicated_source(
    target: WriteTarget,
    staging: str,
    fields: Sequence[CanonicalField],
    *,
    cursor_field: str | None = None,
) -> sql.Composed:
    selected = sql.SQL(", ").join(sql.Identifier(field.name) for field in fields)
    keys = sql.SQL(", ").join(sql.Identifier(key) for key in target.business_key)
    ordering: list[sql.SQL | sql.Identifier | sql.Composed] = [
        *(sql.Identifier(key) for key in target.business_key)
    ]
    if cursor_field is not None:
        ordering.append(sql.SQL("{} DESC").format(sql.Identifier(cursor_field)))
    ordering.append(sql.SQL("{} DESC").format(sql.Identifier(_ORDINAL)))
    return sql.SQL("SELECT DISTINCT ON ({}) {} FROM {} ORDER BY {}").format(
        keys,
        selected,
        sql.Identifier(staging),
        sql.SQL(", ").join(ordering),
    )


def _snapshot_sql(
    target: WriteTarget,
    staging: str,
    fields: Sequence[CanonicalField],
) -> sql.Composed:
    selected = sql.SQL(", ").join(sql.Identifier(field.name) for field in fields)
    incoming = sql.SQL(", ").join(
        sql.SQL("incoming.{}").format(sql.Identifier(field.name)) for field in fields
    )
    match = sql.SQL(" AND ").join(
        sql.SQL("current.{} IS NOT DISTINCT FROM incoming.{}").format(
            sql.Identifier(field.name),
            sql.Identifier(field.name),
        )
        for field in fields
    )
    return sql.SQL(
        "INSERT INTO {} ({}) SELECT {} FROM (SELECT DISTINCT {} FROM {}) AS incoming "
        "WHERE NOT EXISTS (SELECT 1 FROM {} AS current WHERE {}) RETURNING 1"
    ).format(
        _target_identifier(target),
        selected,
        incoming,
        selected,
        sql.Identifier(staging),
        _target_identifier(target),
        match,
    )


def _scd2_sql(
    target: WriteTarget,
    staging: str,
    fields: Sequence[CanonicalField],
) -> tuple[sql.Composed, sql.Composed]:
    names = tuple(field.name for field in fields)
    source = _deduplicated_source(target, staging, fields)
    match = sql.SQL(" AND ").join(
        sql.SQL("current.{} = incoming.{}").format(
            sql.Identifier(key),
            sql.Identifier(key),
        )
        for key in target.business_key
    )
    mutable = tuple(name for name in names if name not in target.business_key)
    changed = (
        sql.SQL(" OR ").join(
            sql.SQL("current.{} IS DISTINCT FROM incoming.{}").format(
                sql.Identifier(name),
                sql.Identifier(name),
            )
            for name in mutable
        )
        if mutable
        else sql.SQL("FALSE")
    )
    close = sql.SQL(
        "UPDATE {} AS current SET {} = transaction_timestamp(), {} = FALSE "
        "FROM ({}) AS incoming WHERE {} AND current.{} IS TRUE AND ({})"
    ).format(
        _target_identifier(target),
        sql.Identifier(_SCD2_VALID_TO),
        sql.Identifier(_SCD2_IS_CURRENT),
        source,
        match,
        sql.Identifier(_SCD2_IS_CURRENT),
        changed,
    )
    incoming = sql.SQL(", ").join(
        sql.SQL("incoming.{}").format(sql.Identifier(name)) for name in names
    )
    columns = sql.SQL(", ").join(
        [
            *(sql.Identifier(name) for name in names),
            sql.Identifier(_SCD2_VALID_FROM),
            sql.Identifier(_SCD2_VALID_TO),
            sql.Identifier(_SCD2_IS_CURRENT),
        ]
    )
    values = sql.SQL(", ").join(
        [
            incoming,
            sql.SQL("transaction_timestamp()"),
            sql.SQL("NULL"),
            sql.SQL("TRUE"),
        ]
    )
    insert = sql.SQL(
        "INSERT INTO {} ({}) SELECT {} FROM ({}) AS incoming WHERE NOT EXISTS ("
        "SELECT 1 FROM {} AS current WHERE {} AND current.{} IS TRUE) RETURNING 1"
    ).format(
        _target_identifier(target),
        columns,
        values,
        source,
        _target_identifier(target),
        match,
        sql.Identifier(_SCD2_IS_CURRENT),
    )
    return close, insert


def _replace_sql(
    target: WriteTarget,
    staging: str,
    fields: Sequence[CanonicalField],
) -> tuple[sql.Composed, sql.Composed]:
    selected = sql.SQL(", ").join(sql.Identifier(field.name) for field in fields)
    source = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
        selected,
        sql.Identifier(staging),
        sql.Identifier(_ORDINAL),
    )
    return (
        sql.SQL("DELETE FROM {}").format(_target_identifier(target)),
        sql.SQL("INSERT INTO {} ({}) SELECT {} FROM ({}) AS incoming RETURNING 1").format(
            _target_identifier(target),
            selected,
            selected,
            source,
        ),
    )


def _no_op_sql(target: WriteTarget) -> sql.Composed:
    field = target.canonical_schema.fields[0].name
    return sql.SQL("UPDATE {} SET {} = {} WHERE FALSE").format(
        _target_identifier(target),
        sql.Identifier(field),
        sql.Identifier(field),
    )


def _target_schema_for_mode(schema: RelationSchema, mode: WriteMode) -> RelationSchema:
    if mode is not WriteMode.SCD2:
        return schema
    timestamp_type = CanonicalType(
        kind=LogicalTypeKind.TIMESTAMP,
        fractional_second_precision=6,
        with_timezone=True,
    )
    return RelationSchema(
        fields=(
            *schema.fields,
            CanonicalField(
                name=_SCD2_VALID_FROM,
                data_type=timestamp_type,
                cardinality=FieldCardinality.REQUIRED,
            ),
            CanonicalField(name=_SCD2_VALID_TO, data_type=timestamp_type),
            CanonicalField(
                name=_SCD2_IS_CURRENT,
                data_type=CanonicalType(kind=LogicalTypeKind.BOOLEAN),
                cardinality=FieldCardinality.REQUIRED,
            ),
        )
    )


def _target_identifier(target: WriteTarget) -> sql.Identifier:
    return sql.Identifier(target.relation_ref.namespace, target.relation_ref.name)


def _unique_index_name(target: WriteTarget, *, mode: WriteMode) -> str:
    mode_identity = ":scd2" if mode is WriteMode.SCD2 else ""
    digest = hashlib.sha256(
        (
            f"{target.relation_ref.namespace}.{target.relation_ref.name}:"
            f"{','.join(target.business_key)}{mode_identity}"
        ).encode()
    ).hexdigest()[:10]
    prefix = f"dander_uq_{target.relation_ref.name}"[:51]
    return f"{prefix}_{digest}"


def _requires_value(field: CanonicalField, business_key: Sequence[str]) -> bool:
    return field.cardinality is FieldCardinality.REQUIRED or field.name in business_key


def _normalize_type(value: str) -> str:
    return " ".join(value.lower().split())


def _postgresql_type(data_type: CanonicalType) -> str:
    match data_type.kind:
        case LogicalTypeKind.BOOLEAN:
            return "boolean"
        case LogicalTypeKind.INTEGER:
            if data_type.bit_width in {8, 16}:
                return "smallint"
            if data_type.bit_width == 32:
                return "integer"
            return "bigint"
        case LogicalTypeKind.DECIMAL:
            return f"numeric({data_type.precision},{data_type.scale})"
        case LogicalTypeKind.FLOAT:
            return "real" if data_type.bit_width == 32 else "double precision"
        case LogicalTypeKind.STRING:
            return "text"
        case LogicalTypeKind.BINARY:
            return "bytea"
        case LogicalTypeKind.DATE:
            return "date"
        case LogicalTypeKind.TIME:
            return f"time({data_type.fractional_second_precision}) without time zone"
        case LogicalTypeKind.TIMESTAMP:
            timezone = "with" if data_type.with_timezone else "without"
            return f"timestamp({data_type.fractional_second_precision}) {timezone} time zone"
        case LogicalTypeKind.JSON | LogicalTypeKind.RECORD:
            return "jsonb"
        case LogicalTypeKind.ARRAY:
            assert data_type.element is not None
            return f"{_postgresql_type(data_type.element)}[]"
    raise AssertionError("Unhandled canonical PostgreSQL type")


def _postgresql_value(value: object, data_type: CanonicalType) -> object:
    if value is None:
        return None
    if data_type.kind in {LogicalTypeKind.JSON, LogicalTypeKind.RECORD}:
        return Jsonb(value)
    if data_type.kind is LogicalTypeKind.ARRAY:
        assert data_type.element is not None
        if not isinstance(value, (list, tuple)):
            raise PostgreSQLWriteError("PostgreSQL array values must be lists or tuples")
        return [_postgresql_value(item, data_type.element) for item in value]
    if isinstance(value, (str, int, float, bool, bytes, Decimal, date, time, datetime)):
        return value
    raise PostgreSQLWriteError("PostgreSQL record contains an unsupported scalar value")


__all__ = [
    "PostgreSQLCopyWriter",
    "PostgreSQLScd1Writer",
    "PostgreSQLTimeouts",
    "PostgreSQLWriteError",
]
