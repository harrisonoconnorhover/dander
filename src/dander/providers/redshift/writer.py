"""Bounded S3/Parquet COPY and transactionally fenced Redshift SCD1 writes."""

# ruff: noqa: N803 -- boto3's public S3 API uses capitalized parameter names.

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from dander.concurrency import TargetFenceLostError
from dander.providers.redshift.config import validate_redshift_relation
from dander.providers.redshift.session import (
    RedshiftConnection,
    RedshiftConnectionFactory,
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
    from dander.providers.redshift.fence import RedshiftTargetFence

_ORDINAL = "_dander_ordinal"
_MAX_PARQUET_ROW_BYTES = 4 * 1_024 * 1_024


class RedshiftWriteError(ValueError):
    """Raised when Redshift staging, schema, or publication fails closed."""


class RedshiftS3Client(Protocol):
    def get_bucket_location(  # noqa: N803
        self, *, Bucket: str
    ) -> Mapping[str, object]: ...
    def upload_file(  # noqa: N803
        self, Filename: str, Bucket: str, Key: str
    ) -> object: ...
    def put_object(  # noqa: N803
        self, *, Bucket: str, Key: str, Body: bytes
    ) -> object: ...
    def delete_objects(  # noqa: N803
        self, *, Bucket: str, Delete: Mapping[str, object]
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class RedshiftStagingSettings:
    root: Path
    bucket: str
    prefix: str
    region: str
    copy_role_arn: str
    max_rows_per_file: int
    max_logical_bytes_per_file: int
    compression: str
    statement_timeout_ms: int


@dataclass(frozen=True, slots=True)
class RedshiftPublication:
    statements: tuple[tuple[str, Sequence[object]], ...]
    affected_statement_index: int


class RedshiftScd1Writer(WritePattern):
    """Upload bounded Parquet parts and merge deterministic last-record-wins rows."""

    mode = WriteMode.SCD1
    supports_batched_writes = True
    requires_publication_fence = True

    def __init__(
        self,
        *,
        database: str,
        connection_factory: RedshiftConnectionFactory,
        s3_client: RedshiftS3Client,
        target_fence: RedshiftTargetFence,
        schema_evolution: SchemaEvolution,
        staging: RedshiftStagingSettings,
    ) -> None:
        self._database = database
        self._connection_factory = connection_factory
        self._s3 = s3_client
        self._target_fence = target_fence
        self._evolution = schema_evolution
        self._staging = staging

    def write(self, records: Iterable[Mapping[str, object]], target: WriteTarget) -> int:
        _validate_target(target, self._database)
        publication = target.publication_fence
        assert publication is not None
        schema = target.canonical_schema
        _validate_schema(schema)
        if any(field.name == _ORDINAL for field in schema.fields):
            raise RedshiftWriteError(f"Declared schema reserves Dander field {_ORDINAL!r}")
        staged_schema = RelationSchema(
            fields=(
                *schema.fields,
                CanonicalField(
                    name=_ORDINAL,
                    data_type=CanonicalType(kind=LogicalTypeKind.INTEGER, bit_width=64),
                    cardinality=FieldCardinality.REQUIRED,
                ),
            )
        )
        local_id = f"redshift-{uuid4().hex}"
        with ParquetStagingSession(
            self._staging.root,
            run_id=local_id,
            max_rows_per_file=self._staging.max_rows_per_file,
            max_logical_bytes_per_file=self._staging.max_logical_bytes_per_file,
            compression=self._staging.compression,
        ) as local:
            manifest = local.stage(_with_ordinals(records), staged_schema)
            _validate_manifest(manifest)
            return self._publish(target, publication, schema, staged_schema, manifest)

    def _publish(
        self,
        target: WriteTarget,
        publication: TargetFence,
        target_schema: RelationSchema,
        staged_schema: RelationSchema,
        manifest: StagingManifest,
    ) -> int:
        _validate_bucket_region(self._s3, self._staging.bucket, self._staging.region)
        remote_id = hashlib.sha256(
            f"{publication.target_id}:{publication.run_id}:{uuid4().hex}".encode()
        ).hexdigest()[:24]
        remote_prefix = f"{self._staging.prefix}/{remote_id}"
        temp_table = f"dander_stage_{remote_id}"
        uploaded: list[str] = []
        with open_connection(self._connection_factory) as connection:
            publication_failed = True
            try:
                _set_query_group(
                    connection,
                    publication,
                    statement_timeout_ms=self._staging.statement_timeout_ms,
                )
                if manifest.rows:
                    for artifact in manifest.artifacts:
                        key = f"{remote_prefix}/{artifact.path.name}"
                        uploaded.append(key)
                        self._s3.upload_file(str(artifact.path), self._staging.bucket, key)
                    manifest_key = f"{remote_prefix}/manifest.json"
                    uploaded.append(manifest_key)
                    self._s3.put_object(
                        Bucket=self._staging.bucket,
                        Key=manifest_key,
                        Body=_copy_manifest(self._staging.bucket, remote_prefix, manifest),
                    )
                    execute(connection, _temporary_table_sql(temp_table, staged_schema))
                    execute(
                        connection,
                        _copy_sql(
                            temp_table,
                            bucket=self._staging.bucket,
                            manifest_key=manifest_key,
                            role_arn=self._staging.copy_role_arn,
                        ),
                    )
                # COPY and SET open an implicit transaction. Close it while retaining the
                # session-scoped temp table so the fenced publication owns one transaction.
                connection.commit()
                plan = _publication_plan(
                    connection,
                    target,
                    temp_table,
                    target_schema,
                    manifest,
                    evolution=self._evolution,
                    has_rows=bool(manifest.rows),
                )
                # The schema read also opens an implicit transaction. End that read-only
                # snapshot before beginning the one fenced publication transaction.
                connection.commit()
                results = self._target_fence.execute_statements(
                    connection,
                    plan.statements,
                    publication,
                )
                affected_rows = results[plan.affected_statement_index].rowcount
                if affected_rows < 0:
                    raise RedshiftWriteError("Redshift MERGE did not report affected rows")
                publication_failed = False
                return affected_rows
            except (RedshiftWriteError, TargetFenceLostError):
                raise
            except Exception as error:
                raise RedshiftWriteError("Redshift staged SCD1 write failed") from error
            finally:
                cleanup_error: Exception | None = None
                try:
                    _drop_temporary(connection, temp_table)
                except Exception as error:
                    cleanup_error = error
                try:
                    _delete_owned(self._s3, self._staging.bucket, uploaded)
                except Exception as error:
                    cleanup_error = cleanup_error or error
                if cleanup_error is not None and not publication_failed:
                    raise cleanup_error


def default_staging_settings(
    *,
    bucket: str,
    prefix: str,
    region: str,
    copy_role_arn: str,
    max_rows_per_file: int,
    max_logical_bytes_per_file: int,
    compression: str,
    statement_timeout_ms: int,
) -> RedshiftStagingSettings:
    return RedshiftStagingSettings(
        root=Path(tempfile.gettempdir()) / "dander-redshift-staging",
        bucket=bucket,
        prefix=prefix,
        region=region,
        copy_role_arn=copy_role_arn,
        max_rows_per_file=max_rows_per_file,
        max_logical_bytes_per_file=max_logical_bytes_per_file,
        compression=compression,
        statement_timeout_ms=statement_timeout_ms,
    )


def _with_ordinals(records: Iterable[Mapping[str, object]]) -> Iterable[Mapping[str, object]]:
    for ordinal, record in enumerate(records):
        yield {**record, _ORDINAL: ordinal}


def _validate_target(target: WriteTarget, database: str) -> None:
    try:
        validate_redshift_relation(target.relation_ref)
    except ValueError as error:
        raise RedshiftWriteError(str(error)) from error
    if target.relation_ref.catalog != database:
        raise RedshiftWriteError("Redshift target belongs to another database")
    if not target.business_key or not target.schema:
        raise RedshiftWriteError("Redshift SCD1 requires a business key and declared schema")
    declared = {field.name for field in target.canonical_schema.fields}
    if any(key not in declared for key in target.business_key):
        raise RedshiftWriteError("Redshift business keys must be declared schema fields")
    publication = target.publication_fence
    if publication is None:
        raise RedshiftWriteError("Redshift hosted writes require a destination target fence")
    expected = ".".join(target.relation_ref.coordinates)
    expected_table = f"{database}.{target.relation_ref.namespace}.dander_target_commits"
    if publication.target_id != expected or publication.fence_table != expected_table:
        raise RedshiftWriteError("Redshift destination target fence does not match the target")


def _validate_schema(schema: RelationSchema) -> None:
    for field in schema.fields:
        if len(field.name.encode()) > 127:
            raise RedshiftWriteError("Redshift column identifiers cannot exceed 127 bytes")
        _redshift_type(field.data_type)


def _validate_manifest(manifest: StagingManifest) -> None:
    if any(
        artifact.rows == 1 and artifact.logical_bytes > _MAX_PARQUET_ROW_BYTES
        for artifact in manifest.artifacts
    ):
        raise RedshiftWriteError("Redshift Parquet row exceeds the 4 MB COPY limit")


def _validate_bucket_region(client: RedshiftS3Client, bucket: str, expected: str) -> None:
    response = client.get_bucket_location(Bucket=bucket)
    location = response.get("LocationConstraint")
    actual = "us-east-1" if location in {None, ""} else location
    if actual == "EU":
        actual = "eu-west-1"
    if actual != expected:
        raise RedshiftWriteError("Redshift staging bucket must be in the warehouse AWS region")


def _copy_manifest(bucket: str, prefix: str, manifest: StagingManifest) -> bytes:
    return json.dumps(
        {
            "entries": [
                {
                    "url": f"s3://{bucket}/{prefix}/{artifact.path.name}",
                    "mandatory": True,
                    "meta": {"content_length": artifact.compressed_bytes},
                }
                for artifact in manifest.artifacts
            ]
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _publication_plan(
    connection: RedshiftConnection,
    target: WriteTarget,
    temp_table: str,
    schema: RelationSchema,
    manifest: StagingManifest,
    *,
    evolution: SchemaEvolution,
    has_rows: bool,
) -> RedshiftPublication:
    statements: list[tuple[str, Sequence[object]]] = [
        (_create_target_sql(target, schema), ()),
        (_history_table_sql(target), ()),
    ]
    statements.extend(_schema_changes(connection, target, schema, evolution=evolution))
    digest = _manifest_digest(manifest)
    history_values: tuple[object, ...] = (
        ".".join(target.relation_ref.coordinates),
        target.publication_fence.pipeline_id if target.publication_fence else "",
        target.publication_fence.run_id if target.publication_fence else "",
        manifest.schema_fingerprint,
        digest,
    )
    if has_rows:
        statements.append((_merge_sql(target, temp_table, schema.fields), history_values))
    else:
        statements.append((_no_op_sql(target), ()))
    affected_statement_index = len(statements) - 1
    statements.append((_history_insert_sql(target), (*history_values, *history_values)))
    return RedshiftPublication(
        statements=tuple(statements),
        affected_statement_index=affected_statement_index,
    )


def _schema_changes(
    connection: RedshiftConnection,
    target: WriteTarget,
    schema: RelationSchema,
    *,
    evolution: SchemaEvolution,
) -> tuple[tuple[str, Sequence[object]], ...]:
    relation = target.relation_ref
    rows = execute(
        connection,
        "SELECT column_name, data_type, character_maximum_length, numeric_precision, "
        "numeric_scale, is_nullable FROM svv_columns "
        "WHERE database_name = %s AND schema_name = %s AND table_name = %s",
        relation.coordinates,
        fetch="all",
    ).rows
    deployed = _deployed_columns(rows)
    expected = {field.name: field for field in schema.fields}
    if extras := sorted(set(deployed) - set(expected)):
        raise RedshiftWriteError(f"Deployed target has undeclared column {extras[0]!r}")
    changes: list[tuple[str, Sequence[object]]] = []
    for field in schema.fields:
        current = deployed.get(field.name)
        required = (
            field.cardinality is FieldCardinality.REQUIRED or field.name in target.business_key
        )
        if current is None:
            if not deployed:
                continue
            if evolution is not SchemaEvolution.ADDITIVE or required:
                raise RedshiftWriteError(f"Cannot add Redshift column {field.name!r}")
            changes.append(
                (
                    f"ALTER TABLE {_target(target)} ADD COLUMN {_quote(field.name)} "
                    f"{_redshift_type(field.data_type)}",
                    (),
                )
            )
            continue
        deployed_type, nullable = current
        if deployed_type.casefold() != _redshift_type(field.data_type).casefold():
            raise RedshiftWriteError(f"Redshift type drift for column {field.name!r}")
        if nullable == required:
            raise RedshiftWriteError(f"Redshift nullability drift for column {field.name!r}")
    return tuple(changes)


def _deployed_columns(rows: Sequence[object]) -> dict[str, tuple[str, bool]]:
    result: dict[str, tuple[str, bool]] = {}
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) < 6:
            raise RedshiftWriteError("Redshift schema inspection returned an invalid row")
        name, data_type, length, precision, scale, nullable = row[:6]
        if not all(isinstance(value, str) for value in (name, data_type, nullable)):
            raise RedshiftWriteError("Redshift schema inspection returned invalid values")
        result[name] = (
            _normalize_deployed_type(data_type, length, precision, scale),
            nullable.casefold() == "yes",
        )
    return result


def _create_target_sql(target: WriteTarget, schema: RelationSchema) -> str:
    fields = ", ".join(_target_column(field, target) for field in schema.fields)
    return f"CREATE TABLE IF NOT EXISTS {_target(target)} ({fields})"


def _target_column(field: CanonicalField, target: WriteTarget) -> str:
    required = field.cardinality is FieldCardinality.REQUIRED or field.name in target.business_key
    return (
        f"{_quote(field.name)} {_redshift_type(field.data_type)}{' NOT NULL' if required else ''}"
    )


def _temporary_table_sql(name: str, schema: RelationSchema) -> str:
    fields = ", ".join(
        f"{_quote(field.name)} {_redshift_type(field.data_type)}"
        f"{' NOT NULL' if field.cardinality is FieldCardinality.REQUIRED else ''}"
        for field in schema.fields
    )
    return f"CREATE TEMP TABLE {_quote(name)} ({fields})"


def _copy_sql(name: str, *, bucket: str, manifest_key: str, role_arn: str) -> str:
    return (
        f"COPY {_quote(name)} FROM {_literal(f's3://{bucket}/{manifest_key}')} "
        f"IAM_ROLE {_literal(role_arn)} FORMAT AS PARQUET MANIFEST"
    )


def _merge_sql(target: WriteTarget, temporary: str, fields: Sequence[CanonicalField]) -> str:
    names = tuple(field.name for field in fields)
    columns = ", ".join(_quote(name) for name in names)
    keys = ", ".join(_quote(key) for key in target.business_key)
    match = " AND ".join(
        f"target.{_quote(key)} = incoming.{_quote(key)}" for key in target.business_key
    )
    mutable = tuple(name for name in names if name not in target.business_key)
    updates = ", ".join(f"{_quote(name)} = incoming.{_quote(name)}" for name in mutable)
    selected = ", ".join(f"incoming.{_quote(name)}" for name in names)
    history = _history(target)
    matched = f" WHEN MATCHED THEN UPDATE SET {updates}" if updates else ""
    return (
        f"MERGE INTO {_target(target)} AS target USING (SELECT {columns} FROM "
        f"(SELECT {columns}, ROW_NUMBER() OVER (PARTITION BY {keys} ORDER BY "
        f"{_quote(_ORDINAL)} DESC) AS {_quote('_dander_rank')} FROM {_quote(temporary)}) ranked "
        f"WHERE {_quote('_dander_rank')} = 1 AND NOT EXISTS (SELECT 1 FROM {history} WHERE "
        '"target_id" = %s AND "pipeline_id" = %s AND "run_id" = %s '
        'AND "schema_fingerprint" = %s AND "manifest_digest" = %s)) AS incoming '
        f"ON {match}{matched} WHEN NOT MATCHED THEN "
        f"INSERT ({columns}) VALUES ({selected})"
    )


def _history_table_sql(target: WriteTarget) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {_history(target)} ("
        '"target_id" VARCHAR(1024) NOT NULL, "pipeline_id" VARCHAR(127) NOT NULL, '
        '"run_id" VARCHAR(127) NOT NULL, "schema_fingerprint" CHAR(64) NOT NULL, '
        '"manifest_digest" CHAR(64) NOT NULL, "loaded_at" TIMESTAMPTZ NOT NULL)'
    )


def _history_insert_sql(target: WriteTarget) -> str:
    return (
        f'INSERT INTO {_history(target)} ("target_id", "pipeline_id", "run_id", '
        '"schema_fingerprint", "manifest_digest", "loaded_at") SELECT %s, %s, %s, %s, %s, '
        f"GETDATE() WHERE NOT EXISTS (SELECT 1 FROM {_history(target)} WHERE "
        '"target_id" = %s AND "pipeline_id" = %s AND "run_id" = %s '
        'AND "schema_fingerprint" = %s AND "manifest_digest" = %s)'
    )


def _history(target: WriteTarget) -> str:
    return _qualified(target.relation_ref.namespace, "dander_stage_loads")


def _no_op_sql(target: WriteTarget) -> str:
    key = _quote(target.business_key[0])
    return f"UPDATE {_target(target)} SET {key} = {key} WHERE FALSE"


def _set_query_group(
    connection: RedshiftConnection,
    publication: TargetFence,
    *,
    statement_timeout_ms: int,
) -> None:
    digest = hashlib.sha256(f"{publication.pipeline_id}:{publication.run_id}".encode()).hexdigest()[
        :20
    ]
    execute(connection, f"SET query_group TO {_literal(f'dander_{digest}')}")
    execute(connection, f"SET statement_timeout TO {statement_timeout_ms}")


def _drop_temporary(connection: RedshiftConnection, name: str) -> None:
    try:
        execute(connection, f"DROP TABLE IF EXISTS {_quote(name)}")
        connection.commit()
    except Exception as error:
        connection.rollback()
        raise RedshiftWriteError("Redshift temporary staging cleanup failed") from error


def _delete_owned(client: RedshiftS3Client, bucket: str, keys: Sequence[str]) -> None:
    if not keys:
        return
    try:
        for start in range(0, len(keys), 1_000):
            response = client.delete_objects(
                Bucket=bucket,
                Delete={
                    "Objects": [{"Key": key} for key in keys[start : start + 1_000]],
                    "Quiet": True,
                },
            )
            if isinstance(response, Mapping) and response.get("Errors"):
                raise RedshiftWriteError("Redshift S3 staging cleanup reported object errors")
    except Exception as error:
        if isinstance(error, RedshiftWriteError):
            raise
        raise RedshiftWriteError("Redshift S3 staging cleanup failed") from error


def _redshift_type(data_type: CanonicalType) -> str:
    match data_type.kind:
        case LogicalTypeKind.BOOLEAN:
            return "BOOLEAN"
        case LogicalTypeKind.INTEGER:
            return "BIGINT"
        case LogicalTypeKind.DECIMAL:
            if data_type.precision is None or data_type.scale is None or data_type.precision > 38:
                raise RedshiftWriteError("Redshift decimal precision cannot exceed 38")
            return f"DECIMAL({data_type.precision},{data_type.scale})"
        case LogicalTypeKind.FLOAT:
            return "DOUBLE PRECISION"
        case LogicalTypeKind.STRING:
            return "VARCHAR(65535)"
        case LogicalTypeKind.BINARY:
            return "VARBYTE"
        case LogicalTypeKind.DATE:
            return "DATE"
        case LogicalTypeKind.TIME:
            if (data_type.fractional_second_precision or 0) > 6:
                raise RedshiftWriteError("Redshift time precision cannot exceed 6")
            return "TIME"
        case LogicalTypeKind.TIMESTAMP:
            if (data_type.fractional_second_precision or 0) > 6:
                raise RedshiftWriteError("Redshift timestamp precision cannot exceed 6")
            return "TIMESTAMPTZ" if data_type.with_timezone else "TIMESTAMP"
        case LogicalTypeKind.JSON | LogicalTypeKind.ARRAY | LogicalTypeKind.RECORD:
            raise RedshiftWriteError(
                "Redshift semi-structured types require an explicit SUPER fallback"
            )
    raise AssertionError("Unhandled Redshift canonical type")


def _target(target: WriteTarget) -> str:
    return _qualified(target.relation_ref.namespace, target.relation_ref.name)


def _qualified(*parts: str) -> str:
    return ".".join(_quote(part) for part in parts)


def _quote(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _manifest_digest(manifest: StagingManifest) -> str:
    payload = {
        "schema_fingerprint": manifest.schema_fingerprint,
        "artifacts": [
            {
                "rows": artifact.rows,
                "logical_bytes": artifact.logical_bytes,
                "compressed_bytes": artifact.compressed_bytes,
                "sha256": artifact.sha256,
            }
            for artifact in manifest.artifacts
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _normalize_deployed_type(
    data_type: str,
    length: object,
    precision: object,
    scale: object,
) -> str:
    normalized = " ".join(data_type.casefold().split())
    if normalized in {"character varying", "varchar"}:
        if not isinstance(length, int):
            raise RedshiftWriteError("Redshift VARCHAR metadata has no length")
        return f"VARCHAR({length})"
    if normalized in {"numeric", "decimal"}:
        if not isinstance(precision, int) or not isinstance(scale, int):
            raise RedshiftWriteError("Redshift DECIMAL metadata has no precision or scale")
        return f"DECIMAL({precision},{scale})"
    aliases = {
        "int8": "BIGINT",
        "bigint": "BIGINT",
        "float8": "DOUBLE PRECISION",
        "double precision": "DOUBLE PRECISION",
        "bool": "BOOLEAN",
        "boolean": "BOOLEAN",
        "timestamp with time zone": "TIMESTAMPTZ",
        "timestamp without time zone": "TIMESTAMP",
        "time without time zone": "TIME",
        "binary varying": "VARBYTE",
    }
    return aliases.get(normalized, normalized.upper())


__all__ = [
    "RedshiftS3Client",
    "RedshiftScd1Writer",
    "RedshiftStagingSettings",
    "RedshiftWriteError",
    "default_staging_settings",
]
