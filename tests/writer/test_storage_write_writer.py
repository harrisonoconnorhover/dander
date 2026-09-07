"""Storage Write API staging tests with no network or cloud credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from dander.concurrency import FencingToken
from dander.telemetry import TelemetryOperation
from dander.writer import (
    BigQueryStorageIncrementalWriter,
    BigQueryStorageScd1Writer,
    BigQueryWriteError,
    WriteField,
    WriteTarget,
)
from dander.writer.storage_write import _message_type, _serialize_row

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from google.cloud import bigquery


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


def test_storage_writer_uses_pending_backend_then_idempotent_merge() -> None:
    client = _Client()
    backend = _Backend()
    writer = BigQueryStorageScd1Writer(
        project="unit-project",
        client=client,
        backend=backend,
        max_batch_rows=2,
    )

    affected = writer.write(
        [
            {"id": "one", "count": 1},
            {"id": "one", "count": 2},
            {"id": "two", "count": 3},
        ],
        _target(),
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
    message_class, _ = _message_type(target)

    serialized = _serialize_row(message_class, {"id": "one", "count": 3}, target)
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
