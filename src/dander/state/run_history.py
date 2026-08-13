"""Non-sensitive operational run history for local and BigQuery runtimes."""

from __future__ import annotations

import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, cast

from google.cloud import bigquery

from dander.identity import google_client_options

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class RunStatus(StrEnum):
    """Lifecycle state recorded for one pipeline run."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStage(StrEnum):
    """Current lifecycle stage for one project-defined pipeline run."""

    INGEST = "ingest"
    TRANSFORM = "transform"
    METADATA = "metadata"
    COMPLETE = "complete"


@dataclass(frozen=True)
class RunRecord:
    """Non-sensitive persisted pipeline lifecycle record."""

    run_id: str
    pipeline_id: str
    source: str
    status: RunStatus
    stage: RunStage
    started_at: str
    finished_at: str | None
    endpoints: int
    extracted: int
    affected: int
    models: int
    assertions: int
    assets: int
    failure_stage: RunStage | None
    failure_code: str | None = None
    failure_summary: str | None = None


class RunHistoryStore(ABC):
    """Persist lifecycle summaries without row values, cursors, or raw exceptions."""

    @abstractmethod
    def start(self, run_id: str, source: str, *, pipeline_id: str | None = None) -> None:
        """Record that a run started."""

    def restart_retryable(
        self,
        run_id: str,
        source: str,
        *,
        pipeline_id: str | None = None,
    ) -> None:
        """Restart one matching retryable failure without creating a duplicate run."""
        raise RuntimeError("Run history store does not support retryable restarts")

    def checkpoint(
        self,
        run_id: str,
        stage: RunStage,
        *,
        endpoints: int = 0,
        extracted: int = 0,
        affected: int = 0,
        models: int = 0,
        assertions: int = 0,
        assets: int = 0,
    ) -> None:
        """Persist a non-terminal lifecycle checkpoint when supported."""
        return None

    @abstractmethod
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
        """Record a terminal aggregate for a run."""

    def reconcile_interrupted(self, pipeline_id: str, *, current_run_id: str) -> None:
        """Mark older active rows failed after this runner has acquired the lease."""
        return None

    def recent(
        self,
        *,
        limit: int = 20,
        pipeline_id: str | None = None,
    ) -> tuple[RunRecord, ...]:
        """Return recent records when the backing store supports reads."""
        return ()


class _Job(Protocol):
    def result(self) -> Iterable[object]:
        """Wait for query completion."""


class _Client(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _Job:
        """Run a parameterized statement."""

    def get_table(self, table: str) -> _Table:
        """Return the current table schema."""


class _SchemaField(Protocol):
    @property
    def name(self) -> str:
        """Return the warehouse column name."""


class _Table(Protocol):
    @property
    def schema(self) -> Iterable[_SchemaField]:
        """Return the current warehouse schema."""


class _Row(Protocol):
    def __getitem__(self, key: str) -> object:
        """Return a projected field by name."""


class BigQueryRunHistoryStore(RunHistoryStore):
    """Persist run lifecycle summaries in a clustered BigQuery control table."""

    def __init__(
        self,
        *,
        project: str,
        dataset: str,
        client: _Client | None = None,
        initialize_on_read: bool = True,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", project):
            raise ValueError(f"Invalid BigQuery project: {project!r}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dataset):
            raise ValueError(f"Invalid BigQuery dataset: {dataset!r}")
        self._table = f"{project}.{dataset}._dander_runs"
        self._client = client or cast(
            "_Client",
            bigquery.Client(project=project, **google_client_options()),
        )
        self._initialize_on_read = initialize_on_read
        self._ready = False

    def migrate(self) -> None:
        """Idempotently ensure the current run-history schema exists."""
        self._ensure_table()

    def start(self, run_id: str, source: str, *, pipeline_id: str | None = None) -> None:
        self._ensure_table()
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter("source", "STRING", source),
                bigquery.ScalarQueryParameter("pipeline_id", "STRING", pipeline_id or source),
            ]
        )
        self._client.query(
            f"INSERT INTO `{self._table}` "
            "(run_id, pipeline_id, source_name, status, stage, started_at, finished_at, "
            "endpoints, extracted, affected, models, assertions, assets, failure_stage) "
            "VALUES (@run_id, @pipeline_id, @source, 'running', 'ingest', "
            "CURRENT_TIMESTAMP(), NULL, 0, 0, 0, 0, 0, 0, NULL)",
            job_config=config,
        ).result()

    def restart_retryable(
        self,
        run_id: str,
        source: str,
        *,
        pipeline_id: str | None = None,
    ) -> None:
        """Atomically resume one logical run only after a retryable terminal failure."""
        self._ensure_table()
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter("source", "STRING", source),
                bigquery.ScalarQueryParameter("pipeline_id", "STRING", pipeline_id or source),
            ]
        )
        self._client.query(
            "DECLARE matching_count INT64; "
            f"SET matching_count = (SELECT COUNT(*) FROM `{self._table}` "
            "WHERE run_id = @run_id); "
            "ASSERT matching_count = 0 OR (matching_count = 1 AND EXISTS ("
            f"SELECT 1 FROM `{self._table}` WHERE run_id = @run_id "
            "AND source_name = @source AND COALESCE(pipeline_id, source_name) = @pipeline_id "
            "AND status = 'failed' AND failure_code IN ('catalog_failed', "
            "'destination_write_failed', 'extraction_failed', 'lease_failed', "
            "'rate_limited'))) AS 'run is not a matching retryable failure'; "
            f"MERGE `{self._table}` AS target USING (SELECT @run_id AS run_id, "
            "@pipeline_id AS pipeline_id, @source AS source_name) AS incoming "
            "ON target.run_id = incoming.run_id "
            "WHEN MATCHED THEN UPDATE SET status = 'running', stage = 'ingest', "
            "finished_at = NULL, endpoints = 0, extracted = 0, affected = 0, models = 0, "
            "assertions = 0, assets = 0, failure_stage = NULL, failure_code = NULL, "
            "failure_summary = NULL "
            "WHEN NOT MATCHED THEN INSERT (run_id, pipeline_id, source_name, status, stage, "
            "started_at, finished_at, endpoints, extracted, affected, models, assertions, "
            "assets, failure_stage, failure_code, failure_summary) VALUES (incoming.run_id, "
            "incoming.pipeline_id, incoming.source_name, 'running', 'ingest', "
            "CURRENT_TIMESTAMP(), NULL, 0, 0, 0, 0, 0, 0, NULL, NULL, NULL)",
            job_config=config,
        ).result()

    def checkpoint(
        self,
        run_id: str,
        stage: RunStage,
        *,
        endpoints: int = 0,
        extracted: int = 0,
        affected: int = 0,
        models: int = 0,
        assertions: int = 0,
        assets: int = 0,
    ) -> None:
        self._update(
            run_id,
            status=None,
            stage=stage,
            endpoints=endpoints,
            extracted=extracted,
            affected=affected,
            models=models,
            assertions=assertions,
            assets=assets,
            failure_stage=None,
        )

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
        self._update(
            run_id,
            status=status,
            stage=(
                RunStage.COMPLETE
                if status in {RunStatus.SUCCEEDED, RunStatus.SKIPPED}
                else failure_stage
            ),
            endpoints=endpoints,
            extracted=extracted,
            affected=affected,
            models=models,
            assertions=assertions,
            assets=assets,
            failure_stage=failure_stage,
            failure_code=failure_code,
            failure_summary=failure_summary,
        )

    def _update(
        self,
        run_id: str,
        *,
        status: RunStatus | None,
        stage: RunStage | None,
        endpoints: int,
        extracted: int,
        affected: int,
        models: int,
        assertions: int,
        assets: int,
        failure_stage: RunStage | None,
        failure_code: str | None = None,
        failure_summary: str | None = None,
    ) -> None:
        self._ensure_table()
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter(
                    "status", "STRING", status.value if status is not None else "running"
                ),
                bigquery.ScalarQueryParameter("stage", "STRING", (stage or RunStage.INGEST).value),
                bigquery.ScalarQueryParameter("endpoints", "INT64", endpoints),
                bigquery.ScalarQueryParameter("extracted", "INT64", extracted),
                bigquery.ScalarQueryParameter("affected", "INT64", affected),
                bigquery.ScalarQueryParameter("models", "INT64", models),
                bigquery.ScalarQueryParameter("assertions", "INT64", assertions),
                bigquery.ScalarQueryParameter("assets", "INT64", assets),
                bigquery.ScalarQueryParameter(
                    "failure_stage",
                    "STRING",
                    failure_stage.value if failure_stage is not None else None,
                ),
                bigquery.ScalarQueryParameter("failure_code", "STRING", failure_code),
                bigquery.ScalarQueryParameter("failure_summary", "STRING", failure_summary),
            ]
        )
        finished_at = "CURRENT_TIMESTAMP()" if status is not None else "finished_at"
        self._client.query(
            f"UPDATE `{self._table}` SET status = @status, stage = @stage, "
            f"finished_at = {finished_at}, endpoints = @endpoints, extracted = @extracted, "
            "affected = @affected, models = @models, assertions = @assertions, "
            "assets = @assets, failure_stage = @failure_stage, failure_code = @failure_code, "
            "failure_summary = @failure_summary "
            "WHERE run_id = @run_id",
            job_config=config,
        ).result()

    def _ensure_table(self) -> None:
        if self._ready:
            return
        self._client.query(
            f"CREATE TABLE IF NOT EXISTS `{self._table}` ("
            "run_id STRING NOT NULL, pipeline_id STRING, source_name STRING NOT NULL, "
            "status STRING NOT NULL, stage STRING, started_at TIMESTAMP NOT NULL, "
            "finished_at TIMESTAMP, endpoints INT64 NOT NULL, extracted INT64 NOT NULL, "
            "affected INT64 NOT NULL, models INT64, assertions INT64, assets INT64, "
            "failure_stage STRING, failure_code STRING, failure_summary STRING) "
            "CLUSTER BY pipeline_id, status"
        ).result()
        additive_columns = (
            ("pipeline_id", "STRING"),
            ("stage", "STRING"),
            ("models", "INT64"),
            ("assertions", "INT64"),
            ("assets", "INT64"),
            ("failure_stage", "STRING"),
            ("failure_code", "STRING"),
            ("failure_summary", "STRING"),
        )
        existing_columns = {
            field.name.casefold() for field in self._client.get_table(self._table).schema
        }
        missing_columns = tuple(
            (column, data_type)
            for column, data_type in additive_columns
            if column.casefold() not in existing_columns
        )
        if missing_columns:
            clauses = ", ".join(
                f"ADD COLUMN IF NOT EXISTS {column} {data_type}"
                for column, data_type in missing_columns
            )
            self._client.query(f"ALTER TABLE `{self._table}` {clauses}").result()
        self._ready = True

    def recent(
        self,
        *,
        limit: int = 20,
        pipeline_id: str | None = None,
    ) -> tuple[RunRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if self._initialize_on_read:
            self._ensure_table()
        parameters = [bigquery.ScalarQueryParameter("limit", "INT64", limit)]
        where = ""
        if pipeline_id is not None:
            where = " WHERE COALESCE(pipeline_id, source_name) = @pipeline_id"
            parameters.append(bigquery.ScalarQueryParameter("pipeline_id", "STRING", pipeline_id))
        rows = self._client.query(
            f"SELECT run_id, COALESCE(pipeline_id, source_name) AS pipeline_id, "
            "source_name, status, COALESCE(stage, IF(status = 'succeeded', "
            "'complete', 'ingest')) AS stage, started_at, finished_at, endpoints, "
            "extracted, affected, COALESCE(models, 0) AS models, "
            "COALESCE(assertions, 0) AS assertions, COALESCE(assets, 0) AS assets, "
            "failure_stage, "
            "JSON_VALUE(TO_JSON_STRING(history_row), '$.failure_code') AS failure_code, "
            "JSON_VALUE(TO_JSON_STRING(history_row), '$.failure_summary') AS failure_summary "
            f"FROM `{self._table}` AS history_row{where} "
            "ORDER BY started_at DESC LIMIT @limit",
            job_config=bigquery.QueryJobConfig(query_parameters=parameters),
        ).result()
        return tuple(_bigquery_run_record(cast("_Row", row)) for row in rows)

    def reconcile_interrupted(self, pipeline_id: str, *, current_run_id: str) -> None:
        self._ensure_table()
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("pipeline_id", "STRING", pipeline_id),
                bigquery.ScalarQueryParameter("current_run_id", "STRING", current_run_id),
            ]
        )
        self._client.query(
            f"UPDATE `{self._table}` SET status = 'failed', finished_at = CURRENT_TIMESTAMP(), "
            "failure_stage = COALESCE(stage, 'ingest'), failure_code = 'interrupted_run', "
            "failure_summary = 'A later execution acquired the expired pipeline lease.' "
            "WHERE status = 'running' AND COALESCE(pipeline_id, source_name) = @pipeline_id "
            "AND run_id != @current_run_id",
            job_config=config,
        ).result()


class SqliteRunHistoryStore(RunHistoryStore):
    """Persist sandbox run summaries beside local watermark state."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "run_id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL, "
                "started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT, "
                "endpoints INTEGER NOT NULL DEFAULT 0, extracted INTEGER NOT NULL DEFAULT 0, "
                "affected INTEGER NOT NULL DEFAULT 0, pipeline_id TEXT, stage TEXT, "
                "models INTEGER NOT NULL DEFAULT 0, assertions INTEGER NOT NULL DEFAULT 0, "
                "assets INTEGER NOT NULL DEFAULT 0, failure_stage TEXT, failure_code TEXT, "
                "failure_summary TEXT)"
            )
            existing = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            for column, declaration in (
                ("pipeline_id", "TEXT"),
                ("stage", "TEXT"),
                ("models", "INTEGER NOT NULL DEFAULT 0"),
                ("assertions", "INTEGER NOT NULL DEFAULT 0"),
                ("assets", "INTEGER NOT NULL DEFAULT 0"),
                ("failure_stage", "TEXT"),
                ("failure_code", "TEXT"),
                ("failure_summary", "TEXT"),
            ):
                if column not in existing:
                    connection.execute(f"ALTER TABLE runs ADD COLUMN {column} {declaration}")

    def start(self, run_id: str, source: str, *, pipeline_id: str | None = None) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "INSERT INTO runs (run_id, source, pipeline_id, status, stage) "
                "VALUES (?, ?, ?, 'running', 'ingest')",
                (run_id, source, pipeline_id or source),
            )

    def restart_retryable(
        self,
        run_id: str,
        source: str,
        *,
        pipeline_id: str | None = None,
    ) -> None:
        """Resume a failed logical run while rejecting unrelated or permanent outcomes."""
        with sqlite3.connect(self._path) as connection:
            result = connection.execute(
                "INSERT INTO runs (run_id, source, pipeline_id, status, stage) "
                "VALUES (?, ?, ?, 'running', 'ingest') "
                "ON CONFLICT(run_id) DO UPDATE SET status = 'running', stage = 'ingest', "
                "finished_at = NULL, endpoints = 0, extracted = 0, affected = 0, models = 0, "
                "assertions = 0, assets = 0, failure_stage = NULL, failure_code = NULL, "
                "failure_summary = NULL WHERE runs.source = excluded.source "
                "AND COALESCE(runs.pipeline_id, runs.source) = excluded.pipeline_id "
                "AND runs.status = 'failed' AND runs.failure_code IN ('catalog_failed', "
                "'destination_write_failed', 'extraction_failed', 'lease_failed', "
                "'rate_limited')",
                (run_id, source, pipeline_id or source),
            )
            if result.rowcount != 1:
                raise RuntimeError("Run is not a matching retryable failure")

    def checkpoint(
        self,
        run_id: str,
        stage: RunStage,
        *,
        endpoints: int = 0,
        extracted: int = 0,
        affected: int = 0,
        models: int = 0,
        assertions: int = 0,
        assets: int = 0,
    ) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "UPDATE runs SET stage = ?, endpoints = ?, extracted = ?, affected = ?, "
                "models = ?, assertions = ?, assets = ? WHERE run_id = ?",
                (
                    stage.value,
                    endpoints,
                    extracted,
                    affected,
                    models,
                    assertions,
                    assets,
                    run_id,
                ),
            )

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
        stage = (
            RunStage.COMPLETE
            if status in {RunStatus.SUCCEEDED, RunStatus.SKIPPED}
            else failure_stage
        )
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "UPDATE runs SET status = ?, stage = ?, finished_at = CURRENT_TIMESTAMP, "
                "endpoints = ?, extracted = ?, affected = ?, models = ?, assertions = ?, "
                "assets = ?, failure_stage = ?, failure_code = ?, failure_summary = ? "
                "WHERE run_id = ?",
                (
                    status.value,
                    (stage or RunStage.INGEST).value,
                    endpoints,
                    extracted,
                    affected,
                    models,
                    assertions,
                    assets,
                    failure_stage.value if failure_stage is not None else None,
                    failure_code,
                    failure_summary,
                    run_id,
                ),
            )

    def recent(
        self,
        *,
        limit: int = 20,
        pipeline_id: str | None = None,
    ) -> tuple[RunRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        query = (
            "SELECT run_id, COALESCE(pipeline_id, source), source, status, "
            "COALESCE(stage, CASE WHEN status = 'succeeded' THEN 'complete' ELSE 'ingest' END), "
            "started_at, finished_at, endpoints, extracted, affected, "
            "COALESCE(models, 0), COALESCE(assertions, 0), COALESCE(assets, 0), failure_stage, "
            "failure_code, failure_summary "
            "FROM runs"
        )
        parameters: tuple[object, ...] = ()
        if pipeline_id is not None:
            query += " WHERE COALESCE(pipeline_id, source) = ?"
            parameters = (pipeline_id,)
        query += " ORDER BY started_at DESC LIMIT ?"
        parameters = (*parameters, limit)
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_sqlite_run_record(row) for row in rows)

    def reconcile_interrupted(self, pipeline_id: str, *, current_run_id: str) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "UPDATE runs SET status = 'failed', finished_at = CURRENT_TIMESTAMP, "
                "failure_stage = COALESCE(stage, 'ingest'), failure_code = 'interrupted_run', "
                "failure_summary = 'A later execution acquired the expired pipeline lease.' "
                "WHERE status = 'running' AND COALESCE(pipeline_id, source) = ? AND run_id != ?",
                (pipeline_id, current_run_id),
            )


