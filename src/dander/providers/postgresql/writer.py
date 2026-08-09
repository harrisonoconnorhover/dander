"""PostgreSQL schema management and bounded COPY-backed SCD1 writes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from psycopg import Connection, sql
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from dander.warehouse import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    RelationSchema,
)
from dander.writer import SchemaEvolution, WriteMode, WritePattern, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from dander.providers.postgresql.fence import PostgreSQLTargetFence

PostgreSQLRow = dict[str, object]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


class PostgreSQLWriteError(ValueError):
    """Raised when records or a deployed relation violate the PostgreSQL contract."""


@dataclass(frozen=True, slots=True)
class PostgreSQLTimeouts:
    """Transaction-local PostgreSQL timeout values in milliseconds."""

    statement_ms: int
    lock_ms: int
    idle_transaction_ms: int


class PostgreSQLScd1Writer(WritePattern):
    """Stream records through COPY and publish keyed rows transactionally."""

    mode = WriteMode.SCD1
    supports_batched_writes = True
    requires_publication_fence = True

    def __init__(
        self,
        *,
        database: str,
        pool: PostgreSQLPool,
        target_fence: PostgreSQLTargetFence,
        schema_evolution: SchemaEvolution = SchemaEvolution.STRICT,
        timeouts: PostgreSQLTimeouts,
    ) -> None:
        self._database = database
        self._pool = pool
        self._target_fence = target_fence
        self._schema_evolution = schema_evolution
        self._timeouts = timeouts

    def write(self, records: Iterable[Mapping[str, object]], target: WriteTarget) -> int:
        """COPY one bounded batch and upsert its deterministic last record per key."""
        _validate_target(target, database=self._database)
        publication_fence = target.publication_fence
        assert publication_fence is not None
        schema = target.canonical_schema
        fields = {field.name: field for field in schema.fields}
        missing_keys = sorted(set(target.business_key) - set(fields))
        if missing_keys:
            raise PostgreSQLWriteError(
                f"Business-key column {missing_keys[0]!r} is absent from the declared schema"
            )

        with self._pool.connection() as connection, connection.transaction():
            _set_timeouts(connection, self._timeouts)
            _ensure_target(
                connection,
                target,
                schema,
                evolution=self._schema_evolution,
            )
            staging = f"dander_stage_{uuid4().hex}"
            _create_staging(connection, staging, schema, target.business_key)
            written = _copy_records(
                connection,
                staging,
                records,
                schema.fields,
                target.business_key,
            )
            if written == 0:
                no_op = sql.SQL("UPDATE {} SET {} = {} WHERE FALSE").format(
                    _target_identifier(target),
                    sql.Identifier(target.business_key[0]),
                    sql.Identifier(target.business_key[0]),
                )
                self._target_fence.execute_dml(
                    connection,
                    no_op.as_string(connection),
                    publication_fence,
                )
                return 0
            finalizer = _scd1_sql(target, staging, schema.fields)
            cursor = self._target_fence.execute_dml(
                connection,
                finalizer.as_string(connection),
                publication_fence,
            )
            return max(cursor.rowcount, 0)


def _validate_target(target: WriteTarget, *, database: str) -> None:
    if target.project != database:
        raise PostgreSQLWriteError(
            f"Writer database {database!r} does not match target catalog {target.project!r}"
        )
    if not target.business_key:
        raise PostgreSQLWriteError("PostgreSQL SCD1 writes require a business key")
    if not target.schema:
        raise PostgreSQLWriteError("PostgreSQL writes require a declared schema")
    publication_fence = target.publication_fence
    if publication_fence is None:
        raise PostgreSQLWriteError("PostgreSQL hosted writes require a destination target fence")
    expected_target = ".".join(target.relation_ref.coordinates)
    expected_fence_table = f"{target.dataset}.dander_target_commits"
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
) -> None:
    connection.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(target.dataset))
    )
    definitions = [
        sql.SQL("{} {}{}").format(
            sql.Identifier(field.name),
            sql.SQL(_postgresql_type(field.data_type)),
            sql.SQL(" NOT NULL" if _requires_value(field, target.business_key) else ""),
        )
        for field in schema.fields
    ]
    connection.execute(
        sql.SQL("CREATE TABLE IF NOT EXISTS {} ({}, PRIMARY KEY ({}))").format(
            _target_identifier(target),
            sql.SQL(", ").join(definitions),
            sql.SQL(", ").join(sql.Identifier(key) for key in target.business_key),
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

    index_name = _unique_index_name(target)
    connection.execute(
        sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} ({})").format(
            sql.Identifier(index_name),
            _target_identifier(target),
            sql.SQL(", ").join(sql.Identifier(key) for key in target.business_key),
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
        (target.dataset, target.table),
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


def _copy_records(
    connection: Connection[PostgreSQLRow],
    staging: str,
    records: Iterable[Mapping[str, object]],
    fields: Sequence[CanonicalField],
    business_key: Sequence[str],
) -> int:
    names = tuple(field.name for field in fields)
    expected = set(names)
    copied = 0
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(staging),
        sql.SQL(", ").join(sql.Identifier(name) for name in names),
    )
    with connection.cursor().copy(statement) as copy:
        for index, record in enumerate(records):
            if set(record) != expected:
                raise PostgreSQLWriteError(
                    f"Record {index} does not match the declared PostgreSQL schema"
                )
            if any(record[key] is None for key in business_key):
                raise PostgreSQLWriteError(f"Record {index} has a null business-key value")
            copy.write_row(
                tuple(_postgresql_value(record[field.name], field.data_type) for field in fields)
            )
            copied += 1
    return copied


def _scd1_sql(
    target: WriteTarget,
    staging: str,
    fields: Sequence[CanonicalField],
) -> sql.Composed:
    names = tuple(field.name for field in fields)
    mutable = tuple(name for name in names if name not in target.business_key)
    conflict = (
        sql.SQL("DO UPDATE SET {} ").format(
            sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(
                    sql.Identifier(name),
                    sql.Identifier(name),
                )
                for name in mutable
            )
        )
        if mutable
        else sql.SQL("DO NOTHING ")
    )
    selected = sql.SQL(", ").join(sql.Identifier(name) for name in names)
    keys = sql.SQL(", ").join(sql.Identifier(key) for key in target.business_key)
    ordering = sql.SQL(", ").join(
        [*(sql.Identifier(key) for key in target.business_key), sql.Identifier("_dander_ordinal")]
    )
    return sql.SQL(
        "INSERT INTO {} ({}) SELECT {} FROM ("
        "SELECT DISTINCT ON ({}) {}, {} FROM {} ORDER BY {} DESC"
        ") AS incoming ON CONFLICT ({}) {}RETURNING 1"
    ).format(
        _target_identifier(target),
        selected,
        selected,
        keys,
        selected,
        sql.Identifier("_dander_ordinal"),
        sql.Identifier(staging),
        ordering,
        keys,
        conflict,
    )


def _target_identifier(target: WriteTarget) -> sql.Identifier:
    return sql.Identifier(target.dataset, target.table)


def _unique_index_name(target: WriteTarget) -> str:
    digest = hashlib.sha256(
        f"{target.dataset}.{target.table}:{','.join(target.business_key)}".encode()
    ).hexdigest()[:10]
    prefix = f"dander_uq_{target.table}"[:51]
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
    "PostgreSQLScd1Writer",
    "PostgreSQLTimeouts",
    "PostgreSQLWriteError",
]
