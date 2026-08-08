"""Durable metadata-spine snapshots for cloud and local runtimes."""

from __future__ import annotations

import json
import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from google.cloud import bigquery

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


@dataclass(frozen=True)
class MetadataSnapshot:
    """One atomic, versioned pipeline metadata snapshot."""

    pipeline_id: str
    run_id: str
    manifest: dict[str, object]
    updated_at: str


class MetadataStore(ABC):
    """Publish and read complete metadata snapshots."""

    @abstractmethod
    def publish(
        self,
        *,
        pipeline_id: str,
        run_id: str,
        manifest: dict[str, object],
    ) -> None:
        """Atomically replace one pipeline's current metadata snapshot."""

    @abstractmethod
    def snapshots(self, *, pipeline_id: str | None = None) -> tuple[MetadataSnapshot, ...]:
        """Return current snapshots, optionally for one pipeline."""


class _Row(Protocol):
    def __getitem__(self, key: str) -> object:
        """Return a projected row field."""


class _Job(Protocol):
    def result(self) -> Iterable[_Row]:
        """Wait for query completion and return rows."""


class _Client(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _Job:
        """Run a Standard SQL statement."""


class BigQueryMetadataStore(MetadataStore):
    """Keep one atomic semantic manifest per pipeline in BigQuery."""

    def __init__(self, *, project: str, dataset: str, client: _Client | None = None) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", project):
            raise ValueError(f"Invalid BigQuery project: {project!r}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dataset):
            raise ValueError(f"Invalid BigQuery dataset: {dataset!r}")
        self._table = f"{project}.{dataset}._dander_catalog"
        self._client = client or cast("_Client", bigquery.Client(project=project))
        self._ready = False

    def migrate(self) -> None:
        """Idempotently ensure the current metadata schema exists."""
        self._ensure_table()

    def publish(
        self,
        *,
        pipeline_id: str,
        run_id: str,
        manifest: dict[str, object],
    ) -> None:
        self._ensure_table()
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("pipeline_id", "STRING", pipeline_id),
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter("manifest_json", "STRING", payload),
            ]
        )
        self._client.query(
            f"MERGE `{self._table}` AS target "
            "USING (SELECT @pipeline_id AS pipeline_id, @run_id AS run_id, "
            "@manifest_json AS manifest_json) AS incoming "
            "ON target.pipeline_id = incoming.pipeline_id "
            "WHEN MATCHED THEN UPDATE SET run_id = incoming.run_id, "
            "manifest_json = incoming.manifest_json, updated_at = CURRENT_TIMESTAMP() "
            "WHEN NOT MATCHED THEN INSERT (pipeline_id, run_id, manifest_json, updated_at) "
            "VALUES (incoming.pipeline_id, incoming.run_id, incoming.manifest_json, "
            "CURRENT_TIMESTAMP())",
            job_config=config,
        ).result()

    def snapshots(self, *, pipeline_id: str | None = None) -> tuple[MetadataSnapshot, ...]:
        self._ensure_table()
        parameters: list[bigquery.ScalarQueryParameter] = []
        where = ""
        if pipeline_id is not None:
            where = " WHERE pipeline_id = @pipeline_id"
            parameters.append(bigquery.ScalarQueryParameter("pipeline_id", "STRING", pipeline_id))
        config = bigquery.QueryJobConfig(query_parameters=parameters)
        rows = self._client.query(
            f"SELECT pipeline_id, run_id, manifest_json, updated_at FROM `{self._table}`"
            f"{where} ORDER BY pipeline_id",
            job_config=config,
        ).result()
        return tuple(_snapshot_from_values(row) for row in rows)

    def _ensure_table(self) -> None:
        if self._ready:
            return
        self._client.query(
            f"CREATE TABLE IF NOT EXISTS `{self._table}` ("
            "pipeline_id STRING NOT NULL, run_id STRING NOT NULL, "
            "manifest_json STRING NOT NULL, updated_at TIMESTAMP NOT NULL) "
            "CLUSTER BY pipeline_id"
        ).result()
        self._ready = True


class SqliteMetadataStore(MetadataStore):
    """Keep local sandbox metadata beside its watermark and run state."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata_snapshots ("
                "pipeline_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, "
                "manifest_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    def publish(
        self,
        *,
        pipeline_id: str,
        run_id: str,
        manifest: dict[str, object],
    ) -> None:
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "INSERT INTO metadata_snapshots (pipeline_id, run_id, manifest_json) "
                "VALUES (?, ?, ?) ON CONFLICT(pipeline_id) DO UPDATE SET "
                "run_id = excluded.run_id, manifest_json = excluded.manifest_json, "
                "updated_at = CURRENT_TIMESTAMP",
                (pipeline_id, run_id, payload),
            )

    def snapshots(self, *, pipeline_id: str | None = None) -> tuple[MetadataSnapshot, ...]:
        query = "SELECT pipeline_id, run_id, manifest_json, updated_at FROM metadata_snapshots"
        parameters: tuple[object, ...] = ()
        if pipeline_id is not None:
            query += " WHERE pipeline_id = ?"
            parameters = (pipeline_id,)
        query += " ORDER BY pipeline_id"
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_snapshot_from_sequence(row) for row in rows)


def _snapshot_from_values(row: _Row) -> MetadataSnapshot:
    return _make_snapshot(
        pipeline_id=row["pipeline_id"],
        run_id=row["run_id"],
        manifest_json=row["manifest_json"],
        updated_at=row["updated_at"],
    )


def _snapshot_from_sequence(row: tuple[object, ...]) -> MetadataSnapshot:
    return _make_snapshot(
        pipeline_id=row[0],
        run_id=row[1],
        manifest_json=row[2],
        updated_at=row[3],
    )


def _make_snapshot(
    *,
    pipeline_id: object,
    run_id: object,
    manifest_json: object,
    updated_at: object,
) -> MetadataSnapshot:
    try:
        decoded = json.loads(str(manifest_json))
    except (TypeError, ValueError) as error:
        raise RuntimeError("Stored Dander metadata is invalid") from error
    if not isinstance(decoded, dict):
        raise RuntimeError("Stored Dander metadata is invalid")
    return MetadataSnapshot(
        pipeline_id=str(pipeline_id),
        run_id=str(run_id),
        manifest=cast("dict[str, object]", decoded),
        updated_at=str(updated_at),
    )
