"""Watermark / control state — last successful cursor per (source, entity).

Enables idempotent restarts: a re-run resumes from the last committed cursor rather than
re-pulling or corrupting data. Backed by BigQuery or Firestore. See ``steering/02-engineering.md``.
"""

from __future__ import annotations

import re
import sqlite3
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol, cast

from google.cloud import bigquery

from dander._bigquery_retry import run_mutation_with_retry
from dander.concurrency import FencingToken, fenced_dml, fencing_job_config

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path


class WatermarkStore(ABC):
    """Persists the incremental cursor for each (source, entity) pair."""

    @abstractmethod
    def get(self, source: str, entity: str) -> str | None:
        """Return the last successful cursor value, or ``None`` if never run."""

    @abstractmethod
    def set(self, source: str, entity: str, cursor: str) -> None:
        """Persist ``cursor`` after a successful load (called only on commit)."""

    def compare_and_set(
        self,
        source: str,
        entity: str,
        *,
        expected: str | None,
        cursor: str,
        fence: FencingToken | None = None,
    ) -> bool:
        """Commit only when the stored cursor still matches the extraction boundary."""
        if self.get(source, entity) != expected:
            return False
        self.set(source, entity, cursor)
        return True


class _QueryJob(Protocol):
    num_dml_affected_rows: int | None

    def result(self) -> Iterable[Mapping[str, Any]]:
        """Wait for query completion and return rows."""


class _BigQueryClient(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _QueryJob:
        """Run a BigQuery statement."""


class BigQueryWatermarkStore(WatermarkStore):
    """Persist committed cursors in a BigQuery control table."""

    def __init__(
        self,
        *,
        project: str,
        dataset: str,
        client: _BigQueryClient | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", project):
            raise ValueError(f"Invalid BigQuery project: {project!r}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dataset):
            raise ValueError(f"Invalid BigQuery dataset: {dataset!r}")
        self._table_id = f"{project}.{dataset}._dander_watermarks"
        self._client = client or cast("_BigQueryClient", bigquery.Client(project=project))
        self._table_ready = False

    def migrate(self) -> None:
        """Idempotently ensure the current watermark schema exists."""
        self._ensure_table()

    def get(self, source: str, entity: str) -> str | None:
        """Return the most recently committed cursor for `(source, entity)`."""
        self._ensure_table()
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("source", "STRING", source),
                bigquery.ScalarQueryParameter("entity", "STRING", entity),
            ]
        )
        rows = list(
            self._client.query(
                (
                    f"SELECT last_cursor FROM `{self._table_id}` "
                    "WHERE source_name = @source AND entity_name = @entity "
                    "LIMIT 1"
                ),
                job_config=config,
            ).result()
        )
        if not rows:
            return None
        cursor = rows[0]["last_cursor"]
        return str(cursor) if cursor is not None else None

    def set(self, source: str, entity: str, cursor: str) -> None:
        """Atomically insert or replace a committed cursor."""
        self._ensure_table()
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("source", "STRING", source),
                bigquery.ScalarQueryParameter("entity", "STRING", entity),
                bigquery.ScalarQueryParameter("cursor", "STRING", cursor),
            ]
        )
        run_mutation_with_retry(
            lambda: self._client.query(
                f"MERGE `{self._table_id}` AS target "
                "USING (SELECT @source AS source_name, @entity AS entity_name, "
                "@cursor AS last_cursor) AS incoming "
                "ON target.source_name = incoming.source_name "
                "AND target.entity_name = incoming.entity_name "
                "WHEN MATCHED THEN UPDATE SET "
                "last_cursor = incoming.last_cursor, updated_at = CURRENT_TIMESTAMP() "
                "WHEN NOT MATCHED THEN INSERT "
                "(source_name, entity_name, last_cursor, updated_at) "
                "VALUES (incoming.source_name, incoming.entity_name, "
                "incoming.last_cursor, CURRENT_TIMESTAMP())",
                job_config=config,
            )
        )

    def compare_and_set(
        self,
        source: str,
        entity: str,
        *,
        expected: str | None,
        cursor: str,
        fence: FencingToken | None = None,
    ) -> bool:
        """Atomically fence and commit only the cursor boundary this run extracted from."""
        self._ensure_table()
        parameters = [
            bigquery.ScalarQueryParameter("source", "STRING", source),
            bigquery.ScalarQueryParameter("entity", "STRING", entity),
            bigquery.ScalarQueryParameter("expected", "STRING", expected),
            bigquery.ScalarQueryParameter("cursor", "STRING", cursor),
        ]
        merge = (
            f"MERGE `{self._table_id}` AS target "
            "USING (SELECT @source AS source_name, @entity AS entity_name, "
            "@cursor AS last_cursor) AS incoming "
            "ON target.source_name = incoming.source_name "
            "AND target.entity_name = incoming.entity_name "
            "WHEN MATCHED AND target.last_cursor IS NOT DISTINCT FROM @expected "
            "THEN UPDATE SET last_cursor = incoming.last_cursor, "
            "updated_at = CURRENT_TIMESTAMP() "
            "WHEN NOT MATCHED AND @expected IS NULL THEN INSERT "
            "(source_name, entity_name, last_cursor, updated_at) "
            "VALUES (incoming.source_name, incoming.entity_name, "
            "incoming.last_cursor, CURRENT_TIMESTAMP())"
        )
        statement = merge
        if fence is not None:
            fence_config = fencing_job_config(fence)
            parameters.extend(fence_config.query_parameters)
            statement = fenced_dml(
                f"{merge};\nASSERT @@row_count = 1 AS 'Dander watermark boundary changed'",
                fence,
            )
        job = run_mutation_with_retry(
            lambda: self._client.query(
                statement,
                job_config=bigquery.QueryJobConfig(query_parameters=parameters),
            )
        )
        return fence is not None or job.num_dml_affected_rows == 1

    def _ensure_table(self) -> None:
        if self._table_ready:
            return
        self._client.query(
            f"CREATE TABLE IF NOT EXISTS `{self._table_id}` ("
            "source_name STRING NOT NULL, "
            "entity_name STRING NOT NULL, "
            "last_cursor STRING NOT NULL, "
            "updated_at TIMESTAMP NOT NULL"
            ") "
            "CLUSTER BY source_name, entity_name"
        ).result()
        self._table_ready = True


