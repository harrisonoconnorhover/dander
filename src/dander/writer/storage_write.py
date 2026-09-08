"""BigQuery Storage Write API staging with idempotent SCD1/incremental merge.

This transport encodes supported scalar fields only. Use load_job for REPEATED fields.
"""

from __future__ import annotations

from functools import partial
from time import monotonic_ns
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

from google.cloud import bigquery, bigquery_storage_v1
from google.cloud.bigquery_storage_v1 import types, writer
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from dander.concurrency import fenced_dml, fencing_job_config
from dander.identity import google_client_options
from dander.providers.bigquery.telemetry import BigQueryJobTelemetry
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.writer.base import SchemaEvolution, WriteMode, WritePattern, WriteTarget
from dander.writer.bigquery import (
    BigQueryWriteError,
    _apply_schema_evolution,
    _create_target_sql,
    _deduplicate_keyed,
    _field_type_sql,
    _merge_sql,
    _target_id,
    _validate_declared_schema,
    _validated_batch,
    _validated_batch_size,
)
from dander.writer.bigquery import (
    _BigQueryClient as _LoadClient,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from dander.writer.base import WriteField


class _Job(Protocol):
    num_dml_affected_rows: int | None

    def result(self) -> object:
        """Wait for query completion."""


class _BigQueryClient(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _Job:
        """Run BigQuery SQL."""

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        """Delete a table."""


class PendingStreamBackend(Protocol):
    """Atomic pending-stream boundary, injectable for offline tests."""

    def append(
        self,
        rows: Sequence[Mapping[str, Any]],
        target: WriteTarget,
        *,
        max_batch_rows: int,
    ) -> None:
        """Append, finalize, and atomically commit one pending stream."""


class BigQueryPendingStreamBackend:
    """Low-level protobuf Storage Write API pending-stream implementation."""

    def __init__(
        self,
        *,
        client: bigquery_storage_v1.BigQueryWriteClient | None = None,
    ) -> None:
        self._client = client

    def append(
        self,
        rows: Sequence[Mapping[str, Any]],
        target: WriteTarget,
        *,
        max_batch_rows: int,
    ) -> None:
        fields = _validate_declared_schema(target, SchemaEvolution.ADDITIVE)
        message_class, descriptor = _message_type(fields)
        client = self._client or bigquery_storage_v1.BigQueryWriteClient(  # type: ignore[no-untyped-call]
            **google_client_options()
        )
        parent = f"projects/{target.project}/datasets/{target.dataset}/tables/{target.table}"
        stream = client.create_write_stream(
            parent=parent,
            write_stream=types.WriteStream(type_=types.WriteStream.Type.PENDING),
        )
        template = types.AppendRowsRequest(write_stream=stream.name)
        proto_schema = types.ProtoSchema(proto_descriptor=descriptor)
        template.proto_rows = types.AppendRowsRequest.ProtoData(writer_schema=proto_schema)
        append_stream = writer.AppendRowsStream(client, template)
        try:
            offset = 0
            for start in range(0, len(rows), max_batch_rows):
                serialized = [
                    _serialize_row(message_class, row)
                    for row in rows[start : start + max_batch_rows]
                ]
                request = types.AppendRowsRequest(offset=offset)
                request.proto_rows = types.AppendRowsRequest.ProtoData(
                    rows=types.ProtoRows(serialized_rows=serialized)
                )
                response = append_stream.send(request).result()  # type: ignore[no-untyped-call]
                if response.error.code:
                    raise BigQueryWriteError("Storage Write API rejected an append request")
                offset += len(serialized)
        finally:
            append_stream.close()
        client.finalize_write_stream(name=stream.name)
        committed = client.batch_commit_write_streams(
            request=types.BatchCommitWriteStreamsRequest(
                parent=parent,
                write_streams=[stream.name],
            )
        )
        if committed.stream_errors:
            raise BigQueryWriteError("Storage Write API could not commit the pending stream")


class BigQueryStorageScd1Writer(WritePattern):
    """Stage through an atomic pending stream, then merge idempotently by business key."""

    mode = WriteMode.SCD1

    def __init__(
        self,
        *,
        project: str,
        client: _BigQueryClient | None = None,
        backend: PendingStreamBackend | None = None,
        max_batch_rows: int = 10_000,
        schema_evolution: SchemaEvolution = SchemaEvolution.STRICT,
    ) -> None:
        self._project = project
        self._client = client or cast(
            "_BigQueryClient",
            bigquery.Client(project=project, **google_client_options()),
        )
        self._backend = backend or BigQueryPendingStreamBackend()
        self._max_batch_rows = _validated_batch_size(max_batch_rows)
        self._schema_evolution = schema_evolution
        self._telemetry = BigQueryJobTelemetry()

    def drain_telemetry(self) -> tuple[OperationTelemetry, ...]:
        """Return completed jobs and committed stream observations in execution order."""
        return self._telemetry.drain()

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        """Write one logical batch through a pending stream and explicit MERGE."""
        target_id = _target_id(target)
        if target.project != self._project:
            raise BigQueryWriteError(
                f"Writer project {self._project!r} does not match target project {target.project!r}"
            )
        if not target.business_key:
            raise BigQueryWriteError("SCD1 writes require at least one business-key column")
        rows, columns = _validated_batch(records)
        if not rows:
            return 0
        staged_rows = _deduplicate_keyed(rows, columns, target.business_key)
        declared = _validate_declared_schema(target, SchemaEvolution.ADDITIVE)
        declared_names = tuple(field.name for field in declared)
        if set(declared_names) != set(columns):
            raise BigQueryWriteError(
                "Storage Write API schema must exactly match the incoming columns"
            )
        _message_type(declared)

        staging_table = f"_dander_stage_{target.table}_{uuid4().hex}"
        staging_target = WriteTarget(
            project=target.project,
            dataset=target.dataset,
            table=staging_table,
            schema=declared,
            declared_schema=target.canonical_schema,
        )
        staging_id = _target_id(staging_target)
        schema_sql = ", ".join(f"`{field.name}` {_field_type_sql(field)}" for field in declared)
        try:
            self._telemetry.run(
                partial(
                    self._client.query,
                    f"CREATE TABLE `{staging_id}` ({schema_sql}) OPTIONS "
                    f"(expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), "
                    f"INTERVAL 1 DAY))",
                ),
                operation=TelemetryOperation.QUERY,
            )
            started = monotonic_ns()
            self._backend.append(
                staged_rows,
                staging_target,
                max_batch_rows=self._max_batch_rows,
            )
            self._telemetry.record_committed_stream(
                rows_written=len(staged_rows), duration_ms=(monotonic_ns() - started) // 1_000_000
            )
            self._telemetry.run(
                partial(self._client.query, _create_target_sql(target_id, staging_id, columns)),
                operation=TelemetryOperation.QUERY,
            )
            _apply_schema_evolution(
                cast("_LoadClient", self._client),
                target_id,
                target,
                self._schema_evolution,
                telemetry=self._telemetry,
            )
            merge_sql = _merge_sql(target_id, staging_id, columns, target.business_key)
            if target.fence is not None:
                merge_script = fenced_dml(merge_sql, target.fence)
                merge_config = fencing_job_config(target.fence)
                merge = self._telemetry.run(
                    lambda: self._client.query(merge_script, job_config=merge_config),
                    operation=TelemetryOperation.QUERY,
                    retry_mutation=True,
                )
            else:
                merge = self._telemetry.run(
                    lambda: self._client.query(merge_sql), operation=TelemetryOperation.QUERY
                )
            return (
                merge.num_dml_affected_rows
                if merge.num_dml_affected_rows is not None
                else len(staged_rows)
            )
        finally:
            self._client.delete_table(staging_id, not_found_ok=True)


class BigQueryStorageIncrementalWriter(BigQueryStorageScd1Writer):
    """Storage Write staging plus cursor validation and idempotent keyed merge."""

    mode = WriteMode.INCREMENTAL

    def __init__(
        self,
        *,
        project: str,
        cursor_field: str,
        client: _BigQueryClient | None = None,
        backend: PendingStreamBackend | None = None,
        max_batch_rows: int = 10_000,
        schema_evolution: SchemaEvolution = SchemaEvolution.STRICT,
    ) -> None:
        super().__init__(
            project=project,
            client=client,
            backend=backend,
            max_batch_rows=max_batch_rows,
            schema_evolution=schema_evolution,
        )
        self._cursor_field = cursor_field

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        rows = [dict(record) for record in records]
        for index, row in enumerate(rows):
            if self._cursor_field not in row:
                raise BigQueryWriteError(
                    f"Cursor column {self._cursor_field!r} is absent from record {index}"
                )
            if row[self._cursor_field] is None:
                raise BigQueryWriteError(f"Record {index} has a null cursor value")
        return super().write(rows, target)


_PROTO_TYPES = {
    "BOOL": descriptor_pb2.FieldDescriptorProto.TYPE_BOOL,
    "BOOLEAN": descriptor_pb2.FieldDescriptorProto.TYPE_BOOL,
    "BYTES": descriptor_pb2.FieldDescriptorProto.TYPE_BYTES,
    "FLOAT64": descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE,
    "INT64": descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    "INTEGER": descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    "STRING": descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
}


def _message_type(
    fields: Sequence[WriteField],
) -> tuple[type[Any], descriptor_pb2.DescriptorProto]:
    file_descriptor = descriptor_pb2.FileDescriptorProto(
        name="dander_storage_write.proto",
        package="dander",
        syntax="proto2",
    )
    message = file_descriptor.message_type.add(name="DanderRow")
    for number, field in enumerate(fields, start=1):
        if field.mode == "REPEATED":
            raise BigQueryWriteError(
                f"Storage Write API does not support REPEATED field {field.name!r}; use load_job"
            )
        data_type = field.data_type.upper()
        try:
            proto_type = _PROTO_TYPES[data_type]
        except KeyError as error:
            raise BigQueryWriteError(
                f"Storage Write API does not support declared type {field.data_type!r}"
            ) from error
        message.field.add(
            name=field.name,
            number=number,
            label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
            type=proto_type,
        )
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_descriptor)
    descriptor = pool.FindMessageTypeByName("dander.DanderRow")
    message_class = message_factory.GetMessageClass(descriptor)
    return message_class, message


def _serialize_row(
    message_class: type[Any],
    row: Mapping[str, Any],
) -> bytes:
    message = message_class()
    for field in message.DESCRIPTOR.fields:
        value = row[field.name]
        if value is not None:
            setattr(message, field.name, value)
    return cast("bytes", message.SerializeToString())
