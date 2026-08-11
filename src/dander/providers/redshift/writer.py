"""Bounded direct or S3/Parquet COPY staging with fenced Redshift publication."""

# ruff: noqa: N803 -- boto3's public S3 API uses capitalized parameter names.

from __future__ import annotations

import hashlib
import json
import tempfile
from base64 import b64encode
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal
from itertools import chain
from pathlib import Path
from time import perf_counter_ns
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from dander.concurrency import TargetFenceLostError
from dander.providers.redshift.config import validate_redshift_relation
from dander.providers.redshift.session import (
    RedshiftConnection,
    RedshiftConnectionFactory,
    RedshiftStatementResult,
    capture_last_query_id,
    enrich_operation_telemetry,
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
    from dander.providers.redshift.fence import RedshiftTargetFence

_ORDINAL = "_dander_ordinal"
_SCD2_VALID_FROM = "valid_from"
_SCD2_VALID_TO = "valid_to"
_SCD2_IS_CURRENT = "is_current"
_SCD2_SYSTEM_FIELDS = frozenset({_SCD2_VALID_FROM, _SCD2_VALID_TO, _SCD2_IS_CURRENT})
_MAX_PARQUET_ROW_BYTES = 4 * 1_024 * 1_024
_MAX_SUPER_BYTES = 16 * 1_024 * 1_024


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
    direct_max_rows: int = 0
    direct_max_logical_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _DirectBatch:
    rows: tuple[dict[str, object], ...]
    logical_bytes: int
    schema_fingerprint: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RedshiftPublication:
    statements: tuple[tuple[str, Sequence[object]], ...]
    affected_statement_index: int


@dataclass(frozen=True, slots=True)
class _LoadOutcome:
    affected: int
    operations: tuple[OperationTelemetry, ...]


class RedshiftStagedWriter(WritePattern):
    """Select bounded direct/COPY staging and publish one logical write mode."""

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
        mode: WriteMode,
        cursor_field: str | None = None,
        snapshot_field: str | None = None,
    ) -> None:
        self._database = database
        self._connection_factory = connection_factory
        self._s3 = s3_client
        self._target_fence = target_fence
        self._evolution = schema_evolution
        self._staging = staging
        self.mode = mode
        self._cursor_field = cursor_field
        self._snapshot_field = snapshot_field
        direct_enabled = staging.direct_max_rows > 0
        # Direct/COPY selection must see the complete endpoint. When direct is disabled,
        # preserve the established executor batching behavior exactly.
        self.supports_batched_writes = not direct_enabled and mode in {
            WriteMode.SCD1,
            WriteMode.INCREMENTAL,
            WriteMode.SNAPSHOT,
        }
        self.accepts_streaming_input = not self.supports_batched_writes
        self._telemetry: list[OperationTelemetry] = []

    def drain_telemetry(self) -> tuple[OperationTelemetry, ...]:
        """Return completed COPY/publication operations exactly once."""
        operations = tuple(self._telemetry)
        self._telemetry.clear()
        return operations

    def write(self, records: Iterable[Mapping[str, object]], target: WriteTarget) -> int:
        self._telemetry.clear()
        _validate_target(
            target,
            database=self._database,
            mode=self.mode,
            cursor_field=self._cursor_field,
            snapshot_field=self._snapshot_field,
        )
        publication = target.publication_fence
        assert publication is not None
        schema = target.canonical_schema
        validate_redshift_schema(schema)
        _validate_super_roles(
            target,
            cursor_field=self._cursor_field,
            snapshot_field=self._snapshot_field,
        )
        reserved = {_ORDINAL}
        if self.mode is WriteMode.SCD2:
            reserved.update(_SCD2_SYSTEM_FIELDS)
        if collision := sorted(field.name for field in schema.fields if field.name in reserved):
            raise RedshiftWriteError(f"Declared schema reserves Dander field {collision[0]!r}")
        staged_schema = RelationSchema(
            fields=(
                *(_staging_field(field) for field in schema.fields),
                CanonicalField(
                    name=_ORDINAL,
                    data_type=CanonicalType(kind=LogicalTypeKind.INTEGER, bit_width=64),
                    cardinality=FieldCardinality.REQUIRED,
                ),
            )
        )
        prepared = iter(
            _with_ordinals(
                _serialize_super_records(records, schema),
                business_key=target.business_key,
                cursor_field=self._cursor_field,
                snapshot_field=self._snapshot_field,
            )
        )
        direct, remaining = _select_direct_batch(
            prepared,
            staged_schema,
            max_rows=self._staging.direct_max_rows,
            max_logical_bytes=self._staging.direct_max_logical_bytes,
        )
        if direct is not None:
            outcome = self._load_and_publish(
                target,
                publication,
                schema,
                staged_schema,
                transport=WriteTransport.DIRECT,
                direct=direct,
            )
            self._telemetry.extend(outcome.operations)
            return outcome.affected

        local_id = f"redshift-{uuid4().hex}"
        with ParquetStagingSession(
            self._staging.root,
            run_id=local_id,
            max_rows_per_file=self._staging.max_rows_per_file,
            max_logical_bytes_per_file=self._staging.max_logical_bytes_per_file,
            compression=self._staging.compression,
        ) as local:
            manifest = local.stage(
                _validated_staging_rows(remaining, staged_schema),
                staged_schema,
            )
            outcome = self._load_and_publish(
                target,
                publication,
                schema,
                staged_schema,
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
        staged_schema: RelationSchema,
        *,
        transport: WriteTransport,
        manifest: StagingManifest | None = None,
        direct: _DirectBatch | None = None,
    ) -> _LoadOutcome:
        if (manifest is None) == (direct is None):
            raise TypeError("Redshift writer requires exactly one staged load input")
        if manifest is not None:
            _validate_bucket_region(self._s3, self._staging.bucket, self._staging.region)
            rows = manifest.rows
            logical_or_compressed_bytes = sum(
                artifact.compressed_bytes for artifact in manifest.artifacts
            )
            schema_fingerprint = manifest.schema_fingerprint
            load_digest = _manifest_digest(manifest)
        else:
            assert direct is not None
            rows = len(direct.rows)
            logical_or_compressed_bytes = direct.logical_bytes
            schema_fingerprint = direct.schema_fingerprint
            load_digest = direct.sha256
        remote_id = hashlib.sha256(
            f"{publication.target_id}:{publication.run_id}:{uuid4().hex}".encode()
        ).hexdigest()[:24]
        remote_prefix = f"{self._staging.prefix}/{remote_id}"
        temp_table = f"dander_stage_{remote_id}"
        uploaded: list[str] = []
        operations: list[OperationTelemetry] = []
        loaded: RedshiftStatementResult | None = None
        load_duration_ms = 0
        with open_connection(self._connection_factory) as connection:
            publication_failed = True
            try:
                _set_query_group(
                    connection,
                    publication,
                    statement_timeout_ms=self._staging.statement_timeout_ms,
                )
                if rows and manifest is not None:
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
                    loaded, load_duration_ms = _timed_call(
                        execute,
                        connection,
                        _copy_sql(
                            temp_table,
                            bucket=self._staging.bucket,
                            manifest_key=manifest_key,
                            role_arn=self._staging.copy_role_arn,
                        ),
                    )
                elif rows:
                    assert direct is not None
                    execute(connection, _temporary_table_sql(temp_table, staged_schema))
                    loaded, load_duration_ms = _timed_call(
                        execute_many,
                        connection,
                        *_direct_insert(temp_table, staged_schema, direct.rows),
                    )
                # COPY and SET open an implicit transaction. Close it while retaining the
                # session-scoped temp table so the fenced publication owns one transaction.
                connection.commit()
                if rows:
                    assert loaded is not None
                    if transport is WriteTransport.COPY:
                        loaded = replace(loaded, query_id=capture_last_query_id(connection))
                    operations.append(
                        _operation_telemetry(
                            loaded,
                            operation=TelemetryOperation.LOAD,
                            duration_ms=load_duration_ms,
                            rows_written=rows,
                            bytes_written=logical_or_compressed_bytes,
                            transport=transport,
                        )
                    )
                plan = _publication_plan(
                    connection,
                    target,
                    temp_table,
                    target_schema,
                    schema_fingerprint=schema_fingerprint,
                    load_digest=load_digest,
                    evolution=self._evolution,
                    has_rows=bool(rows),
                    mode=self.mode,
                    cursor_field=self._cursor_field,
                    snapshot_field=self._snapshot_field,
                )
                # The schema read also opens an implicit transaction. End that read-only
                # snapshot before beginning the one fenced publication transaction.
                connection.commit()
                started = perf_counter_ns()
                results = self._target_fence.execute_statements(
                    connection,
                    plan.statements,
                    publication,
                )
                publication_duration_ms = _elapsed_milliseconds(started)
                affected_rows = results[plan.affected_statement_index].rowcount
                if affected_rows < 0:
                    raise RedshiftWriteError("Redshift publication did not report affected rows")
                operations.append(
                    _operation_telemetry(
                        results[plan.affected_statement_index],
                        operation=TelemetryOperation.QUERY,
                        duration_ms=publication_duration_ms,
                        transport=transport,
                    )
                )
                completed = enrich_operation_telemetry(connection, operations)
                publication_failed = False
                return _LoadOutcome(affected_rows, completed)
            except (RedshiftWriteError, TargetFenceLostError):
                raise
            except Exception as error:
                raise RedshiftWriteError(
                    f"Redshift staged {self.mode.value.upper()} write failed"
                ) from error
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


