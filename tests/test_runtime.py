"""Pipeline commit-order tests for DANDER-20."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from dander.concurrency import FencingToken, TargetFence
from dander.ingestion import load_source_config
from dander.ingestion.source import Endpoint, RawField, Source, SourceConfig
from dander.runtime import PipelineRunner, RawSchemaError, WatermarkConflictError
from dander.state import LeaseLostError, RunHistoryStore, RunStage, RunStatus, WatermarkStore
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.warehouse import ProviderExtension, RelationRef
from dander.writer import WriteMode, WritePattern, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from dander.warehouse import PreparedWarehouseStatement, RelationSchema


class _Source(Source):
    def __init__(
        self,
        events: list[str],
        *,
        expected_since: str | None = "2026-01-01T00:00:00Z",
        cursor_param: str | None = None,
    ) -> None:
        super().__init__(
            SourceConfig(
                name="example",
                base_url="https://example.test",
                auth_strategy="api_key_basic",
                auth_ref="DANDER_TEST_REFERENCE",
                endpoints=[
                    Endpoint(
                        name="widgets",
                        path="/widgets",
                        incremental_cursor="updated_at",
                        cursor_param=cursor_param,
                        primary_key=["id"],
                    )
                ],
            )
        )
        self._events = events
        self._expected_since = expected_since

    def discover(self) -> Mapping[str, Any]:
        return {}

    def extract(self, endpoint: str, *, since: str | None = None) -> Iterator[Mapping[str, Any]]:
        assert endpoint == "widgets"
        assert since == self._expected_since
        self._events.append("extract")
        yield {"id": "one", "updated_at": "2026-01-02T00:00:00Z"}
        yield {"id": "two", "updated_at": "2026-01-03T00:00:00Z"}


class _Writer(WritePattern):
    mode = WriteMode.SCD1

    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self._events = events
        self._fail = fail

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        assert target.table == "example_widgets"
        assert list(records)
        self._events.append("write")
        if self._fail:
            raise RuntimeError("synthetic write failure")
        return 2


class _BatchedWriter(WritePattern):
    mode = WriteMode.SCD1
    supports_batched_writes = True

    def __init__(self, *, fail_batch: int | None = None) -> None:
        self.batches: list[list[dict[str, Any]]] = []
        self.state: dict[str, dict[str, Any]] = {}
        self._fail_batch = fail_batch

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        assert target.table == "example_widgets"
        batch = [dict(record) for record in records]
        self.batches.append(batch)
        if self._fail_batch == len(self.batches):
            raise RuntimeError("synthetic batch failure")
        for record in batch:
            self.state[str(record["id"])] = record
        return len(batch)


class _TelemetryWriter(_BatchedWriter):
    def __init__(self) -> None:
        super().__init__()
        self._pending: tuple[OperationTelemetry, ...] = ()
        self.target: WriteTarget | None = None

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        batch = [dict(record) for record in records]
        self.batches.append(batch)
        self.target = target
        affected = len(batch)
        self._pending = (
            OperationTelemetry(
                provider="testwarehouse",
                operation=TelemetryOperation.LOAD,
                rows_written=affected,
            ),
        )
        return affected

    def drain_telemetry(self) -> tuple[OperationTelemetry, ...]:
        pending, self._pending = self._pending, ()
        return pending


class _FencedWriter(_Writer):
    requires_publication_fence = True

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        assert target.publication_fence is not None
        return super().write(records, target)


class _TargetFence:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def claim(self, target: RelationRef, fence: FencingToken) -> TargetFence:
        self._events.append("claim")
        return TargetFence(
            fence_table=f"{target.namespace}.dander_target_commits",
            target_id=".".join(target.coordinates),
            authority_id=fence.resolved_authority_id,
            authority_epoch=fence.authority_epoch,
            pipeline_id=fence.pipeline_id,
            run_id=fence.run_id,
            token=fence.token,
        )

    def prepare_dml(self, statement: str, fence: TargetFence) -> PreparedWarehouseStatement:
        raise NotImplementedError


class _DeclaredSource(Source):
    def __init__(self, records: list[Mapping[str, Any]]) -> None:
        super().__init__(
            SourceConfig(
                name="declared",
                base_url="https://example.test",
                auth_strategy="none",
                endpoints=[
                    Endpoint(
                        name="companies",
                        path="/companies",
                        primary_key=["id"],
                        raw_schema=[
                            RawField(name="id", data_type="INT64", mode="REQUIRED"),
                            RawField(
                                name="properties",
                                data_type="RECORD",
                                fields=[
                                    RawField(name="name", data_type="STRING"),
                                    RawField(name="active", data_type="BOOL"),
                                ],
                            ),
                            RawField(name="tags", data_type="STRING", mode="REPEATED"),
                            RawField(
                                name="contacts",
                                data_type="RECORD",
                                mode="REPEATED",
                                fields=[
                                    RawField(name="email", data_type="STRING"),
                                    RawField(name="primary", data_type="BOOL"),
                                ],
                            ),
                            RawField(name="metadata", data_type="JSON"),
                        ],
                    )
                ],
            )
        )
        self._records = records

    def discover(self) -> Mapping[str, Any]:
        return {}

    def extract(self, endpoint: str, *, since: str | None = None) -> Iterator[Mapping[str, Any]]:
        assert endpoint == "companies"
        assert since is None
        yield from self._records


class _CapturingWriter(WritePattern):
    mode = WriteMode.SCD1
    supports_batched_writes = True

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.target: WriteTarget | None = None

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        self.rows.extend(dict(record) for record in records)
        self.target = target
        return len(self.rows)


class _Watermarks(WatermarkStore):
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.committed: str | None = None

    def get(self, source: str, entity: str) -> str | None:
        assert (source, entity) == ("example", "widgets")
        self._events.append("get")
        return self.committed or "2026-01-01T00:00:00Z"

    def set(self, source: str, entity: str, cursor: str) -> None:
        assert (source, entity) == ("example", "widgets")
        self._events.append("set")
        self.committed = cursor

    def compare_and_set(
        self,
        source: str,
        entity: str,
        *,
        expected: str | None,
        cursor: str,
        fence: FencingToken | None = None,
    ) -> bool:
        assert (source, entity) == ("example", "widgets")
        current = self.committed or "2026-01-01T00:00:00Z"
        if current != expected:
            return False
        self.set(source, entity, cursor)
        return True


class _Ownership:
    fence = None

    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes = outcomes

    def verify(self) -> None:
        if not self._outcomes.pop(0):
            raise LeaseLostError("Pipeline lease ownership was lost")


class _FencedOwnership:
    fence = FencingToken(
        lease_table=None,
        authority_id="postgresql:test-state",
        authority_epoch=1,
        pipeline_id="example_pipeline",
        run_id="run-fenced",
        token=7,
    )

    def verify(self) -> None:
        return None


class _History(RunHistoryStore):
    def __init__(self) -> None:
        self.started: tuple[str, str] | None = None
        self.finished: tuple[str, RunStatus, int, int, int] | None = None

    def start(self, run_id: str, source: str, *, pipeline_id: str | None = None) -> None:
        assert pipeline_id is None
        self.started = (run_id, source)

    def finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        endpoints: int,
        extracted: int,
        affected: int,
        models: int = 0,
        assertions: int = 0,
        assets: int = 0,
        failure_stage: RunStage | None = None,
        failure_code: str | None = None,
        failure_summary: str | None = None,
    ) -> None:
        assert (models, assertions, assets, failure_stage, failure_code, failure_summary) == (
            0,
            0,
            0,
            None,
            None,
            None,
        )
        self.finished = (run_id, status, endpoints, extracted, affected)


def _runner(events: list[str], *, fail: bool = False) -> tuple[PipelineRunner, _Watermarks]:
    watermarks = _Watermarks(events)
    return (
        PipelineRunner(
            source=_Source(events),
            writer=_Writer(events, fail=fail),
            watermarks=watermarks,
            project="unit-project",
            dataset="raw",
        ),
        watermarks,
    )


def test_runner_commits_maximum_cursor_after_write() -> None:
    events: list[str] = []
    runner, watermarks = _runner(events)

    result = runner.run()

    assert events == ["get", "extract", "write", "set"]
    assert watermarks.committed == "2026-01-03T00:00:00Z"
    assert result.endpoints[0].affected == 2


def test_runner_preserves_endpoint_relation_without_provider_reinterpretation() -> None:
    captured: list[RelationRef] = []

    class RelationWriter(_Writer):
        def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
            captured.append(target.relation_ref)
            return super().write(records, target)

    events: list[str] = []
    relation = RelationRef(
        catalog="warehouse_db",
        namespace="landing",
        name="example_widgets",
    )
    runner = PipelineRunner(
        source=_Source(events),
        writer=RelationWriter(events),
        watermarks=_Watermarks(events),
        endpoint_relations={"widgets": relation},
    )

    runner.run()

    assert captured == [relation]


def test_runner_claims_required_destination_before_extraction() -> None:
    events: list[str] = []
    runner = PipelineRunner(
        source=_Source(events),
        writer=_FencedWriter(events),
        watermarks=_Watermarks(events),
        project="dander_test",
        dataset="raw",
        target_fence=_TargetFence(events),
    )

    runner.run(run_id="run-fenced", ownership=_FencedOwnership())

    assert events == ["get", "claim", "extract", "write", "set"]


def test_runner_extracts_only_selected_configured_endpoints() -> None:
    extracted: list[str] = []

    class SelectedSource(Source):
        def __init__(self) -> None:
            super().__init__(
                SourceConfig(
                    name="selected",
                    base_url="https://example.test",
                    auth_strategy="none",
                    endpoints=[
                        Endpoint(name="one", path="/one"),
                        Endpoint(name="two", path="/two"),
                    ],
                )
            )

        def discover(self) -> Mapping[str, Any]:
            return {}

        def extract(
            self,
            endpoint: str,
            *,
            since: str | None = None,
        ) -> Iterator[Mapping[str, Any]]:
            assert since is None
            extracted.append(endpoint)
            yield {"id": endpoint}

    class SelectedWriter(WritePattern):
        mode = WriteMode.SCD1

        def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
            assert target.table == "selected_two"
            return len(list(records))

    result = PipelineRunner(
        source=SelectedSource(),
        writer=SelectedWriter(),
        watermarks=_Watermarks([]),
        project="unit-project",
        dataset="raw",
        endpoint_names=["two"],
    ).run()

    assert extracted == ["two"]
    assert [endpoint.endpoint for endpoint in result.endpoints] == ["two"]


def test_runner_normalizes_sparse_nested_records_from_declared_schema() -> None:
    writer = _CapturingWriter()
    runner = PipelineRunner(
        source=_DeclaredSource(
            [
                {
                    "id": "42",
                    "properties": {"name": "Dander"},
                    "tags": None,
                    "contacts": [{"email": "proof@example.test"}],
                    "metadata": {"source": ["proof", 1]},
                }
            ]
        ),
        writer=writer,
        watermarks=_Watermarks([]),
        project="unit-project",
        dataset="raw",
    )

    result = runner.run()

    assert writer.rows == [
        {
            "id": 42,
            "properties": {"name": "Dander", "active": None},
            "tags": [],
            "contacts": [{"email": "proof@example.test", "primary": None}],
            "metadata": {"source": ["proof", 1]},
        }
    ]
    assert result.endpoints[0].extracted == 1
    assert writer.target is not None
    assert writer.target.schema[0].mode == "REQUIRED"
    assert writer.target.schema[1].fields[1].name == "active"


def test_salesforce_query_record_envelope_satisfies_declared_runtime_schema() -> None:
    config = load_source_config(
        Path(__file__).parents[1] / "connectors" / "salesforce_jwt.example.yaml"
    )
    config.endpoints = [config.endpoints[0]]

    class _SalesforceSource(Source):
        def discover(self) -> Mapping[str, Any]:
            return {}

        def extract(
            self,
            endpoint: str,
            *,
            since: str | None = None,
        ) -> Iterator[Mapping[str, Any]]:
            assert endpoint == "accounts"
            assert since is None
            yield {
                "attributes": {
                    "type": "Account",
                    "url": "/services/data/v67.0/sobjects/Account/001TEST",
                },
                "Id": "001TEST",
                "Name": "Dander Synthetic Account",
                "CreatedDate": "2026-08-02T12:00:00.000+0000",
                "LastModifiedDate": "2026-08-02T12:01:00.000+0000",
                "SystemModstamp": "2026-08-02T12:01:00.000+0000",
                "IsDeleted": False,
            }

    class _SalesforceWatermarks(WatermarkStore):
        committed: str | None = None

        def get(self, source: str, entity: str) -> str | None:
            assert (source, entity) == ("salesforce", "accounts")
            return self.committed

        def set(self, source: str, entity: str, cursor: str) -> None:
            assert (source, entity) == ("salesforce", "accounts")
            self.committed = cursor

        def compare_and_set(
            self,
            source: str,
            entity: str,
            *,
            expected: str | None,
            cursor: str,
            fence: FencingToken | None = None,
        ) -> bool:
            del fence
            if self.get(source, entity) != expected:
                return False
            self.set(source, entity, cursor)
            return True

    writer = _CapturingWriter()
    watermarks = _SalesforceWatermarks()
    result = PipelineRunner(
        source=_SalesforceSource(config),
        writer=writer,
        watermarks=watermarks,
        project="unit-project",
        dataset="raw",
    ).run()

    assert result.endpoints[0].extracted == 1
    assert writer.target is not None
    assert writer.target.table == "salesforce_accounts"
    assert writer.rows[0]["attributes"] == {
        "type": "Account",
        "url": "/services/data/v67.0/sobjects/Account/001TEST",
    }
    assert writer.rows[0]["AnnualRevenue"] is None
    assert writer.rows[0]["CreatedDate"] == datetime(2026, 8, 2, 12, tzinfo=UTC)
    assert writer.rows[0]["LastModifiedDate"] == datetime(2026, 8, 2, 12, 1, tzinfo=UTC)
    assert writer.rows[0]["SystemModstamp"] == datetime(2026, 8, 2, 12, 1, tzinfo=UTC)
    assert watermarks.committed == "2026-08-02T12:01:00+00:00"


def test_runner_propagates_declared_schema_for_empty_endpoint() -> None:
    writer = _CapturingWriter()
    runner = PipelineRunner(
        source=_DeclaredSource([]),
        writer=writer,
        watermarks=_Watermarks([]),
        project="unit-project",
        dataset="raw",
    )

    result = runner.run()

    assert result.endpoints[0].extracted == 0
    assert writer.rows == []
    assert writer.target is not None
    assert [field.name for field in writer.target.schema] == [
        "id",
        "properties",
        "tags",
        "contacts",
        "metadata",
    ]


def test_runner_preserves_canonical_extensions_and_drains_batch_telemetry() -> None:
    extension = ProviderExtension(provider="snowflake", name="fallback", value="variant")
    source = _DeclaredSource([])
    source.config.endpoints[0].raw_schema[-1].extensions = (extension,)
    writer = _TelemetryWriter()

    result = PipelineRunner(
        source=source,
        writer=writer,
        watermarks=_Watermarks([]),
        project="unit-project",
        dataset="raw",
        batch_rows=1,
    ).run()

    assert writer.target is not None
    metadata = writer.target.canonical_schema.fields[-1]
    assert metadata.data_type.kind.value == "json"
    assert extension in metadata.extensions
    assert result.telemetry == result.endpoints[0].telemetry
    assert len(result.telemetry) == 1
    assert result.telemetry[0].rows_written == 0


@pytest.mark.parametrize(
    ("record", "match"),
    [
        ({"id": 1, "unexpected": "x"}, r"record\[0\]\.unexpected"),
        (
            {"id": 1, "properties": {"unexpected": "x"}},
            r"record\[0\]\.properties\.unexpected",
        ),
        ({"properties": {}}, r"record\[0\]\.id"),
        ({"id": "not-an-integer"}, r"Invalid INT64 field at record\[0\]\.id"),
        ({"id": 1, "properties": "not-an-object"}, r"record\[0\]\.properties"),
        ({"id": 1, "tags": "not-a-list"}, r"record\[0\]\.tags"),
        ({"id": 1, "tags": [None]}, r"record\[0\]\.tags\[0\]"),
    ],
)
def test_runner_rejects_records_that_violate_declared_schema(
    record: Mapping[str, Any],
    match: str,
) -> None:
    writer = _CapturingWriter()
    runner = PipelineRunner(
        source=_DeclaredSource([record]),
        writer=writer,
        watermarks=_Watermarks([]),
        project="unit-project",
        dataset="raw",
    )

    with pytest.raises(RawSchemaError, match=match):
        runner.run()

    assert writer.rows == []


def test_raw_schema_failure_does_not_include_or_chain_source_value() -> None:
    source_value = "sensitive-not-an-integer"
    runner = PipelineRunner(
        source=_DeclaredSource([{"id": source_value}]),
        writer=_CapturingWriter(),
        watermarks=_Watermarks([]),
        project="unit-project",
        dataset="raw",
    )

    with pytest.raises(RawSchemaError) as raised:
        runner.run()

    assert source_value not in str(raised.value)
    assert raised.value.__cause__ is None


def test_direct_source_without_declared_schema_logs_deprecation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner, _ = _runner([])

    runner.run()

    assert "undeclared_raw_schema_deprecated" in caplog.messages


def test_runner_does_not_advance_cursor_when_write_fails() -> None:
    events: list[str] = []
    runner, watermarks = _runner(events, fail=True)

    with pytest.raises(RuntimeError, match="synthetic write failure"):
        runner.run()

    assert events == ["get", "extract", "write"]
    assert watermarks.committed is None


def test_full_refresh_ignores_existing_cursor_but_records_observed_cursor() -> None:
    events: list[str] = []
    watermarks = _Watermarks(events)
    runner = PipelineRunner(
        source=_Source(events, expected_since=None),
        writer=_Writer(events),
        watermarks=watermarks,
        project="unit-project",
        dataset="raw",
        resume_from_watermark=False,
    )

    runner.run()

    assert events == ["get", "extract", "write", "set"]
    assert watermarks.committed == "2026-01-03T00:00:00Z"


def test_runner_never_regresses_watermark_after_full_read() -> None:
    events: list[str] = []
    watermarks = _Watermarks(events)
    watermarks.committed = "2026-01-04T00:00:00Z"
    runner = PipelineRunner(
        source=_Source(
            events,
            expected_since="2026-01-04T00:00:00Z",
            cursor_param="",
        ),
        writer=_Writer(events),
        watermarks=watermarks,
        project="unit-project",
        dataset="raw",
    )

    result = runner.run()

    assert events == ["get", "extract", "write", "set"]
    assert watermarks.committed == "2026-01-04T00:00:00Z"
    assert result.endpoints[0].committed_cursor == "2026-01-04T00:00:00Z"


@pytest.mark.parametrize(
    ("fail", "status", "endpoints", "extracted", "affected"),
    [
        (False, RunStatus.SUCCEEDED, 1, 2, 2),
        (True, RunStatus.FAILED, 0, 0, 0),
    ],
)
def test_runner_records_non_sensitive_terminal_history(
    fail: bool,
    status: RunStatus,
    endpoints: int,
    extracted: int,
    affected: int,
) -> None:
    events: list[str] = []
    watermarks = _Watermarks(events)
    history = _History()
    runner = PipelineRunner(
        source=_Source(events),
        writer=_Writer(events, fail=fail),
        watermarks=watermarks,
        project="unit-project",
        dataset="raw",
        history=history,
    )

    if fail:
        with pytest.raises(RuntimeError, match="synthetic write failure"):
            runner.run()
    else:
        runner.run()

    assert history.started is not None
    run_id, source = history.started
    assert source == "example"
    assert history.finished == (run_id, status, endpoints, extracted, affected)


def test_scd1_runtime_writes_large_endpoint_in_bounded_batches() -> None:
    total = 100_003
    yielded = 0

    class _LargeSource(_Source):
        def extract(
            self,
            endpoint: str,
            *,
            since: str | None = None,
        ) -> Iterator[Mapping[str, Any]]:
            nonlocal yielded
            assert endpoint == "widgets"
            assert since == "2026-01-01T00:00:00Z"
            for index in range(total):
                yielded += 1
                yield {
                    "id": str(index),
                    "updated_at": f"{index:06d}",
                }

    class _ObservingWriter(_BatchedWriter):
        def __init__(self) -> None:
            super().__init__()
            self.first_write_yielded: int | None = None

        def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
            if self.first_write_yielded is None:
                self.first_write_yielded = yielded
            return super().write(records, target)

    writer = _ObservingWriter()
    watermarks = _Watermarks([])
    runner = PipelineRunner(
        source=_LargeSource([]),
        writer=writer,
        watermarks=watermarks,
        project="unit-project",
        dataset="raw",
        batch_rows=1_024,
    )

    result = runner.run()

    assert yielded == total
    assert writer.first_write_yielded == 1_024
    assert len(writer.batches) == 98
    assert max(map(len, writer.batches)) == 1_024
    assert len(writer.batches[-1]) == 675
    assert result.endpoints[0].extracted == total
    assert result.endpoints[0].affected == total
    assert watermarks.committed == "100002"


def test_scd1_cross_batch_duplicate_is_last_record_wins() -> None:
    events: list[str] = []

    class _DuplicateSource(_Source):
        def extract(
            self,
            endpoint: str,
            *,
            since: str | None = None,
        ) -> Iterator[Mapping[str, Any]]:
            assert endpoint == "widgets"
            assert since == "2026-01-01T00:00:00Z"
            yield {"id": "one", "updated_at": "2026-01-02T00:00:00Z"}
            yield {"id": "one", "updated_at": "2026-01-03T00:00:00Z"}

    writer = _BatchedWriter()
    runner = PipelineRunner(
        source=_DuplicateSource(events),
        writer=writer,
        watermarks=_Watermarks(events),
        project="unit-project",
        dataset="raw",
        batch_rows=1,
    )

    runner.run()

    assert writer.state["one"]["updated_at"] == "2026-01-03T00:00:00Z"


def test_scd1_does_not_advance_watermark_when_later_batch_fails() -> None:
    events: list[str] = []
    watermarks = _Watermarks(events)
    runner = PipelineRunner(
        source=_Source(events),
        writer=_BatchedWriter(fail_batch=2),
        watermarks=watermarks,
        project="unit-project",
        dataset="raw",
        batch_rows=1,
    )

    with pytest.raises(RuntimeError, match="synthetic batch failure"):
        runner.run()

    assert watermarks.committed is None


def test_runner_fails_closed_before_writer_after_heartbeat_loss() -> None:
    events: list[str] = []
    writer = _BatchedWriter()
    runner = PipelineRunner(
        source=_Source(events),
        writer=writer,
        watermarks=_Watermarks(events),
        project="unit-project",
        dataset="raw",
    )

    with pytest.raises(LeaseLostError, match="ownership was lost"):
        runner.run(ownership=_Ownership([True, False]))

    assert writer.batches == []


def test_runner_rejects_stale_watermark_commit_after_successful_write() -> None:
    events: list[str] = []

    class _ConflictingWatermarks(_Watermarks):
        def compare_and_set(
            self,
            source: str,
            entity: str,
            *,
            expected: str | None,
            cursor: str,
            fence: FencingToken | None = None,
        ) -> bool:
            return False

    watermarks = _ConflictingWatermarks(events)
    runner = PipelineRunner(
        source=_Source(events),
        writer=_BatchedWriter(),
        watermarks=watermarks,
        project="unit-project",
        dataset="raw",
    )

    with pytest.raises(WatermarkConflictError, match="boundary changed"):
        runner.run()

    assert watermarks.committed is None


def test_runner_validates_declared_schema_before_state_source_or_writer_io() -> None:
    events: list[str] = []

    class _DeclaredIncrementalSource(Source):
        def __init__(self) -> None:
            super().__init__(
                SourceConfig(
                    name="example",
                    base_url="https://example.test",
                    auth_strategy="none",
                    endpoints=[
                        Endpoint(
                            name="widgets",
                            path="/widgets",
                            incremental_cursor="updated_at",
                            primary_key=["id"],
                            raw_schema=[
                                RawField(name="id", data_type="STRING", mode="REQUIRED"),
                                RawField(name="updated_at", data_type="TIMESTAMP"),
                            ],
                        )
                    ],
                )
            )

        def discover(self) -> Mapping[str, Any]:
            return {}

        def extract(
            self,
            endpoint: str,
            *,
            since: str | None = None,
        ) -> Iterator[Mapping[str, Any]]:
            del endpoint, since
            events.append("extract")
            yield {"id": "one"}

    class _RejectingSchemaMapper:
        def canonical_schema(self, fields: Sequence[object]) -> RelationSchema:
            assert fields
            events.append("schema")
            raise ValueError("synthetic unsupported schema")

    writer = _BatchedWriter()
    runner = PipelineRunner(
        source=_DeclaredIncrementalSource(),
        writer=writer,
        watermarks=_Watermarks(events),
        project="unit-project",
        dataset="raw",
        schema_mapper=_RejectingSchemaMapper(),
    )

    with pytest.raises(ValueError, match="synthetic unsupported schema"):
        runner.run()

    assert events == ["schema"]
    assert writer.batches == []


@pytest.mark.parametrize("batch_rows", [0, 100_001, True])
def test_runner_rejects_invalid_batch_rows(batch_rows: int) -> None:
    with pytest.raises(ValueError, match="batch_rows"):
        PipelineRunner(
            source=_Source([]),
            writer=_Writer([]),
            watermarks=_Watermarks([]),
            project="unit-project",
            dataset="raw",
            batch_rows=batch_rows,
        )
