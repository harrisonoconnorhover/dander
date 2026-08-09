"""Exclusive pipeline leases, heartbeats, and monotonically increasing fencing tokens."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event, Lock, Thread
from time import time
from typing import TYPE_CHECKING, Any, Protocol, cast

from google.cloud import bigquery

from dander._bigquery_retry import run_mutation_with_retry
from dander.concurrency import FencingToken
from dander.identity import google_client_options

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)


class LeaseLostError(RuntimeError):
    """Raised when a run can no longer prove ownership of its pipeline lease."""


@dataclass(frozen=True)
class LeaseHandle:
    """One successfully acquired pipeline lease."""

    pipeline_id: str
    run_id: str
    fencing_token: int
    lease_seconds: int
    fence: FencingToken | None = None


class LeaseStore(ABC):
    """Acquire and maintain exclusive run ownership per pipeline."""

    @property
    @abstractmethod
    def lease_seconds(self) -> int:
        """Return the lease duration used by acquisition and heartbeats."""

    @abstractmethod
    def acquire(self, pipeline_id: str, run_id: str) -> LeaseHandle | None:
        """Acquire an expired or absent lease, or return ``None`` for an active owner."""

    @abstractmethod
    def heartbeat(self, lease: LeaseHandle) -> bool:
        """Extend a matching unexpired lease and report whether ownership remains current."""

    @abstractmethod
    def release(self, lease: LeaseHandle) -> bool:
        """Release only the exact run and fencing token represented by ``lease``."""


class _QueryJob(Protocol):
    num_dml_affected_rows: int | None

    def result(self) -> Iterable[Mapping[str, Any]]:
        """Wait for completion and return rows when projected."""


class _BigQueryClient(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _QueryJob:
        """Run one BigQuery statement or script."""


class BigQueryLeaseStore(LeaseStore):
    """Coordinate hosted pipeline ownership through a BigQuery control table."""

    def __init__(
        self,
        *,
        project: str,
        dataset: str,
        client: _BigQueryClient | None = None,
        lease_seconds: int = 120,
        authority_id: str | None = None,
        authority_epoch: int = 1,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", project):
            raise ValueError(f"Invalid BigQuery project: {project!r}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dataset):
            raise ValueError(f"Invalid BigQuery dataset: {dataset!r}")
        self._lease_seconds = _validated_lease_seconds(lease_seconds)
        self._table_prefix = f"{project}.{dataset}._dander_lease_"
        self._authority_id = authority_id or f"bigquery:{project}:{dataset}"
        self._authority_epoch = authority_epoch
        self._client = client or cast(
            "_BigQueryClient",
            bigquery.Client(project=project, **google_client_options()),
        )
        self._ready: set[str] = set()

    @property
    def lease_seconds(self) -> int:
        return self._lease_seconds

    def acquire(self, pipeline_id: str, run_id: str) -> LeaseHandle | None:
        """Conditionally claim one row and increment its fencing token."""
        table = self._table_for_pipeline(pipeline_id)
        self._ensure_table(table)
        config = _lease_config(pipeline_id, run_id)
        run_mutation_with_retry(
            lambda: self._client.query(
                f"MERGE `{table}` AS target "
                "USING (SELECT @pipeline_id AS pipeline_id) AS incoming "
                "ON target.pipeline_id = incoming.pipeline_id "
                "WHEN NOT MATCHED THEN INSERT "
                "(pipeline_id, run_id, fencing_token, heartbeat_at, lease_expires_at) "
                "VALUES (incoming.pipeline_id, NULL, 0, NULL, "
                "TIMESTAMP('1970-01-01T00:00:00Z'))",
                job_config=config,
            )
        )
        claim = run_mutation_with_retry(
            lambda: self._client.query(
                f"UPDATE `{table}` SET run_id = @run_id, "
                "fencing_token = fencing_token + 1, heartbeat_at = CURRENT_TIMESTAMP(), "
                "lease_expires_at = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), "
                f"INTERVAL {self._lease_seconds} SECOND) "
                "WHERE pipeline_id = @pipeline_id "
                "AND lease_expires_at <= CURRENT_TIMESTAMP()",
                job_config=config,
            )
        )
        if claim.num_dml_affected_rows != 1:
            return None
        rows = list(
            self._client.query(
                f"SELECT fencing_token FROM `{table}` "
                "WHERE pipeline_id = @pipeline_id AND run_id = @run_id "
                "AND lease_expires_at > CURRENT_TIMESTAMP()",
                job_config=config,
            ).result()
        )
        if len(rows) != 1:
            return None
        token = rows[0]["fencing_token"]
        if isinstance(token, bool) or not isinstance(token, int) or token <= 0:
            return None
        return LeaseHandle(
            pipeline_id=pipeline_id,
            run_id=run_id,
            fencing_token=token,
            lease_seconds=self._lease_seconds,
            fence=FencingToken(
                lease_table=table,
                pipeline_id=pipeline_id,
                run_id=run_id,
                token=token,
                authority_id=self._authority_id,
                authority_epoch=self._authority_epoch,
            ),
        )

    def heartbeat(self, lease: LeaseHandle) -> bool:
        table = self._table_for_pipeline(lease.pipeline_id)
        self._ensure_table(table)
        job = run_mutation_with_retry(
            lambda: self._client.query(
                f"UPDATE `{table}` SET heartbeat_at = CURRENT_TIMESTAMP(), "
                "lease_expires_at = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), "
                f"INTERVAL {self._lease_seconds} SECOND) "
                "WHERE pipeline_id = @pipeline_id AND run_id = @run_id "
                "AND fencing_token = @fencing_token "
                "AND lease_expires_at > CURRENT_TIMESTAMP()",
                job_config=_lease_config(
                    lease.pipeline_id,
                    lease.run_id,
                    fencing_token=lease.fencing_token,
                ),
            )
        )
        return job.num_dml_affected_rows == 1

    def release(self, lease: LeaseHandle) -> bool:
        table = self._table_for_pipeline(lease.pipeline_id)
        self._ensure_table(table)
        job = run_mutation_with_retry(
            lambda: self._client.query(
                f"UPDATE `{table}` SET run_id = NULL, "
                "heartbeat_at = CURRENT_TIMESTAMP(), lease_expires_at = CURRENT_TIMESTAMP() "
                "WHERE pipeline_id = @pipeline_id AND run_id = @run_id "
                "AND fencing_token = @fencing_token",
                job_config=_lease_config(
                    lease.pipeline_id,
                    lease.run_id,
                    fencing_token=lease.fencing_token,
                ),
            )
        )
        return job.num_dml_affected_rows == 1

    def _table_for_pipeline(self, pipeline_id: str) -> str:
        digest = hashlib.sha256(pipeline_id.encode("utf-8")).hexdigest()[:16]
        return f"{self._table_prefix}{digest}"

    def _ensure_table(self, table: str) -> None:
        if table in self._ready:
            return
        self._client.query(
            f"CREATE TABLE IF NOT EXISTS `{table}` ("
            "pipeline_id STRING NOT NULL, run_id STRING, fencing_token INT64 NOT NULL, "
            "heartbeat_at TIMESTAMP, lease_expires_at TIMESTAMP NOT NULL) "
            "CLUSTER BY pipeline_id"
        ).result()
        self._ready.add(table)


class SqliteLeaseStore(LeaseStore):
    """Coordinate local and sandbox runs through SQLite's immediate transaction lock."""

    def __init__(self, path: Path, *, lease_seconds: int = 120) -> None:
        self._path = path
        self._lease_seconds = _validated_lease_seconds(lease_seconds)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS pipeline_leases ("
                "pipeline_id TEXT PRIMARY KEY, run_id TEXT, fencing_token INTEGER NOT NULL, "
                "heartbeat_at REAL, lease_expires_at REAL NOT NULL)"
            )

    @property
    def lease_seconds(self) -> int:
        return self._lease_seconds

    def acquire(self, pipeline_id: str, run_id: str) -> LeaseHandle | None:
        now = time()
        with sqlite3.connect(self._path, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT run_id, fencing_token, lease_expires_at FROM pipeline_leases "
                "WHERE pipeline_id = ?",
                (pipeline_id,),
            ).fetchone()
            if row is None:
                token = 1
                connection.execute(
                    "INSERT INTO pipeline_leases "
                    "(pipeline_id, run_id, fencing_token, heartbeat_at, lease_expires_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (pipeline_id, run_id, token, now, now + self._lease_seconds),
                )
            elif float(row[2]) <= now:
                token = int(cast("int", row[1])) + 1
                connection.execute(
                    "UPDATE pipeline_leases SET run_id = ?, fencing_token = ?, "
                    "heartbeat_at = ?, lease_expires_at = ? WHERE pipeline_id = ?",
                    (run_id, token, now, now + self._lease_seconds, pipeline_id),
                )
            else:
                connection.rollback()
                return None
            connection.commit()
        return LeaseHandle(
            pipeline_id=pipeline_id,
            run_id=run_id,
            fencing_token=token,
            lease_seconds=self._lease_seconds,
        )

    def heartbeat(self, lease: LeaseHandle) -> bool:
        now = time()
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                "UPDATE pipeline_leases SET heartbeat_at = ?, lease_expires_at = ? "
                "WHERE pipeline_id = ? AND run_id = ? AND fencing_token = ? "
                "AND lease_expires_at > ?",
                (
                    now,
                    now + self._lease_seconds,
                    lease.pipeline_id,
                    lease.run_id,
                    lease.fencing_token,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def release(self, lease: LeaseHandle) -> bool:
        now = time()
        with sqlite3.connect(self._path) as connection:
            cursor = connection.execute(
                "UPDATE pipeline_leases SET run_id = NULL, heartbeat_at = ?, "
                "lease_expires_at = ? WHERE pipeline_id = ? AND run_id = ? "
                "AND fencing_token = ?",
                (
                    now,
                    now,
                    lease.pipeline_id,
                    lease.run_id,
                    lease.fencing_token,
                ),
            )
        return cursor.rowcount == 1


class LeaseHeartbeat:
    """Maintain a lease in the background and fail closed after any ownership loss."""

    def __init__(
        self,
        store: LeaseStore,
        lease: LeaseHandle,
        *,
        background: bool = True,
    ) -> None:
        self._store = store
        self._lease = lease
        self._interval = max(1.0, store.lease_seconds / 3)
        self._background = background
        self._stop = Event()
        self._lock = Lock()
        self._lost = False
        self._thread: Thread | None = None

    @property
    def fence(self) -> FencingToken | None:
        return self._lease.fence

    def __enter__(self) -> LeaseHeartbeat:
        if self._background:
            self._thread = Thread(
                target=self._heartbeat_loop,
                name=f"dander-lease-{self._lease.pipeline_id}",
                daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self._interval + 1, 5.0))
        try:
            self._store.release(self._lease)
        except Exception:
            _LOGGER.exception(
                "pipeline_lease_release_failed",
                extra={
                    "dander_event": "pipeline_lease_release_failed",
                    "pipeline_id": self._lease.pipeline_id,
                    "run_id": self._lease.run_id,
                },
            )

    def verify(self) -> None:
        """Synchronously renew ownership immediately before a finalization boundary."""
        with self._lock:
            if self._lost:
                raise LeaseLostError("Pipeline lease ownership was lost")
            try:
                owned = self._store.heartbeat(self._lease)
            except Exception:
                self._lost = True
                raise LeaseLostError("Pipeline lease heartbeat failed") from None
            if not owned:
                self._lost = True
                raise LeaseLostError("Pipeline lease ownership was lost")

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                if self._lost:
                    return
                try:
                    if not self._store.heartbeat(self._lease):
                        self._lost = True
                        return
                except Exception:
                    self._lost = True
                    _LOGGER.exception(
                        "pipeline_lease_heartbeat_failed",
                        extra={
                            "dander_event": "pipeline_lease_heartbeat_failed",
                            "pipeline_id": self._lease.pipeline_id,
                            "run_id": self._lease.run_id,
                        },
                    )
                    return


def _validated_lease_seconds(value: int) -> int:
    if isinstance(value, bool) or not 30 <= value <= 3600:
        raise ValueError("lease_seconds must be an integer from 30 to 3600")
    return value


def _lease_config(
    pipeline_id: str,
    run_id: str,
    *,
    fencing_token: int | None = None,
) -> bigquery.QueryJobConfig:
    parameters = [
        bigquery.ScalarQueryParameter("pipeline_id", "STRING", pipeline_id),
        bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
    ]
    if fencing_token is not None:
        parameters.append(bigquery.ScalarQueryParameter("fencing_token", "INT64", fencing_token))
    return bigquery.QueryJobConfig(query_parameters=parameters)
