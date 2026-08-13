"""Local and BigQuery run-history persistence tests."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from dander.state import BigQueryRunHistoryStore, RunStage, RunStatus, SqliteRunHistoryStore

if TYPE_CHECKING:
    from pathlib import Path

    from google.cloud import bigquery


def test_sqlite_run_history_persists_terminal_aggregates(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store = SqliteRunHistoryStore(path)

    store.start("run-1", "greenhouse", pipeline_id="greenhouse_jobs")
    store.checkpoint(
        "run-1",
        RunStage.METADATA,
        endpoints=2,
        extracted=12,
        affected=10,
        models=1,
        assertions=2,
    )
    store.finish(
        "run-1",
        RunStatus.SUCCEEDED,
        endpoints=2,
        extracted=12,
        affected=10,
        models=1,
        assertions=2,
        assets=1,
    )

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT source, status, stage, endpoints, extracted, affected, models, assertions, "
            "assets FROM runs WHERE run_id = ?",
            ("run-1",),
        ).fetchone()
    assert row == ("greenhouse", "succeeded", "complete", 2, 12, 10, 1, 2, 1)
    records = store.recent(pipeline_id="greenhouse_jobs")
    assert len(records) == 1
    assert records[0].pipeline_id == "greenhouse_jobs"
    assert records[0].stage is RunStage.COMPLETE


def test_sqlite_run_history_records_overlap_as_skipped_complete(tmp_path: Path) -> None:
    store = SqliteRunHistoryStore(tmp_path / "state.db")
    store.start("run-skipped", "hubspot", pipeline_id="hubspot_companies")

    store.finish(
        "run-skipped",
        RunStatus.SKIPPED,
        endpoints=0,
        extracted=0,
        affected=0,
    )

    record = store.recent(pipeline_id="hubspot_companies")[0]
    assert record.status is RunStatus.SKIPPED
    assert record.stage is RunStage.COMPLETE
    assert record.failure_stage is None


def test_sqlite_run_history_reads_active_run(tmp_path: Path) -> None:
    store = SqliteRunHistoryStore(tmp_path / "state.db")
    store.start("run-active", "greenhouse", pipeline_id="greenhouse_jobs")

    record = store.recent(pipeline_id="greenhouse_jobs")[0]

    assert record.status is RunStatus.RUNNING
    assert record.stage is RunStage.INGEST
    assert record.finished_at is None


def test_sqlite_run_history_persists_sanitized_failure_details(tmp_path: Path) -> None:
    store = SqliteRunHistoryStore(tmp_path / "state.db")
    store.start("run-failed", "salesforce", pipeline_id="salesforce_crm")

    store.finish(
        "run-failed",
        RunStatus.FAILED,
        endpoints=0,
        extracted=0,
        affected=0,
        failure_stage=RunStage.INGEST,
        failure_code="authentication_failed",
        failure_summary="Authentication failed. Verify the configured secret.",
    )

    record = store.recent(pipeline_id="salesforce_crm")[0]
    assert record.failure_code == "authentication_failed"
    assert record.failure_summary == "Authentication failed. Verify the configured secret."


def test_sqlite_retry_restarts_one_logical_run_and_clears_failed_attempt(tmp_path: Path) -> None:
    store = SqliteRunHistoryStore(tmp_path / "state.db")
    store.start("run-retry", "greenhouse", pipeline_id="greenhouse_jobs")
    store.finish(
        "run-retry",
        RunStatus.FAILED,
        endpoints=1,
        extracted=3,
        affected=0,
        failure_stage=RunStage.INGEST,
        failure_code="extraction_failed",
        failure_summary="Source extraction failed after bounded retries.",
    )

    store.restart_retryable("run-retry", "greenhouse", pipeline_id="greenhouse_jobs")

    records = store.recent(pipeline_id="greenhouse_jobs")
    assert len(records) == 1
    restarted = records[0]
    assert restarted.status is RunStatus.RUNNING
    assert restarted.stage is RunStage.INGEST
    assert restarted.finished_at is None
    assert (restarted.endpoints, restarted.extracted, restarted.affected) == (0, 0, 0)
    assert restarted.failure_code is None
    assert restarted.failure_summary is None


def test_sqlite_retry_rejects_nonretryable_or_mismatched_run(tmp_path: Path) -> None:
    store = SqliteRunHistoryStore(tmp_path / "state.db")
    store.start("run-permanent", "greenhouse", pipeline_id="greenhouse_jobs")
    store.finish(
        "run-permanent",
        RunStatus.FAILED,
        endpoints=0,
        extracted=0,
        affected=0,
        failure_stage=RunStage.INGEST,
        failure_code="authentication_failed",
        failure_summary="Authentication failed.",
    )

    with pytest.raises(RuntimeError, match="matching retryable failure"):
        store.restart_retryable("run-permanent", "greenhouse", pipeline_id="greenhouse_jobs")

    assert store.recent(pipeline_id="greenhouse_jobs")[0].failure_code == "authentication_failed"


def test_sqlite_reconciles_only_older_active_runs_for_owned_pipeline(tmp_path: Path) -> None:
    store = SqliteRunHistoryStore(tmp_path / "state.db")
    store.start("old", "salesforce", pipeline_id="salesforce_crm")
    store.checkpoint("old", RunStage.TRANSFORM, endpoints=4, extracted=12, affected=12)
    store.start("current", "salesforce", pipeline_id="salesforce_crm")
    store.start("other", "greenhouse", pipeline_id="greenhouse_jobs")

    store.reconcile_interrupted("salesforce_crm", current_run_id="current")

    records = {record.run_id: record for record in store.recent(limit=10)}
    assert records["old"].status is RunStatus.FAILED
    assert records["old"].failure_stage is RunStage.TRANSFORM
    assert records["old"].failure_code == "interrupted_run"
    assert records["old"].finished_at is not None
    assert records["current"].status is RunStatus.RUNNING
    assert records["other"].status is RunStatus.RUNNING


class _QueryJob:
    def result(self) -> tuple[object, ...]:
        return ()


@dataclass
class _BigQueryClient:
    queries: list[str] = field(default_factory=list)
    existing_columns: set[str] = field(
        default_factory=lambda: {
            "run_id",
            "pipeline_id",
            "source_name",
            "status",
            "stage",
            "started_at",
            "finished_at",
            "endpoints",
            "extracted",
            "affected",
            "models",
            "assertions",
            "assets",
            "failure_stage",
            "failure_code",
            "failure_summary",
        }
    )

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _QueryJob:
        del job_config
        self.queries.append(query)
        return _QueryJob()

    def get_table(self, table: str) -> _BigQueryTable:
        assert table == "proof-project.dander_meta._dander_runs"
        return _BigQueryTable(
            schema=tuple(_BigQuerySchemaField(name=name) for name in self.existing_columns)
        )


@dataclass(frozen=True)
class _BigQuerySchemaField:
    name: str


@dataclass(frozen=True)
class _BigQueryTable:
    schema: tuple[_BigQuerySchemaField, ...]


def test_bigquery_history_current_schema_emits_no_alter() -> None:
    client = _BigQueryClient()
    store = BigQueryRunHistoryStore(
        project="proof-project",
        dataset="dander_meta",
        client=client,
    )

    store.start("run-1", "greenhouse", pipeline_id="greenhouse_jobs")
    first_queries = tuple(client.queries)
    store.start("run-2", "greenhouse", pipeline_id="greenhouse_jobs")

    assert len(first_queries) == 2
    assert first_queries[0].startswith("CREATE TABLE IF NOT EXISTS")
    assert first_queries[1].startswith("INSERT INTO")
    assert len(client.queries) == len(first_queries) + 1
    assert client.queries[-1].startswith("INSERT INTO")


def test_bigquery_history_batches_sparse_legacy_schema_migration() -> None:
    client = _BigQueryClient(existing_columns={"RUN_ID", "SOURCE_NAME", "STATUS"})
    store = BigQueryRunHistoryStore(
        project="proof-project",
        dataset="dander_meta",
        client=client,
    )

    store.start("run-1", "greenhouse", pipeline_id="greenhouse_jobs")

    alters = [query for query in client.queries if query.startswith("ALTER TABLE")]
    assert len(alters) == 1
    assert "ADD COLUMN IF NOT EXISTS pipeline_id STRING" in alters[0]
    assert "ADD COLUMN IF NOT EXISTS stage STRING" in alters[0]
    assert "ADD COLUMN IF NOT EXISTS failure_summary STRING" in alters[0]
    assert alters[0].count("ADD COLUMN IF NOT EXISTS") == 8


def test_bigquery_retry_is_atomic_and_restricted_to_retryable_failure() -> None:
    client = _BigQueryClient()
    store = BigQueryRunHistoryStore(
        project="proof-project",
        dataset="dander_meta",
        client=client,
    )

    store.restart_retryable("run-retry", "greenhouse", pipeline_id="greenhouse_jobs")

    script = client.queries[-1]
    assert script.startswith("DECLARE matching_count INT64")
    assert "ASSERT matching_count = 0 OR" in script
    assert "status = 'failed'" in script
    assert "'extraction_failed'" in script
    assert "MERGE `proof-project.dander_meta._dander_runs`" in script
    assert "finished_at = NULL" in script


class _FailingQueryJob(_QueryJob):
    def result(self) -> tuple[object, ...]:
        raise RuntimeError("schema migration failed")


@dataclass
class _FailingBigQueryClient(_BigQueryClient):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _QueryJob:
        del job_config
        self.queries.append(query)
        if query.startswith("ALTER TABLE"):
            return _FailingQueryJob()
        return _QueryJob()


def test_bigquery_history_migration_propagates_schema_failure() -> None:
    client = _FailingBigQueryClient(existing_columns={"run_id"})
    store = BigQueryRunHistoryStore(
        project="proof-project",
        dataset="dander_meta",
        client=client,
    )

    with pytest.raises(RuntimeError, match="schema migration failed"):
        store.start("run-1", "greenhouse", pipeline_id="greenhouse_jobs")

    assert len([query for query in client.queries if query.startswith("ALTER TABLE")]) == 1


def test_bigquery_history_can_read_without_creating_or_altering_tables() -> None:
    client = _BigQueryClient()
    store = BigQueryRunHistoryStore(
        project="proof-project",
        dataset="dander_meta",
        client=client,
        initialize_on_read=False,
    )

    assert store.recent(limit=1, pipeline_id="graph_records") == ()
    assert len(client.queries) == 1
    assert client.queries[0].startswith("SELECT ")
    assert "JSON_VALUE(TO_JSON_STRING(history_row), '$.failure_code')" in client.queries[0]
    assert "JSON_VALUE(TO_JSON_STRING(history_row), '$.failure_summary')" in client.queries[0]
    assert "AS history_row" in client.queries[0]
    assert "CREATE" not in client.queries[0]
    assert "ALTER" not in client.queries[0]


def test_bigquery_reconciliation_is_bounded_to_owned_pipeline_and_excludes_current() -> None:
    client = _BigQueryClient()
    store = BigQueryRunHistoryStore(
        project="proof-project",
        dataset="dander_meta",
        client=client,
    )

    store.reconcile_interrupted("salesforce_crm", current_run_id="current")

    update = client.queries[-1]
    assert "failure_code = 'interrupted_run'" in update
    assert "status = 'running'" in update
    assert "COALESCE(pipeline_id, source_name) = @pipeline_id" in update
    assert "run_id != @current_run_id" in update
