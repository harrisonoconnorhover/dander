"""Repair SQL executes against rows locally; live BigQuery qualification remains separate."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from dander.concurrency import FencingToken
from dander.ingestion import Endpoint, RawField, SourceConfig
from dander.pipeline.graph import PipelineGraph
from dander.pipeline.repair import GraphRepairError, GraphRepairWindow, plan_graph_repair
from dander.pipeline.runtime import GraphRuntimeError, plan_graph_execution
from dander.providers.bigquery.graph import BigQueryGraphRunner

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dander.pipeline.runtime import GraphExecutionPlan

_START = date(2026, 9, 1)
_END = date(2026, 9, 3)
_RAW = "unit-project.raw.records_events"
_TARGET = "unit-project.curated.events"
_LEASE = "unit-project.state.leases"


class _Ownership:
    fence = FencingToken(lease_table=_LEASE, pipeline_id="repair", run_id="repair-run", token=1)

    def verify(self) -> None:
        pass


def repair_graph() -> PipelineGraph:
    fields = [
        {"name": "id", "type": "INT64"},
        {"name": "event_date", "type": "DATE"},
        {"name": "value", "type": "STRING"},
    ]
    return PipelineGraph.model_validate(
        {
            "name": "repair_fixture",
            "nodes": [
                {
                    "id": "source",
                    "name": "Events",
                    "type": "source",
                    "config": {"connector": "records", "endpoint": "events"},
                    "fields": fields,
                },
                {
                    "id": "target",
                    "name": "Events",
                    "type": "target",
                    "fields": fields,
                    "config": {
                        "writer": {
                            "write_mode": "replace",
                            "destination": {
                                "dataset": "curated",
                                "table": "events",
                                "business_key": ["id"],
                            },
                            "partitioning": {"field": "event_date"},
                        }
                    },
                },
            ],
            "edges": [
                {
                    "from": "source",
                    "to": "target",
                    "mappings": [
                        {"source": field["name"], "target": field["name"]} for field in fields
                    ],
                }
            ],
        }
    )


def repair_source() -> SourceConfig:
    return SourceConfig(
        name="records",
        base_url="https://unused.example.test",
        auth_strategy="none",
        endpoints=[
            Endpoint(
                name="events",
                path="/events",
                primary_key=["id"],
                raw_schema=[
                    RawField(name="id", data_type="INT64"),
                    RawField(name="event_date", data_type="DATE"),
                    RawField(name="value", data_type="STRING"),
                ],
            )
        ],
    )


def _plan() -> GraphExecutionPlan:
    return plan_graph_execution(
        repair_graph(),
        repair_source(),
        project="unit-project",
        dataset="raw",
        repair_window=GraphRepairWindow(start_date=_START, end_date=_END),
    )


class _Job:
    def __init__(self, rows: list[dict[str, int]] | None = None) -> None:
        self.rows = rows or []

    def result(self) -> object:
        return self.rows


class _SqliteClient:
    """Execute generated SELECT/ASSERT/DML with only dialect syntax translated.

    This exercises the actual row predicates, cross-window joins, and rollback boundaries.
    It is deliberately not evidence of BigQuery dialect or provider behavior.
    """

    def __init__(
        self, raw: Sequence[tuple[object, ...]], target: Sequence[tuple[object, ...]]
    ) -> None:
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.execute(
            f"CREATE TABLE `{_LEASE}` (pipeline_id TEXT, run_id TEXT, fencing_token INTEGER, "
            "lease_expires_at TEXT, heartbeat_at TEXT)"
        )
        self.db.execute(
            f"INSERT INTO `{_LEASE}` VALUES ('repair', 'repair-run', 1, '2099-01-01 00:00:00', '')"
        )
        for table, rows in ((_RAW, raw), (_TARGET, target)):
            self.db.execute(f"CREATE TABLE `{table}` (id INT64, event_date DATE, value STRING)")
            self.db.executemany(f"INSERT INTO `{table}` VALUES (?, ?, ?)", rows)
        self.queries: list[str] = []
        self.deleted: list[str] = []

    def query(self, query: str, *, job_config: object | None = None) -> _Job:
        parameters = {
            parameter.name: parameter.value
            for parameter in getattr(job_config, "query_parameters", [])
        }
        self.queries.append(query)
        query = re.sub(r"OPTIONS \(expiration_timestamp=.*?\)\)\n", "", query)
        query = re.sub(r"`([^`]+)`\.`([^`]+)`\.`([^`]+)`", r"`\1.\2.\3`", query)
        query = re.sub(r"DATE '([0-9-]+)'", r"'\1'", query)
        query = query.replace("CURRENT_TIMESTAMP()", "CURRENT_TIMESTAMP")
        deleted = inserted = last_count = 0
        try:
            for statement in query.split(";"):
                statement = statement.strip()
                if not statement or statement.startswith("DECLARE "):
                    continue
                if statement.startswith("ASSERT "):
                    if statement == "ASSERT @@row_count = 1 AS 'Dander pipeline lease lost'":
                        if last_count != 1:
                            raise GraphRuntimeError("Dander pipeline lease lost")
                        continue
                    assertion = re.fullmatch(
                        r"ASSERT NOT EXISTS \((.*)\) AS '(.*)'", statement, re.S
                    )
                    assert assertion is not None, statement
                    if self.db.execute(assertion[1]).fetchone() is not None:
                        raise GraphRuntimeError(assertion[2])
                elif statement.startswith("SET dander_repair_deleted"):
                    deleted = last_count
                elif statement.startswith("SET dander_repair_inserted"):
                    inserted = last_count
                elif statement.startswith("SELECT dander_repair_inserted"):
                    return _Job([{"rows_written": inserted, "rows_affected": deleted + inserted}])
                else:
                    # SQLite requires AS for DELETE aliases; BigQuery permits either spelling.
                    statement = re.sub(
                        r"(DELETE FROM `[^`]+`) t WHERE", r"\1 AS t WHERE", statement
                    )
                    last_count = self.db.execute(statement, parameters).rowcount
        except Exception:
            if self.db.in_transaction:
                self.db.rollback()
            raise
        return _Job()

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        assert not_found_ok
        self.db.execute(f"DROP TABLE IF EXISTS `{table}`")
        self.deleted.append(table)

    def rows(self, table: str = _TARGET) -> list[tuple[Any, ...]]:
        return list(self.db.execute(f"SELECT * FROM `{table}` ORDER BY id"))


def test_repair_replaces_only_selected_dates_from_retained_raw_and_reports_real_counts() -> None:
    raw = [
        (1, "2026-08-31", "raw-before"),
        (2, "2026-09-01", "fixed"),
        (4, "2026-09-03", "raw-end"),
    ]
    old = [
        (1, "2026-08-31", "keep-before"),
        (2, "2026-09-01", "old"),
        (3, "2026-09-02", "deleted"),
        (4, "2026-09-03", "keep-end"),
    ]
    client = _SqliteClient(raw, old)

    result = BigQueryGraphRunner(plan=_plan(), project="unit-project", client=client).build(
        Path("."), ownership=_Ownership()
    )

    assert client.rows() == [
        (1, "2026-08-31", "keep-before"),
        (2, "2026-09-01", "fixed"),
        (4, "2026-09-03", "keep-end"),
    ]
    assert client.rows(_RAW) == raw
    assert sum(item.rows_written for item in result.telemetry) == 1
    assert sum(item.rows_affected for item in result.telemetry) == 3
    assert len(client.deleted) == 1


@pytest.mark.parametrize(
    ("raw", "target", "message"),
    [
        ([(1, "2026-09-01", "new")], [(1, "2026-08-31", "old")], "overlap target rows"),
        ([(1, "2026-09-03", "new")], [(1, "2026-09-01", "old")], "moved outside"),
        (
            [(1, "2026-09-01", "a"), (1, "2026-09-02", "b")],
            [(1, "2026-09-01", "old")],
            "must be unique",
        ),
        ([(None, "2026-09-01", "new")], [(1, "2026-09-01", "old")], "must not be null"),
        ([(1, None, "new")], [(1, "2026-09-01", "old")], "moved outside"),
        ([(1, "2026-09-01", "new")], [(1, None, "old")], "overlap target rows"),
    ],
)
def test_unsafe_window_repair_rolls_back_every_target_row_and_cleans_stage(
    raw: list[tuple[object, ...]],
    target: list[tuple[object, ...]],
    message: str,
) -> None:
    client = _SqliteClient(raw, target)

    with pytest.raises(GraphRuntimeError, match=message):
        BigQueryGraphRunner(plan=_plan(), project="unit-project", client=client).build(
            Path("."), ownership=_Ownership()
        )

    assert client.rows() == target
    assert len(client.deleted) == 1


def test_empty_selected_raw_window_deletes_only_selected_target_dates() -> None:
    client = _SqliteClient([], [(1, "2026-09-01", "remove"), (2, "2026-09-03", "keep")])
    result = BigQueryGraphRunner(plan=_plan(), project="unit-project", client=client).build(
        Path("."), ownership=_Ownership()
    )
    assert client.rows() == [(2, "2026-09-03", "keep")]
    assert sum(item.rows_written for item in result.telemetry) == 0
    assert sum(item.rows_affected for item in result.telemetry) == 1


def test_unrelated_null_dates_null_keys_and_duplicate_keys_outside_window_are_preserved() -> None:
    outside = [
        (None, "2026-08-31", "null-key"),
        (1, None, "null-date"),
        (3, "2026-09-03", "duplicate-a"),
        (3, "2026-09-04", "duplicate-b"),
    ]
    client = _SqliteClient(
        [(2, "2026-09-01", "fixed"), (5, None, "unused"), (5, None, "unused-duplicate")],
        [*outside, (2, "2026-09-01", "old")],
    )

    BigQueryGraphRunner(plan=_plan(), project="unit-project", client=client).build(
        Path("."), ownership=_Ownership()
    )

    assert client.rows() == [outside[0], outside[1], (2, "2026-09-01", "fixed"), *outside[2:]]


def test_repair_requires_fencing_and_rejects_a_stale_lease_transactionally() -> None:
    original = [(1, "2026-09-01", "original")]
    client = _SqliteClient([(1, "2026-09-01", "fixed")], original)
    runner = BigQueryGraphRunner(plan=_plan(), project="unit-project", client=client)
    with pytest.raises(GraphRuntimeError, match="lease fencing"):
        runner.build(Path("."))
    assert client.queries == []

    client.db.execute(f"UPDATE `{_LEASE}` SET fencing_token = 2")
    with pytest.raises(GraphRuntimeError, match="lease lost"):
        runner.build(Path("."), ownership=_Ownership())
    assert client.rows() == original
    assert len(client.deleted) == 1


def test_insert_failure_rolls_back_the_preceding_date_delete() -> None:
    original = [(1, "2026-09-01", "original"), (2, "2026-09-03", "keep")]
    client = _SqliteClient([(1, "2026-09-01", "reject")], [])
    client.db.execute(f"DROP TABLE `{_TARGET}`")
    client.db.execute(
        f"CREATE TABLE `{_TARGET}` "
        "(id INT64, event_date DATE, value STRING CHECK(value != 'reject'))"
    )
    client.db.executemany(f"INSERT INTO `{_TARGET}` VALUES (?, ?, ?)", original)

    with pytest.raises(sqlite3.IntegrityError):
        BigQueryGraphRunner(plan=_plan(), project="unit-project", client=client).build(
            Path("."), ownership=_Ownership()
        )

    assert client.rows() == original
    assert len(client.deleted) == 1


def test_timestamp_selection_uses_utc_calendar_boundaries() -> None:
    graph = repair_graph()
    for node in graph.nodes:
        node.fields[1].type = "TIMESTAMP"
    source = repair_source()
    source.endpoints[0].raw_schema[1].data_type = "TIMESTAMP"
    plan = plan_graph_execution(
        graph,
        source,
        project="unit-project",
        dataset="raw",
        repair_window=GraphRepairWindow(start_date=_START, end_date=_END),
    )
    before = "2026-09-01T00:30:00+01:00"  # August 31 in UTC.
    inside = "2026-09-01T00:00:00Z"
    end = "2026-09-03T00:00:00Z"
    client = _SqliteClient(
        [(1, before, "new-before"), (2, inside, "fixed"), (3, end, "new-end")],
        [(1, before, "keep-before"), (2, inside, "old"), (3, end, "keep-end")],
    )

    BigQueryGraphRunner(plan=plan, project="unit-project", client=client).build(
        Path("."), ownership=_Ownership()
    )

    assert client.rows() == [(1, before, "keep-before"), (2, inside, "fixed"), (3, end, "keep-end")]


@pytest.mark.parametrize("provider", ["postgresql", "redshift", "snowflake"])
def test_other_graph_runners_reject_repair_before_constructing_provider_clients(
    provider: str,
) -> None:
    import importlib

    module = importlib.import_module(f"dander.providers.{provider}.transform")
    runner = getattr(
        module,
        {"postgresql": "PostgreSQL", "redshift": "Redshift", "snowflake": "Snowflake"}[provider]
        + "GraphRunner",
    )
    kwargs: dict[str, object] = {"plan": _plan(), "database": "unused", "target_fence": None}
    if provider == "postgresql":
        kwargs.update(pool=None, timeouts=None)
    else:
        kwargs["connection_factory"] = None
        if provider == "redshift":
            kwargs["statement_timeout_ms"] = 10

    with pytest.raises(GraphRuntimeError, match="unavailable"):
        runner(**kwargs)


@pytest.mark.parametrize(
    "invalid",
    [
        "20260901",
        "2026-9-01",
        "2026-02-30",
        "2026-09-01T00:00:00Z",
        1788220800,
        datetime(2026, 9, 1),
    ],
)
def test_repair_dates_are_strict_calendar_dates(invalid: object) -> None:
    with pytest.raises(ValidationError):
        GraphRepairWindow.model_validate({"start_date": invalid, "end_date": "2026-09-03"})


@pytest.mark.parametrize("end", ["2026-09-01", "2026-08-31"])
def test_repair_window_must_be_nonempty(end: str) -> None:
    with pytest.raises(ValidationError, match="after start_date"):
        GraphRepairWindow.model_validate({"start_date": "2026-09-01", "end_date": end})


def test_repair_eligibility_requires_declared_partition_and_business_key() -> None:
    graph = repair_graph()
    window = GraphRepairWindow(start_date=_START, end_date=_END)
    target = graph.nodes[-1]
    payload = graph.model_dump(mode="json", by_alias=True)
    payload["nodes"][-1]["config"]["writer"]["destination"]["business_key"] = []
    with pytest.raises(GraphRepairError, match="business-key"):
        plan_graph_repair(PipelineGraph.model_validate(payload), window)
    target.fields[1].type = "STRING"
    with pytest.raises(GraphRepairError, match="DATE or TIMESTAMP"):
        plan_graph_repair(graph, window)


@pytest.mark.parametrize("invalid_destination", ["raw_input", "duplicate_output"])
def test_repair_rejects_destinations_that_could_overwrite_retained_inputs_or_each_other(
    invalid_destination: str,
) -> None:
    payload = repair_graph().model_dump(mode="json", by_alias=True)
    if invalid_destination == "raw_input":
        payload["nodes"][-1]["config"]["writer"]["destination"].update(
            dataset="raw", table="records_events"
        )
        message = "retained raw"
    else:
        duplicate = dict(payload["nodes"][-1], id="another_target")
        payload["nodes"].append(duplicate)
        payload["edges"].append(dict(payload["edges"][0], to="another_target"))
        message = "distinct destinations"

    with pytest.raises(GraphRuntimeError, match=message):
        plan_graph_execution(
            PipelineGraph.model_validate(payload),
            repair_source(),
            project="unit-project",
            dataset="raw",
            repair_window=GraphRepairWindow(start_date=_START, end_date=_END),
        )