class SqliteWatermarkStore(WatermarkStore):
    """Persist local sandbox cursors in a small SQLite database."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS watermarks ("
                "source TEXT NOT NULL, "
                "entity TEXT NOT NULL, "
                "cursor TEXT NOT NULL, "
                "PRIMARY KEY (source, entity)"
                ")"
            )

    def get(self, source: str, entity: str) -> str | None:
        """Return a locally recorded cursor, if present."""
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT cursor FROM watermarks WHERE source = ? AND entity = ?",
                (source, entity),
            ).fetchone()
        return str(row[0]) if row else None

    def set(self, source: str, entity: str, cursor: str) -> None:
        """Insert or replace a local cursor atomically."""
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "INSERT INTO watermarks (source, entity, cursor) VALUES (?, ?, ?) "
                "ON CONFLICT(source, entity) DO UPDATE SET cursor = excluded.cursor",
                (source, entity, cursor),
            )

    def compare_and_set(
        self,
        source: str,
        entity: str,
        *,
        expected: str | None,
        cursor: str,
        fence: FencingToken | None = None,
    ) -> bool:
        """Serialize local cursor comparison and update in one immediate transaction."""
        with sqlite3.connect(self._path, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT cursor FROM watermarks WHERE source = ? AND entity = ?",
                (source, entity),
            ).fetchone()
            current = str(row[0]) if row is not None else None
            if current != expected:
                connection.rollback()
                return False
            connection.execute(
                "INSERT INTO watermarks (source, entity, cursor) VALUES (?, ?, ?) "
                "ON CONFLICT(source, entity) DO UPDATE SET cursor = excluded.cursor",
                (source, entity, cursor),
            )
            connection.commit()
        return True