class RedshiftScd1Writer(RedshiftStagedWriter):
    """Backward-compatible SCD1 constructor for existing provider integrations."""

    mode = WriteMode.SCD1

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
        super().__init__(
            database=database,
            connection_factory=connection_factory,
            s3_client=s3_client,
            target_fence=target_fence,
            schema_evolution=schema_evolution,
            staging=staging,
            mode=WriteMode.SCD1,
        )


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
    direct_max_rows: int = 0,
    direct_max_logical_bytes: int = 0,
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
        direct_max_rows=direct_max_rows,
        direct_max_logical_bytes=direct_max_logical_bytes,
    )


def _timed_call(
    function: Callable[..., RedshiftStatementResult],
    *arguments: object,
) -> tuple[RedshiftStatementResult, int]:
    started = perf_counter_ns()
    result = function(*arguments)
    return result, _elapsed_milliseconds(started)


def _elapsed_milliseconds(started: int) -> int:
    return max((perf_counter_ns() - started) // 1_000_000, 0)


def _operation_telemetry(
    result: RedshiftStatementResult,
    *,
    operation: TelemetryOperation,
    duration_ms: int,
    rows_written: int = 0,
    bytes_written: int = 0,
    transport: WriteTransport = WriteTransport.COPY,
) -> OperationTelemetry:
    return OperationTelemetry(
        provider="redshift",
        operation=operation,
        duration_ms=duration_ms,
        rows_written=rows_written,
        rows_affected=max(result.rowcount, 0),
        bytes_written=bytes_written,
        query_id=result.query_id,
        transport=transport,
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
    schema_fingerprint = _schema_fingerprint(schema)
    return (
        _DirectBatch(
            rows=rows,
            logical_bytes=logical_bytes,
            schema_fingerprint=schema_fingerprint,
            sha256=_direct_checksum(schema, rows),
        ),
        (),
    )


def _schema_fingerprint(schema: RelationSchema) -> str:
    return hashlib.sha256(schema.model_dump_json(by_alias=True).encode("utf-8")).hexdigest()


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
    raise RedshiftWriteError("Redshift direct staging received an unsupported scalar value")


def _direct_insert(
    temporary: str,
    schema: RelationSchema,
    rows: tuple[dict[str, object], ...],
) -> tuple[str, tuple[tuple[object, ...], ...]]:
    columns = ", ".join(_quote(field.name) for field in schema.fields)
    placeholders = ", ".join(
        "TO_VARBYTE(%s, 'hex')" if field.data_type.kind is LogicalTypeKind.BINARY else "%s"
        for field in schema.fields
    )
    statement = f"INSERT INTO {_quote(temporary)} ({columns}) VALUES ({placeholders})"
    parameters = tuple(tuple(row[field.name] for field in schema.fields) for row in rows)
    return statement, parameters


def _with_ordinals(
    records: Iterable[Mapping[str, object]],
    *,
    business_key: Sequence[str],
    cursor_field: str | None,
    snapshot_field: str | None,
) -> Iterable[Mapping[str, object]]:
    for ordinal, record in enumerate(records):
        if any(record.get(field) is None for field in business_key):
            raise RedshiftWriteError(f"Record {ordinal} has a null business-key value")
        if cursor_field is not None and record.get(cursor_field) is None:
            raise RedshiftWriteError(f"Record {ordinal} has a null incremental cursor value")
        if snapshot_field is not None and record.get(snapshot_field) is None:
            raise RedshiftWriteError(f"Record {ordinal} has a null snapshot value")
        yield {**record, _ORDINAL: ordinal}


def _validate_target(
    target: WriteTarget,
    *,
    database: str,
    mode: WriteMode,
    cursor_field: str | None,
    snapshot_field: str | None,
) -> None:
    try:
        validate_redshift_relation(target.relation_ref)
    except ValueError as error:
        raise RedshiftWriteError(str(error)) from error
    if target.relation_ref.catalog != database:
        raise RedshiftWriteError("Redshift target belongs to another database")
    declared = {field.name for field in target.canonical_schema.fields}
    if not declared:
        raise RedshiftWriteError("Redshift writes require a declared schema")
    if mode in {WriteMode.SCD1, WriteMode.SCD2, WriteMode.INCREMENTAL}:
        if not target.business_key:
            raise RedshiftWriteError(f"Redshift {mode.value} writes require a business key")
        if missing := sorted(set(target.business_key) - declared):
            raise RedshiftWriteError(f"Redshift business-key field {missing[0]!r} is undeclared")
    if mode is WriteMode.INCREMENTAL:
        if cursor_field is None or not cursor_field.strip():
            raise RedshiftWriteError("Redshift incremental writes require cursor_field")
        if cursor_field not in declared:
            raise RedshiftWriteError("Redshift incremental cursor field is undeclared")
    elif cursor_field is not None:
        raise RedshiftWriteError("cursor_field is valid only for Redshift incremental writes")
    if mode is WriteMode.SNAPSHOT:
        if snapshot_field is None or not snapshot_field.strip():
            raise RedshiftWriteError("Redshift snapshot writes require snapshot_field")
        if snapshot_field not in declared:
            raise RedshiftWriteError("Redshift snapshot field is undeclared")
    elif snapshot_field is not None:
        raise RedshiftWriteError("snapshot_field is valid only for Redshift snapshot writes")
    publication = target.publication_fence
    if publication is None:
        raise RedshiftWriteError("Redshift hosted writes require a destination target fence")
    expected = ".".join(target.relation_ref.coordinates)
    expected_table = f"{database}.{target.relation_ref.namespace}.dander_target_commits"
    if publication.target_id != expected or publication.fence_table != expected_table:
        raise RedshiftWriteError("Redshift destination target fence does not match the target")


def validate_redshift_schema(schema: RelationSchema) -> None:
    """Validate scalar mappings plus the explicit JSON-to-SUPER fallback."""
    for field in schema.fields:
        if len(field.name.encode()) > 127:
            raise RedshiftWriteError("Redshift column identifiers cannot exceed 127 bytes")
        _redshift_field_type(field)


def _validate_super_roles(
    target: WriteTarget,
    *,
    cursor_field: str | None,
    snapshot_field: str | None,
) -> None:
    super_fields = {
        field.name for field in target.canonical_schema.fields if _is_super_fallback(field)
    }
    restricted = {*target.business_key}
    if cursor_field is not None:
        restricted.add(cursor_field)
    if snapshot_field is not None:
        restricted.add(snapshot_field)
    if collision := sorted(super_fields & restricted):
        raise RedshiftWriteError(
            f"Redshift SUPER field {collision[0]!r} cannot be a key, cursor, or snapshot field"
        )


def _staging_field(field: CanonicalField) -> CanonicalField:
    if _is_super_fallback(field):
        return field.model_copy(update={"data_type": CanonicalType(kind=LogicalTypeKind.BINARY)})
    return field


def _is_super_fallback(field: CanonicalField) -> bool:
    redshift_extensions = tuple(
        extension for extension in field.extensions if extension.provider == "redshift"
    )
    expected = _has_super_extension(field)
    if field.data_type.kind is LogicalTypeKind.JSON:
        if not expected:
            raise RedshiftWriteError("Redshift JSON fields require redshift/fallback=super")
        return True
    if field.data_type.kind in {LogicalTypeKind.ARRAY, LogicalTypeKind.RECORD}:
        raise RedshiftWriteError(
            "Redshift ARRAY and RECORD fields have no canonical fallback in this slice"
        )
    if redshift_extensions:
        raise RedshiftWriteError("Redshift field extension is unsupported for this type")
    return False


def _has_super_extension(field: CanonicalField) -> bool:
    redshift_extensions = tuple(
        extension for extension in field.extensions if extension.provider == "redshift"
    )
    return (
        len(redshift_extensions) == 1
        and redshift_extensions[0].name == "fallback"
        and redshift_extensions[0].value == "super"
    )


def _redshift_field_type(field: CanonicalField) -> str:
    if _is_super_fallback(field):
        return "SUPER"
    return _redshift_type(field.data_type)


def _serialize_super_records(
    records: Iterable[Mapping[str, object]],
    schema: RelationSchema,
) -> Iterable[Mapping[str, object]]:
    super_fields = tuple(field.name for field in schema.fields if _is_super_fallback(field))
    for row_index, record in enumerate(records):
        prepared = dict(record)
        for field_name in super_fields:
            value = prepared.get(field_name)
            if value is None:
                continue
            _validate_json_keys(value, row_index=row_index, field_name=field_name)
            try:
                encoded = json.dumps(
                    value,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise RedshiftWriteError(
                    f"Record {row_index} has invalid JSON in field {field_name!r}"
                ) from error
            if len(encoded) > _MAX_SUPER_BYTES:
                raise RedshiftWriteError(
                    f"Record {row_index} JSON field {field_name!r} exceeds Redshift SUPER size"
                )
            prepared[field_name] = encoded
        yield prepared


def _validate_json_keys(
    value: object,
    *,
    row_index: int,
    field_name: str,
    active: set[int] | None = None,
) -> None:
    active = set() if active is None else active
    if isinstance(value, (dict, list, tuple)):
        identity = id(value)
        if identity in active:
            raise RedshiftWriteError(f"Record {row_index} has cyclic JSON in field {field_name!r}")
        active.add(identity)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise RedshiftWriteError(
                f"Record {row_index} has a non-string JSON key in field {field_name!r}"
            )
        for nested in value.values():
            _validate_json_keys(
                nested,
                row_index=row_index,
                field_name=field_name,
                active=active,
            )
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _validate_json_keys(
                nested,
                row_index=row_index,
                field_name=field_name,
                active=active,
            )
    if isinstance(value, (dict, list, tuple)):
        active.remove(id(value))


def _validated_staging_rows(
    records: Iterable[Mapping[str, object]],
    schema: RelationSchema,
) -> Iterable[Mapping[str, object]]:
    for row_index, record in enumerate(records):
        normalized = normalize_staging_record(record, schema, row_index=row_index)
        if staging_logical_size(normalized) > _MAX_PARQUET_ROW_BYTES:
            raise RedshiftWriteError("Redshift Parquet row exceeds the 4 MB COPY limit")
        yield record


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
    *,
    schema_fingerprint: str,
    load_digest: str,
    evolution: SchemaEvolution,
    has_rows: bool,
    mode: WriteMode,
    cursor_field: str | None,
    snapshot_field: str | None,
) -> RedshiftPublication:
    deployed_schema = _target_schema_for_mode(schema, mode)
    statements: list[tuple[str, Sequence[object]]] = [
        (_create_target_sql(target, deployed_schema), ()),
        (_history_table_sql(target), ()),
    ]
    statements.extend(_schema_changes(connection, target, deployed_schema, evolution=evolution))
    history_values: tuple[object, ...] = (
        ".".join(target.relation_ref.coordinates),
        target.publication_fence.pipeline_id if target.publication_fence else "",
        target.publication_fence.run_id if target.publication_fence else "",
        schema_fingerprint,
        load_digest,
    )
    publication = _publication_statements(
        target,
        temp_table,
        schema.fields,
        history_values,
        mode=mode,
        cursor_field=cursor_field,
        snapshot_field=snapshot_field,
        has_rows=has_rows,
    )
    affected_statement_index = len(statements) + publication.affected_statement_index
    statements.extend(publication.statements)
    statements.append((_history_insert_sql(target), (*history_values, *history_values)))
    return RedshiftPublication(
        statements=tuple(statements),
        affected_statement_index=affected_statement_index,
    )


def _publication_statements(
    target: WriteTarget,
    temporary: str,
    fields: Sequence[CanonicalField],
    history_values: tuple[object, ...],
    *,
    mode: WriteMode,
    cursor_field: str | None,
    snapshot_field: str | None,
    has_rows: bool,
) -> RedshiftPublication:
    if not has_rows:
        statement = _replace_delete_sql(target) if mode is WriteMode.REPLACE else _no_op_sql(target)
        parameters = history_values if mode is WriteMode.REPLACE else ()
        return RedshiftPublication(((statement, parameters),), 0)
    if mode is WriteMode.REPLACE:
        statements = _replace_statements(target, temporary, fields, history_values)
        return RedshiftPublication(statements, len(statements) - 1)
    if mode is WriteMode.SNAPSHOT:
        assert snapshot_field is not None
        return RedshiftPublication(
            ((_snapshot_sql(target, temporary, fields, snapshot_field), history_values),),
            0,
        )
    if mode is WriteMode.SCD2:
        statements = _scd2_statements(target, temporary, fields, history_values)
        return RedshiftPublication(statements, len(statements) - 1)
    if mode is WriteMode.INCREMENTAL:
        assert cursor_field is not None
        statements = (
            (_incremental_prune_sql(target, temporary, cursor_field), ()),
            (
                _merge_sql(target, temporary, fields, cursor_field=cursor_field),
                history_values,
            ),
        )
        return RedshiftPublication(statements, len(statements) - 1)
    return RedshiftPublication(
        (
            (
                _merge_sql(
                    target,
                    temporary,
                    fields,
                    cursor_field=None,
                ),
                history_values,
            ),
        ),
        0,
    )


def _deduplicated_source(
    target: WriteTarget,
    temporary: str,
    fields: Sequence[CanonicalField],
    *,
    cursor_field: str | None = None,
) -> str:
    columns = ", ".join(_quote(field.name) for field in fields)
    keys = ", ".join(_quote(key) for key in target.business_key)
    ordering = (
        f"{_quote(cursor_field)} DESC NULLS LAST, {_quote(_ORDINAL)} DESC"
        if cursor_field is not None
        else f"{_quote(_ORDINAL)} DESC"
    )
    source = _projected_staging_source(temporary, fields, include_ordinal=True)
    return (
        f"SELECT {columns} FROM (SELECT {columns}, ROW_NUMBER() OVER (PARTITION BY {keys} "
        f"ORDER BY {ordering}) AS {_quote('_dander_rank')} FROM {source} AS normalized) ranked "
        f"WHERE {_quote('_dander_rank')} = 1"
    )


def _projected_staging_source(
    temporary: str,
    fields: Sequence[CanonicalField],
    *,
    include_ordinal: bool = False,
) -> str:
    projections = [_staging_projection(field) for field in fields]
    if include_ordinal:
        projections.append(f"staged.{_quote(_ORDINAL)} AS {_quote(_ORDINAL)}")
    return f"(SELECT {', '.join(projections)} FROM {_quote(temporary)} AS staged)"


def _staging_projection(field: CanonicalField) -> str:
    reference = f"staged.{_quote(field.name)}"
    value = f"JSON_PARSE({reference})" if _is_super_fallback(field) else reference
    return f"{value} AS {_quote(field.name)}"


def _replace_statements(
    target: WriteTarget,
    temporary: str,
    fields: Sequence[CanonicalField],
    history_values: tuple[object, ...],
) -> tuple[tuple[str, Sequence[object]], ...]:
    columns = ", ".join(_quote(field.name) for field in fields)
    selected = ", ".join(f"incoming.{_quote(field.name)}" for field in fields)
    source = _projected_staging_source(temporary, fields)
    guard = _history_guard(target)
    return (
        (_replace_delete_sql(target), history_values),
        (
            f"INSERT INTO {_target(target)} ({columns}) SELECT {selected} "
            f"FROM {source} AS incoming WHERE {guard}",
            history_values,
        ),
    )


def _replace_delete_sql(target: WriteTarget) -> str:
    return f"DELETE FROM {_target(target)} WHERE {_history_guard(target)}"


def _snapshot_sql(
    target: WriteTarget,
    temporary: str,
    fields: Sequence[CanonicalField],
    snapshot_field: str,
) -> str:
    columns = ", ".join(_quote(field.name) for field in fields)
    selected = ", ".join(f"incoming.{_quote(field.name)}" for field in fields)
    identical = " AND ".join(
        _null_safe_equal(f"existing.{_quote(field.name)}", f"incoming.{_quote(field.name)}")
        for field in fields
    )
    source = _projected_staging_source(temporary, fields)
    return (
        f"INSERT INTO {_target(target)} ({columns}) SELECT DISTINCT {selected} "
        f"FROM {source} AS incoming WHERE "
        f"incoming.{_quote(snapshot_field)} IS NOT NULL AND {_history_guard(target)} "
        f"AND NOT EXISTS (SELECT 1 FROM {_target(target)} AS existing WHERE {identical})"
    )


def _incremental_prune_sql(
    target: WriteTarget,
    temporary: str,
    cursor_field: str,
) -> str:
    temporary_name = _quote(temporary)
    target_name = _quote(target.relation_ref.name)
    match = " AND ".join(
        f"{temporary_name}.{_quote(key)} = {target_name}.{_quote(key)}"
        for key in target.business_key
    )
    return (
        f"DELETE FROM {temporary_name} USING {_target(target)} "
        f"WHERE {match} AND {temporary_name}.{_quote(cursor_field)} "
        f"< {target_name}.{_quote(cursor_field)}"
    )


def _scd2_statements(
    target: WriteTarget,
    temporary: str,
    fields: Sequence[CanonicalField],
    history_values: tuple[object, ...],
) -> tuple[tuple[str, Sequence[object]], ...]:
    names = tuple(field.name for field in fields)
    incoming = _deduplicated_source(target, temporary, fields)
    match = " AND ".join(
        f"current.{_quote(key)} = incoming.{_quote(key)}" for key in target.business_key
    )
    mutable = tuple(name for name in names if name not in target.business_key)
    changed = (
        " OR ".join(
            _null_safe_different(f"current.{_quote(name)}", f"incoming.{_quote(name)}")
            for name in mutable
        )
        or "FALSE"
    )
    close = (
        f"UPDATE {_target(target)} AS current SET {_quote(_SCD2_VALID_TO)} = SYSDATE, "
        f"{_quote(_SCD2_IS_CURRENT)} = FALSE FROM ({incoming}) AS incoming "
        f"WHERE {match} AND current.{_quote(_SCD2_IS_CURRENT)} = TRUE AND ({changed}) "
        f"AND {_history_guard(target)}"
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
            "SYSDATE",
            "NULL",
            "TRUE",
        ]
    )
    insert = (
        f"INSERT INTO {_target(target)} ({columns}) SELECT {values} FROM ({incoming}) AS incoming "
        f"WHERE {_history_guard(target)} AND NOT EXISTS "
        f"(SELECT 1 FROM {_target(target)} AS current "
        f"WHERE {match} AND current.{_quote(_SCD2_IS_CURRENT)} = TRUE)"
    )
    return ((close, history_values), (insert, history_values))


def _history_guard(target: WriteTarget) -> str:
    return (
        f"NOT EXISTS (SELECT 1 FROM {_history(target)} WHERE "
        '"target_id" = %s AND "pipeline_id" = %s AND "run_id" = %s '
        'AND "schema_fingerprint" = %s AND "manifest_digest" = %s)'
    )


def _null_safe_equal(left: str, right: str) -> str:
    return f"({left} = {right} OR ({left} IS NULL AND {right} IS NULL))"


def _null_safe_different(left: str, right: str) -> str:
    return (
        f"({left} <> {right} OR ({left} IS NULL AND {right} IS NOT NULL) "
        f"OR ({left} IS NOT NULL AND {right} IS NULL))"
    )


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
                    fractional_second_precision=6,
                    with_timezone=False,
                ),
                cardinality=FieldCardinality.REQUIRED,
            ),
            CanonicalField(
                name=_SCD2_VALID_TO,
                data_type=CanonicalType(
                    kind=LogicalTypeKind.TIMESTAMP,
                    fractional_second_precision=6,
                    with_timezone=False,
                ),
            ),
            CanonicalField(
                name=_SCD2_IS_CURRENT,
                data_type=CanonicalType(kind=LogicalTypeKind.BOOLEAN),
                cardinality=FieldCardinality.REQUIRED,
            ),
        )
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
        "WHERE table_catalog = %s AND table_schema = %s AND table_name = %s",
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
                    f"{_redshift_field_type(field)}",
                    (),
                )
            )
            continue
        deployed_type, nullable = current
        if deployed_type.casefold() != _redshift_field_type(field).casefold():
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
    return f"{_quote(field.name)} {_redshift_field_type(field)}{' NOT NULL' if required else ''}"


