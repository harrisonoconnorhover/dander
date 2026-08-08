"""PostgreSQL durable-state runtime and versioned schema migrations."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.catalog import MetadataSnapshot, MetadataStore
from dander.providers.postgresql.config import PostgreSQLStateConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.state import (
    LeaseHandle,
    LeaseStore,
    RunHistoryStore,
    RunRecord,
    RunStage,
    RunStatus,
    StateCapabilities,
    StateMigration,
    StateRuntime,
    WatermarkStore,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

    from dander.concurrency import FencingToken

_STATE_SCHEMA_VERSION = 1
_MIGRATIONS = (StateMigration(version=1, name="initial_state_schema"),)
_INTERRUPTED_SUMMARY = "A later execution acquired the expired pipeline lease."

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


class PostgreSQLStateDatabase:
    """Shared bounded connection pool and safe state-schema identifiers."""

    def __init__(self, *, pool: PostgreSQLPool, schema_name: str) -> None:
        self.pool = pool
        self.schema_name = schema_name

    def relation(self, name: str) -> sql.Identifier:
        return sql.Identifier(self.schema_name, name)


class PostgreSQLStateMigrator:
    """Apply the PostgreSQL state schema atomically and reject newer ledgers."""

    def __init__(
        self,
        database: PostgreSQLStateDatabase,
        *,
        terminal_history_retention_days: int,
    ) -> None:
        self._database = database
        self._retention_days = terminal_history_retention_days

    @property
    def migrations(self) -> tuple[StateMigration, ...]:
        return _MIGRATIONS

    def current_version(self) -> int:
        with self._database.pool.connection() as connection, connection.transaction():
            self._ensure_ledger(connection)
            version = self._read_version(connection)
        return version

    def migrate(self) -> int:
        with self._database.pool.connection() as connection, connection.transaction():
            self._ensure_ledger(connection)
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"dander-state:{self._database.schema_name}",),
            )
            version = self._read_version(connection)
            if version < _STATE_SCHEMA_VERSION:
                self._apply_initial_schema(connection)
                connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (version, name, applied_at) "
                        "VALUES (%s, %s, clock_timestamp()) ON CONFLICT (version) DO NOTHING"
                    ).format(self._database.relation("dander_schema_migrations")),
                    (_MIGRATIONS[0].version, _MIGRATIONS[0].name),
                )
            self._prune_terminal_history(connection)
        return _STATE_SCHEMA_VERSION

    def _ensure_ledger(self, connection: Connection[PostgreSQLRow]) -> None:
        connection.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(self._database.schema_name)
            )
        )
        connection.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} ("
                "version INTEGER PRIMARY KEY CHECK (version > 0), "
                "name TEXT NOT NULL UNIQUE, applied_at TIMESTAMPTZ NOT NULL)"
            ).format(self._database.relation("dander_schema_migrations"))
        )

    def _read_version(self, connection: Connection[PostgreSQLRow]) -> int:
        row = connection.execute(
            sql.SQL("SELECT COALESCE(MAX(version), 0) AS version FROM {}").format(
                self._database.relation("dander_schema_migrations")
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL state migration ledger returned no result")
        value = row["version"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError("PostgreSQL state migration ledger contains an invalid version")
        if value > _STATE_SCHEMA_VERSION:
            raise RuntimeError(
                f"PostgreSQL state schema version {value} is newer than this Dander runtime"
            )
        return value

    def _apply_initial_schema(self, connection: Connection[PostgreSQLRow]) -> None:
        relation = self._database.relation
        statements = (
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} ("
                "pipeline_id TEXT PRIMARY KEY, run_id TEXT, "
                "fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0), "
                "heartbeat_at TIMESTAMPTZ, "
                "lease_expires_at TIMESTAMPTZ NOT NULL DEFAULT to_timestamp(0))"
            ).format(relation("dander_pipeline_leases")),
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} ("
                "source_name TEXT NOT NULL, entity_name TEXT NOT NULL, "
                "last_cursor TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL, "
                "PRIMARY KEY (source_name, entity_name))"
            ).format(relation("dander_watermarks")),
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} ("
                "run_id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL, source_name TEXT NOT NULL, "
                "status TEXT NOT NULL, stage TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL, "
                "finished_at TIMESTAMPTZ, endpoints BIGINT NOT NULL DEFAULT 0, "
                "extracted BIGINT NOT NULL DEFAULT 0, affected BIGINT NOT NULL DEFAULT 0, "
                "models BIGINT NOT NULL DEFAULT 0, assertions BIGINT NOT NULL DEFAULT 0, "
                "assets BIGINT NOT NULL DEFAULT 0, failure_stage TEXT, failure_code TEXT, "
                "failure_summary VARCHAR(512))"
            ).format(relation("dander_runs")),
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {} ON {} (pipeline_id, status, started_at DESC)"
            ).format(
                sql.Identifier("dander_runs_pipeline_status_started_idx"),
                relation("dander_runs"),
            ),
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} ("
                "pipeline_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, "
                "manifest_json JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL)"
            ).format(relation("dander_metadata_snapshots")),
        )
        for statement in statements:
            connection.execute(statement)

    def _prune_terminal_history(self, connection: Connection[PostgreSQLRow]) -> None:
        connection.execute(
            sql.SQL(
                "DELETE FROM {} WHERE status IN ('succeeded', 'failed', 'skipped') "
                "AND failure_code IS DISTINCT FROM 'interrupted_run' "
                "AND finished_at < clock_timestamp() - (%s * INTERVAL '1 day')"
            ).format(self._database.relation("dander_runs")),
            (self._retention_days,),
        )


class PostgreSQLLeaseStore(LeaseStore):
    """Coordinate pipeline ownership using PostgreSQL server time and row locks."""

    def __init__(self, database: PostgreSQLStateDatabase, *, lease_seconds: int) -> None:
        self._database = database
        self._lease_seconds = lease_seconds

    @property
    def lease_seconds(self) -> int:
        return self._lease_seconds

    def acquire(self, pipeline_id: str, run_id: str) -> LeaseHandle | None:
        table = self._database.relation("dander_pipeline_leases")
        with self._database.pool.connection() as connection, connection.transaction():
            connection.execute(
                sql.SQL(
                    "INSERT INTO {} (pipeline_id, run_id, fencing_token, "
                    "heartbeat_at, lease_expires_at) "
                    "VALUES (%s, NULL, 0, NULL, to_timestamp(0)) "
                    "ON CONFLICT (pipeline_id) DO NOTHING"
                ).format(table),
                (pipeline_id,),
            )
            row = connection.execute(
                sql.SQL(
                    "UPDATE {} SET run_id = %s, fencing_token = fencing_token + 1, "
                    "heartbeat_at = clock_timestamp(), "
                    "lease_expires_at = clock_timestamp() + (%s * INTERVAL '1 second') "
                    "WHERE pipeline_id = %s AND lease_expires_at <= clock_timestamp() "
                    "RETURNING fencing_token"
                ).format(table),
                (run_id, self._lease_seconds, pipeline_id),
            ).fetchone()
        if row is None:
            return None
        token = row["fencing_token"]
        if isinstance(token, bool) or not isinstance(token, int) or token <= 0:
            raise RuntimeError("PostgreSQL lease returned an invalid fencing token")
        return LeaseHandle(
            pipeline_id=pipeline_id,
            run_id=run_id,
            fencing_token=token,
            lease_seconds=self._lease_seconds,
        )

    def heartbeat(self, lease: LeaseHandle) -> bool:
        with self._database.pool.connection() as connection:
            cursor = connection.execute(
                sql.SQL(
                    "UPDATE {} SET heartbeat_at = clock_timestamp(), "
                    "lease_expires_at = clock_timestamp() + (%s * INTERVAL '1 second') "
                    "WHERE pipeline_id = %s AND run_id = %s AND fencing_token = %s "
                    "AND lease_expires_at > clock_timestamp()"
                ).format(self._database.relation("dander_pipeline_leases")),
                (
                    self._lease_seconds,
                    lease.pipeline_id,
                    lease.run_id,
                    lease.fencing_token,
                ),
            )
        return cursor.rowcount == 1

    def release(self, lease: LeaseHandle) -> bool:
        with self._database.pool.connection() as connection:
            cursor = connection.execute(
                sql.SQL(
                    "UPDATE {} SET run_id = NULL, heartbeat_at = clock_timestamp(), "
                    "lease_expires_at = clock_timestamp() "
                    "WHERE pipeline_id = %s AND run_id = %s AND fencing_token = %s"
                ).format(self._database.relation("dander_pipeline_leases")),
                (lease.pipeline_id, lease.run_id, lease.fencing_token),
            )
        return cursor.rowcount == 1


class PostgreSQLWatermarkStore(WatermarkStore):
    """Persist cursors with atomic expected-boundary comparison."""

    def __init__(self, database: PostgreSQLStateDatabase) -> None:
        self._database = database

    def get(self, source: str, entity: str) -> str | None:
        with self._database.pool.connection() as connection:
            row = connection.execute(
                sql.SQL(
                    "SELECT last_cursor FROM {} WHERE source_name = %s AND entity_name = %s"
                ).format(self._database.relation("dander_watermarks")),
                (source, entity),
            ).fetchone()
        return None if row is None else str(row["last_cursor"])

    def set(self, source: str, entity: str, cursor: str) -> None:
        with self._database.pool.connection() as connection:
            connection.execute(
                sql.SQL(
                    "INSERT INTO {} (source_name, entity_name, last_cursor, updated_at) "
                    "VALUES (%s, %s, %s, clock_timestamp()) "
                    "ON CONFLICT (source_name, entity_name) DO UPDATE SET "
                    "last_cursor = EXCLUDED.last_cursor, updated_at = clock_timestamp()"
                ).format(self._database.relation("dander_watermarks")),
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
        del fence  # Destination fencing is provider-neutral and handled at publication.
        table = self._database.relation("dander_watermarks")
        with self._database.pool.connection() as connection, connection.transaction():
            if expected is None:
                result = connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (source_name, entity_name, last_cursor, updated_at) "
                        "VALUES (%s, %s, %s, clock_timestamp()) "
                        "ON CONFLICT (source_name, entity_name) DO NOTHING"
                    ).format(table),
                    (source, entity, cursor),
                )
            else:
                result = connection.execute(
                    sql.SQL(
                        "UPDATE {} SET last_cursor = %s, updated_at = clock_timestamp() "
                        "WHERE source_name = %s AND entity_name = %s "
                        "AND last_cursor IS NOT DISTINCT FROM %s"
                    ).format(table),
                    (cursor, source, entity, expected),
                )
        return result.rowcount == 1


class PostgreSQLRunHistoryStore(RunHistoryStore):
    """Persist sanitized pipeline lifecycle summaries in PostgreSQL."""

    def __init__(self, database: PostgreSQLStateDatabase) -> None:
        self._database = database

    def start(self, run_id: str, source: str, *, pipeline_id: str | None = None) -> None:
        with self._database.pool.connection() as connection:
            connection.execute(
                sql.SQL(
                    "INSERT INTO {} (run_id, pipeline_id, source_name, status, stage, started_at) "
                    "VALUES (%s, %s, %s, 'running', 'ingest', clock_timestamp())"
                ).format(self._database.relation("dander_runs")),
                (run_id, pipeline_id or source, source),
            )

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
                else failure_stage or RunStage.INGEST
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
        stage: RunStage,
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
        terminal = status is not None
        with self._database.pool.connection() as connection:
            connection.execute(
                sql.SQL(
                    "UPDATE {} SET status = %s, stage = %s, "
                    "finished_at = CASE WHEN %s THEN clock_timestamp() ELSE finished_at END, "
                    "endpoints = %s, extracted = %s, affected = %s, models = %s, "
                    "assertions = %s, assets = %s, failure_stage = %s, "
                    "failure_code = %s, failure_summary = %s WHERE run_id = %s"
                ).format(self._database.relation("dander_runs")),
                (
                    status.value if status is not None else RunStatus.RUNNING.value,
                    stage.value,
                    terminal,
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
        where = sql.SQL("")
        parameters: list[object] = []
        if pipeline_id is not None:
            where = sql.SQL(" WHERE pipeline_id = %s")
            parameters.append(pipeline_id)
        parameters.append(limit)
        with self._database.pool.connection() as connection:
            rows = connection.execute(
                sql.SQL(
                    "SELECT run_id, pipeline_id, source_name, status, stage, started_at, "
                    "finished_at, endpoints, extracted, affected, models, assertions, assets, "
                    "failure_stage, failure_code, failure_summary FROM {}{} "
                    "ORDER BY started_at DESC LIMIT %s"
                ).format(self._database.relation("dander_runs"), where),
                parameters,
            ).fetchall()
        return tuple(_run_record(row) for row in rows)

    def reconcile_interrupted(self, pipeline_id: str, *, current_run_id: str) -> None:
        with self._database.pool.connection() as connection:
            connection.execute(
                sql.SQL(
                    "UPDATE {} SET status = 'failed', finished_at = clock_timestamp(), "
                    "failure_stage = stage, failure_code = 'interrupted_run', "
                    "failure_summary = %s WHERE status = 'running' AND pipeline_id = %s "
                    "AND run_id != %s"
                ).format(self._database.relation("dander_runs")),
                (_INTERRUPTED_SUMMARY, pipeline_id, current_run_id),
            )


class PostgreSQLMetadataStore(MetadataStore):
    """Store one deterministic JSONB metadata snapshot per pipeline."""

    def __init__(self, database: PostgreSQLStateDatabase) -> None:
        self._database = database

    def publish(
        self,
        *,
        pipeline_id: str,
        run_id: str,
        manifest: dict[str, object],
    ) -> None:
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        with self._database.pool.connection() as connection:
            connection.execute(
                sql.SQL(
                    "INSERT INTO {} (pipeline_id, run_id, manifest_json, updated_at) "
                    "VALUES (%s, %s, %s::jsonb, clock_timestamp()) "
                    "ON CONFLICT (pipeline_id) DO UPDATE SET run_id = EXCLUDED.run_id, "
                    "manifest_json = EXCLUDED.manifest_json, updated_at = clock_timestamp()"
                ).format(self._database.relation("dander_metadata_snapshots")),
                (pipeline_id, run_id, payload),
            )

    def snapshots(self, *, pipeline_id: str | None = None) -> tuple[MetadataSnapshot, ...]:
        where = sql.SQL("")
        parameters: list[object] = []
        if pipeline_id is not None:
            where = sql.SQL(" WHERE pipeline_id = %s")
            parameters.append(pipeline_id)
        with self._database.pool.connection() as connection:
            rows = connection.execute(
                sql.SQL(
                    "SELECT pipeline_id, run_id, manifest_json, updated_at FROM {}{} "
                    "ORDER BY pipeline_id"
                ).format(self._database.relation("dander_metadata_snapshots"), where),
                parameters,
            ).fetchall()
        return tuple(_metadata_snapshot(row) for row in rows)


def _run_record(row: PostgreSQLRow) -> RunRecord:
    failure_stage = row["failure_stage"]
    return RunRecord(
        run_id=str(row["run_id"]),
        pipeline_id=str(row["pipeline_id"]),
        source=str(row["source_name"]),
        status=RunStatus(str(row["status"])),
        stage=RunStage(str(row["stage"])),
        started_at=_timestamp(row["started_at"]),
        finished_at=_optional_timestamp(row["finished_at"]),
        endpoints=int(row["endpoints"]),
        extracted=int(row["extracted"]),
        affected=int(row["affected"]),
        models=int(row["models"]),
        assertions=int(row["assertions"]),
        assets=int(row["assets"]),
        failure_stage=RunStage(str(failure_stage)) if failure_stage is not None else None,
        failure_code=str(row["failure_code"]) if row["failure_code"] is not None else None,
        failure_summary=(
            str(row["failure_summary"]) if row["failure_summary"] is not None else None
        ),
    )


def _metadata_snapshot(row: PostgreSQLRow) -> MetadataSnapshot:
    manifest = row["manifest_json"]
    if not isinstance(manifest, dict):
        raise RuntimeError("Stored Dander metadata is invalid")
    return MetadataSnapshot(
        pipeline_id=str(row["pipeline_id"]),
        run_id=str(row["run_id"]),
        manifest=cast("dict[str, object]", manifest),
        updated_at=_timestamp(row["updated_at"]),
    )


def _timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _optional_timestamp(value: object) -> str | None:
    return None if value is None else _timestamp(value)


def _build_postgresql_state(
    config: BaseModel,
    context: Mapping[str, object],
) -> StateRuntime:
    if not isinstance(config, PostgreSQLStateConfig):
        raise TypeError("PostgreSQL state factory received the wrong configuration")
    supplied_pool = context.get("pool")
    if supplied_pool is None:
        dsn = os.environ.get(config.dsn_env)
        if not dsn:
            raise ValueError(f"PostgreSQL state requires a connection string in {config.dsn_env}")
        pool = cast(
            "PostgreSQLPool",
            ConnectionPool(
                conninfo=dsn,
                min_size=config.pool_min_size,
                max_size=config.pool_max_size,
                timeout=config.pool_timeout_seconds,
                kwargs={"row_factory": dict_row},
                open=True,
            ),
        )
        pool.wait(timeout=config.pool_timeout_seconds)
    elif isinstance(supplied_pool, ConnectionPool):
        pool = supplied_pool
    else:
        raise TypeError("PostgreSQL state context pool must be a psycopg ConnectionPool")

    database = PostgreSQLStateDatabase(pool=pool, schema_name=config.schema_name)
    metadata_enabled = context.get("metadata_enabled", True)
    if not isinstance(metadata_enabled, bool):
        raise ValueError("PostgreSQL state runtime requires a boolean metadata_enabled")
    migrator = PostgreSQLStateMigrator(
        database,
        terminal_history_retention_days=config.terminal_history_retention_days,
    )
    return StateRuntime(
        provider_id="postgresql",
        leases=PostgreSQLLeaseStore(database, lease_seconds=config.lease_seconds),
        watermarks=PostgreSQLWatermarkStore(database),
        history=PostgreSQLRunHistoryStore(database),
        metadata=PostgreSQLMetadataStore(database) if metadata_enabled else None,
        migrator=migrator,
        capabilities=StateCapabilities(
            provider_id="postgresql",
            schema_version=_STATE_SCHEMA_VERSION,
            server_time=True,
            atomic_leases=True,
            monotonic_fencing=True,
            atomic_watermark_cas=True,
            interrupted_run_reconciliation=True,
        ),
    )


POSTGRESQL_STATE_FACTORY = ProviderFactory[StateRuntime](
    kind=ProviderKind.STATE,
    provider_id="postgresql",
    api_version=PROVIDER_API_VERSION,
    build=_build_postgresql_state,
)

__all__ = [
    "POSTGRESQL_STATE_FACTORY",
    "PostgreSQLLeaseStore",
    "PostgreSQLMetadataStore",
    "PostgreSQLRunHistoryStore",
    "PostgreSQLStateDatabase",
    "PostgreSQLStateMigrator",
    "PostgreSQLWatermarkStore",
]