def _sqlite_run_record(row: tuple[object, ...]) -> RunRecord:
    return RunRecord(
        run_id=str(row[0]),
        pipeline_id=str(row[1]),
        source=str(row[2]),
        status=RunStatus(str(row[3])),
        stage=RunStage(str(row[4])),
        started_at=str(row[5]),
        finished_at=str(row[6]) if row[6] is not None else None,
        endpoints=int(cast("int", row[7])),
        extracted=int(cast("int", row[8])),
        affected=int(cast("int", row[9])),
        models=int(cast("int", row[10])),
        assertions=int(cast("int", row[11])),
        assets=int(cast("int", row[12])),
        failure_stage=RunStage(str(row[13])) if row[13] is not None else None,
        failure_code=str(row[14]) if row[14] is not None else None,
        failure_summary=str(row[15]) if row[15] is not None else None,
    )


def _bigquery_run_record(row: _Row) -> RunRecord:
    failure_stage = row["failure_stage"]
    finished_at = row["finished_at"]
    return RunRecord(
        run_id=str(row["run_id"]),
        pipeline_id=str(row["pipeline_id"]),
        source=str(row["source_name"]),
        status=RunStatus(str(row["status"])),
        stage=RunStage(str(row["stage"])),
        started_at=str(row["started_at"]),
        finished_at=str(finished_at) if finished_at is not None else None,
        endpoints=int(cast("int", row["endpoints"])),
        extracted=int(cast("int", row["extracted"])),
        affected=int(cast("int", row["affected"])),
        models=int(cast("int", row["models"])),
        assertions=int(cast("int", row["assertions"])),
        assets=int(cast("int", row["assets"])),
        failure_stage=RunStage(str(failure_stage)) if failure_stage is not None else None,
        failure_code=(str(row["failure_code"]) if row["failure_code"] is not None else None),
        failure_summary=(
            str(row["failure_summary"]) if row["failure_summary"] is not None else None
        ),
    )