def _temporary_table_sql(name: str, schema: RelationSchema) -> str:
    fields = ", ".join(
        f"{_quote(field.name)} {_redshift_staging_type(field)}"
        f"{' NOT NULL' if field.cardinality is FieldCardinality.REQUIRED else ''}"
        for field in schema.fields
    )
    return f"CREATE TEMP TABLE {_quote(name)} ({fields})"


def _redshift_staging_type(field: CanonicalField) -> str:
    if _has_super_extension(field):
        return f"VARBYTE({_MAX_SUPER_BYTES})"
    return _redshift_type(field.data_type)


def _copy_sql(name: str, *, bucket: str, manifest_key: str, role_arn: str) -> str:
    return (
        f"COPY {_quote(name)} FROM {_literal(f's3://{bucket}/{manifest_key}')} "
        f"IAM_ROLE {_literal(role_arn)} FORMAT AS PARQUET MANIFEST"
    )


def _merge_sql(
    target: WriteTarget,
    temporary: str,
    fields: Sequence[CanonicalField],
    *,
    cursor_field: str | None,
) -> str:
    names = tuple(field.name for field in fields)
    target_name = _quote(target.relation_ref.name)
    columns = ", ".join(_quote(name) for name in names)
    match = " AND ".join(
        f"{target_name}.{_quote(key)} = incoming.{_quote(key)}" for key in target.business_key
    )
    mutable = tuple(name for name in names if name not in target.business_key)
    updates = ", ".join(f"{_quote(name)} = incoming.{_quote(name)}" for name in mutable)
    selected = ", ".join(f"incoming.{_quote(name)}" for name in names)
    history = _history(target)
    matched = f" WHEN MATCHED THEN UPDATE SET {updates}" if updates else ""
    source = _deduplicated_source(
        target,
        temporary,
        fields,
        cursor_field=cursor_field,
    )
    return (
        f"MERGE INTO {_target(target)} USING (SELECT {columns} FROM ({source}) source "
        f"WHERE NOT EXISTS (SELECT 1 FROM {history} WHERE "
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
    column = _quote(target.canonical_schema.fields[0].name)
    return f"UPDATE {_target(target)} SET {column} = {column} WHERE FALSE"


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
    "RedshiftStagedWriter",
    "RedshiftStagingSettings",
    "RedshiftWriteError",
    "default_staging_settings",
    "validate_redshift_schema",
]
