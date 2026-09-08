"""Storage Write API staging tests with no network or cloud credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest

from dander.concurrency import FencingToken
from dander.telemetry import TelemetryOperation
from dander.warehouse import canonical_schema_from_bigquery
from dander.writer import (
    BigQueryStorageIncrementalWriter,
    BigQueryStorageScd1Writer,
    BigQueryWriteError,
    WriteField,
    WriteTarget,
)
from dander.writer.storage_write import BigQueryPendingStreamBackend, _message_type, _serialize_row

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from google.cloud import bigquery, bigquery_storage_v1


class _Job:
    def __init__(self, *, affected: int | None = None) -> None:
        self.num_dml_affected_rows = affected

    def result(self) -> object:
        return self


class _Client:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.deleted: list[str] = []

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _Job:
        self.queries.append(query)
        return _Job(affected=2 if query.startswith("MERGE") else None)

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        assert not_found_ok
        self.deleted.append(table)


class _Backend:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.target: WriteTarget | None = None
        self.max_batch_rows = 0

    def append(
        self,
        rows: Sequence[Mapping[str, Any]],
        target: WriteTarget,
        *,
        max_batch_rows: int,
    ) -> None:
        self.rows = [dict(row) for row in rows]
        self.target = target
        self.max_batch_rows = max_batch_rows


def _target() -> WriteTarget:
    return WriteTarget(
        project="unit-project",
        dataset="raw",
        table="widgets",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="count", data_type="INT64"),
        ),
    )


@pytest.mark.parametrize("canonical_only", [False, True])
def test_storage_writer_uses_pending_backend_then_idempotent_merge(canonical_only: bool) -> None:
    client = _Client()
    backend = _Backend()
    writer = BigQueryStorageScd1Writer(
        project="unit-project",
        client=client,
        backend=backend,
        max_batch_rows=2,
    )

    target = _target()
    if canonical_only:
        target = WriteTarget(
            relation=target.relation,
            business_key=target.business_key,
            declared_schema=target.canonical_schema,
        )
    affected = writer.write(
        [
            {"id": "one", "count": 1},
            {"id": "one", "count": 2},
            {"id": "two", "count": 3},
        ],
        target,
    )

    assert affected == 2
    observations = writer.drain_telemetry()
    assert [item.operation for item in observations] == [
        TelemetryOperation.QUERY,
        TelemetryOperation.LOAD,
        TelemetryOperation.QUERY,
        TelemetryOperation.QUERY,
    ]
    assert observations[1].rows_written == 2
    assert writer.drain_telemetry() == ()
    assert backend.rows == [{"id": "one", "count": 2}, {"id": "two", "count": 3}]
    assert backend.max_batch_rows == 2
    assert client.queries[0].startswith("CREATE TABLE `unit-project.raw._dander_stage_widgets_")
    assert "OPTIONS (expiration_timestamp" in client.queries[0]
    assert client.queries[-1].startswith("MERGE `unit-project.raw.widgets`")
    assert backend.target is not None
    assert backend.target.schema == _target().schema
    assert client.deleted == [
        f"{backend.target.project}.{backend.target.dataset}.{backend.target.table}"
    ]


def test_storage_merge_honors_cloud_fencing_contract() -> None:
    client = _Client()
    backend = _Backend()
    writer = BigQueryStorageScd1Writer(
        project="unit-project",
        client=client,
        backend=backend,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="widgets",
        business_key=("id",),
        schema=_target().schema,
        fence=FencingToken(
            lease_table="unit-project.meta._dander_leases",
            pipeline_id="example_pipeline",
            run_id="run-one",
            token=3,
        ),
    )

    writer.write([{"id": "one", "count": 1}], target)

    script = client.queries[-1]
    assert script.startswith("BEGIN TRANSACTION;\nUPDATE `unit-project.meta._dander_leases`")
    assert "ASSERT @@row_count = 1 AS 'Dander pipeline lease lost'" in script
    assert "MERGE `unit-project.raw.widgets`" in script


def test_storage_incremental_requires_cursor_before_staging() -> None:
    client = _Client()
    writer = BigQueryStorageIncrementalWriter(
        project="unit-project",
        cursor_field="updated_at",
        client=client,
        backend=_Backend(),
    )

    with pytest.raises(BigQueryWriteError, match="Cursor column"):
        writer.write([{"id": "one", "count": 1}], _target())

    assert client.queries == []


def test_dynamic_proto_serializes_supported_scalar_schema() -> None:
    target = _target()
    message_class, _ = _message_type(target.schema)

    serialized = _serialize_row(message_class, {"id": "one", "count": 3})
    decoded = message_class.FromString(serialized)

    assert decoded.id == "one"
    assert decoded.count == 3


def test_storage_writer_rejects_undeclared_or_unsupported_schema() -> None:
    client = _Client()
    writer = BigQueryStorageScd1Writer(
        project="unit-project",
        client=client,
        backend=_Backend(),
    )
    unsupported = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="widgets",
        business_key=("id",),
        schema=(WriteField(name="id", data_type="TIMESTAMP"),),
    )

    with pytest.raises(BigQueryWriteError, match="does not support"):
        writer.write([{"id": "2026-01-01T00:00:00Z"}], unsupported)

    assert client.queries == []


def test_failed_pending_stream_does_not_report_a_successful_load() -> None:
    class FailingBackend(_Backend):
        def append(
            self, rows: Sequence[Mapping[str, Any]], target: WriteTarget, *, max_batch_rows: int
        ) -> None:
            raise RuntimeError("synthetic commit failure")

    client = _Client()
    writer = BigQueryStorageScd1Writer(
        project="unit-project", client=client, backend=FailingBackend()
    )
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        writer.write([{"id": "one", "count": 1}], _target())
    observations = writer.drain_telemetry()
    assert [item.operation for item in observations] == [TelemetryOperation.QUERY]
    assert len(client.deleted) == 1
    assert len(client.queries) == 1


@pytest.mark.parametrize("canonical_only", [False, True])
def test_pending_stream_preserves_rows_for_both_schema_boundaries(
    monkeypatch: pytest.MonkeyPatch, canonical_only: bool
) -> None:
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    source = _target()
    target = WriteTarget(
        relation=source.relation,
        business_key=source.business_key,
        schema=() if canonical_only else source.schema,
        declared_schema=source.canonical_schema if canonical_only else None,
    )
    client = Mock()
    client.create_write_stream.return_value.name = "projects/test/streams/one"
    client.batch_commit_write_streams.return_value.stream_errors = []
    stream = Mock()
    stream.send.return_value.result.return_value.error.code = 0
    factory = Mock(return_value=stream)
    monkeypatch.setattr("dander.writer.storage_write.writer.AppendRowsStream", factory)

    BigQueryPendingStreamBackend(
        client=cast("bigquery_storage_v1.BigQueryWriteClient", client)
    ).append([{"id": "one", "count": 3}, {"id": "two", "count": None}], target, max_batch_rows=1)

    template = factory.call_args.args[1]
    descriptor = descriptor_pb2.DescriptorProto()
    descriptor.ParseFromString(
        template.proto_rows.writer_schema.proto_descriptor.SerializeToString()
    )
    assert [field.name for field in descriptor.field] == ["id", "count"]
    file_descriptor = descriptor_pb2.FileDescriptorProto(name="observed.proto", syntax="proto2")
    file_descriptor.message_type.add().CopyFrom(descriptor)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_descriptor)
    row_type = cast(
        "type[Any]", message_factory.GetMessageClass(pool.FindMessageTypeByName(descriptor.name))
    )
    requests = [call.args[0] for call in stream.send.call_args_list]
    assert [request.offset for request in requests] == [0, 1]
    decoded = [
        row_type.FromString(request.proto_rows.rows.serialized_rows[0]) for request in requests
    ]
    assert decoded[0].id == "one"
    assert decoded[0].count == 3
    assert decoded[1].id == "two"
    assert not decoded[1].HasField("count")
    stream.close.assert_called_once_with()
    client.finalize_write_stream.assert_called_once_with(name="projects/test/streams/one")
    client.batch_commit_write_streams.assert_called_once()


@pytest.mark.parametrize("canonical_only", [False, True])
def test_pending_stream_rejects_unsupported_schema_before_creation(canonical_only: bool) -> None:
    fields = (WriteField(name="id", data_type="TIMESTAMP"),)
    target = WriteTarget(
        relation=_target().relation,
        schema=() if canonical_only else fields,
        declared_schema=canonical_schema_from_bigquery(fields) if canonical_only else None,
    )
    client = Mock()
    backend = BigQueryPendingStreamBackend(
        client=cast("bigquery_storage_v1.BigQueryWriteClient", client)
    )
    with pytest.raises(BigQueryWriteError, match="does not support declared type"):
        backend.append([{"id": "2026-01-01T00:00:00Z"}], target, max_batch_rows=1)
    client.create_write_stream.assert_not_called()


def test_explicit_legacy_schema_keeps_precedence_for_storage_staging() -> None:
    legacy = _target()
    target = WriteTarget(
        relation=legacy.relation,
        business_key=legacy.business_key,
        schema=legacy.schema,
        declared_schema=canonical_schema_from_bigquery(
            (WriteField(name="different", data_type="BOOL"),)
        ),
    )
    client = _Client()
    backend = _Backend()
    BigQueryStorageScd1Writer(project=target.project, client=client, backend=backend).write(
        [{"id": "one", "count": 1}], target
    )
    assert backend.target is not None
    assert backend.target.schema == legacy.schema
    assert "`id` STRING" in client.queries[0]
    assert "`count` INT64" in client.queries[0]


@pytest.mark.parametrize("canonical_only", [False, True])
def test_pending_stream_rejects_repeated_fields_before_creation(canonical_only: bool) -> None:
    fields = (WriteField(name="counts", data_type="INT64", mode="REPEATED"),)
    target = WriteTarget(
        relation=_target().relation,
        schema=() if canonical_only else fields,
        declared_schema=canonical_schema_from_bigquery(fields) if canonical_only else None,
    )
    client = Mock()
    client.create_write_stream.side_effect = AssertionError("unexpected stream creation")
    backend = BigQueryPendingStreamBackend(
        client=cast("bigquery_storage_v1.BigQueryWriteClient", client)
    )
    with pytest.raises(BigQueryWriteError, match="REPEATED.*load_job"):
        backend.append([{"counts": [1, 2]}], target, max_batch_rows=1)
    client.create_write_stream.assert_not_called()
