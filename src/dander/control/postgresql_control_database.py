"""Versioned PostgreSQL schema owned by hosted Dander Control.

This module is an explicit PostgreSQL adapter boundary.  Provider-neutral Control contracts do
not import Psycopg; composition imports this module only after PostgreSQL has been selected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from psycopg import Connection, sql
from psycopg_pool import ConnectionPool

from dander.control.graph_store import MAX_GRAPH_DOCUMENT_BYTES
from dander.control.orchestration import run_needs_reconciliation

CONTROL_SCHEMA_VERSION = 4

_SCHEMA_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

PostgreSQLRow = dict[str, Any]
PostgreSQLControlPool = ConnectionPool[Connection[PostgreSQLRow]]


@dataclass(frozen=True, slots=True)
class ControlSchemaMigration:
    """One immutable Control-schema migration identity."""

    version: int
    name: str


CONTROL_SCHEMA_MIGRATIONS = (
    ControlSchemaMigration(version=1, name="graph_store_v1"),
    ControlSchemaMigration(version=2, name="run_store_v1"),
    ControlSchemaMigration(version=3, name="schedule_queue_v1"),
    ControlSchemaMigration(version=4, name="run_pending_recovery_v1"),
)


class PostgreSQLControlDatabase:
    """Shared PostgreSQL pool plus injection-safe Control schema identifiers."""

    def __init__(self, *, pool: PostgreSQLControlPool, schema_name: str) -> None:
        if not isinstance(schema_name, str) or _SCHEMA_NAME.fullmatch(schema_name) is None:
            raise ValueError(
                "The PostgreSQL Control schema name must be 1-63 lowercase ASCII letters, "
                "digits, or underscores and must start with a letter."
            )
        self.pool = pool
        self.schema_name = schema_name

    def relation(self, name: str) -> sql.Identifier:
        """Return one schema-qualified relation identifier."""
        return sql.Identifier(self.schema_name, name)


class PostgreSQLControlMigrator:
    """Create the dedicated Control schema atomically and reject schema drift."""

    def __init__(self, database: PostgreSQLControlDatabase) -> None:
        self._database = database

    @property
    def migrations(self) -> tuple[ControlSchemaMigration, ...]:
        return CONTROL_SCHEMA_MIGRATIONS

    def current_version(self) -> int:
        """Return the exact applied migration prefix, creating only the ledger if absent."""
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                self._acquire_migration_lock(connection)
                self._ensure_ledger(connection)
                return self._read_version(connection)
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError(
                "The PostgreSQL Control schema version could not be read."
            ) from error

    def migrate(self) -> int:
        """Apply all known migrations under a schema-scoped transaction advisory lock."""
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                self._acquire_migration_lock(connection)
                self._ensure_ledger(connection)
                version = self._read_version(connection)
                for migration in CONTROL_SCHEMA_MIGRATIONS[version:]:
                    self._apply(connection, migration)
                    connection.execute(
                        sql.SQL(
                            "INSERT INTO {} (version, name, applied_at) "
                            "VALUES (%s, %s, clock_timestamp())"
                        ).format(self._database.relation("dander_control_schema_migrations")),
                        (migration.version, migration.name),
                    )
                return self._read_version(connection)
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError("The PostgreSQL Control schema migration failed.") from error

    def _acquire_migration_lock(self, connection: Connection[PostgreSQLRow]) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"dander-control:{self._database.schema_name}",),
        )

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
            ).format(self._database.relation("dander_control_schema_migrations"))
        )

    def _read_version(self, connection: Connection[PostgreSQLRow]) -> int:
        rows = connection.execute(
            sql.SQL("SELECT version, name FROM {} ORDER BY version").format(
                self._database.relation("dander_control_schema_migrations")
            )
        ).fetchall()
        if len(rows) > CONTROL_SCHEMA_VERSION:
            raise RuntimeError("The PostgreSQL Control schema is newer than this Dander runtime.")
        for index, row in enumerate(rows):
            expected = CONTROL_SCHEMA_MIGRATIONS[index]
            if row.get("version") != expected.version or row.get("name") != expected.name:
                raise RuntimeError("The PostgreSQL Control migration ledger is inconsistent.")
        return len(rows)

    def _apply(
        self,
        connection: Connection[PostgreSQLRow],
        migration: ControlSchemaMigration,
    ) -> None:
        if migration.version == 1:
            self._apply_graph_store_v1(connection)
            return
        if migration.version == 2:
            self._apply_run_store_v1(connection)
            return
        if migration.version == 3:
            self._apply_schedule_queue_v1(connection)
            return
        if migration.version == 4:
            self._apply_run_pending_recovery_v1(connection)
            return
        raise RuntimeError("The PostgreSQL Control migration is unsupported.")

    def _apply_run_pending_recovery_v1(self, connection: Connection[PostgreSQLRow]) -> None:
        # Reuse the adapter's canonical snapshot and indexed-identity validation. The adapter
        # imports this module only for typing, so migration does not introduce an import cycle.
        from dander.control.postgresql_run_store import _stored_run_from_row

        relation = self._database.relation("dander_runs")
        connection.execute(
            sql.SQL(
                "ALTER TABLE {} ADD COLUMN needs_reconciliation BOOLEAN NOT NULL DEFAULT TRUE"
            ).format(relation)
        )
        after = ""
        while True:
            rows = connection.execute(
                sql.SQL(
                    "SELECT run_id, environment, project, idempotency_key_sha256, "
                    "submission_sha256, record_bytes, revision FROM {} "
                    "WHERE run_id > %s ORDER BY run_id LIMIT 100"
                ).format(relation),
                (after,),
            ).fetchall()
            if not rows:
                break
            stored_runs = tuple(_stored_run_from_row(row) for row in rows)
            settled_ids = [
                stored.record.run_id
                for stored in stored_runs
                if not run_needs_reconciliation(stored.record)
            ]
            if settled_ids:
                connection.execute(
                    sql.SQL(
                        "UPDATE {} SET needs_reconciliation = FALSE WHERE run_id = ANY(%s)"
                    ).format(relation),
                    (settled_ids,),
                )
            after = stored_runs[-1].record.run_id
        connection.execute(
            sql.SQL("CREATE INDEX {} ON {} (run_id) WHERE needs_reconciliation").format(
                sql.Identifier("dander_runs_pending_recovery"), relation
            )
        )

    def _apply_graph_store_v1(self, connection: Connection[PostgreSQLRow]) -> None:
        relation = self._database.relation
        statements = (
            sql.SQL(
                "CREATE TABLE {} ("
                "project VARCHAR(63) NOT NULL, graph VARCHAR(63) NOT NULL, "
                "document_bytes BYTEA NOT NULL, revision VARCHAR(512) NOT NULL, "
                "content_sha256 CHAR(64) NOT NULL, "
                "created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, "
                "PRIMARY KEY (project, graph), "
                "CHECK (octet_length(document_bytes) <= {}), "
                "CHECK (content_sha256 ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (created_at <= updated_at))"
            ).format(
                relation("dander_graphs"),
                sql.Literal(MAX_GRAPH_DOCUMENT_BYTES),
            ),
            sql.SQL(
                "CREATE TABLE {} ("
                "project VARCHAR(63) NOT NULL, operation TEXT NOT NULL, "
                "idempotency_key_sha256 CHAR(64) NOT NULL, "
                "request_fingerprint CHAR(64) NOT NULL, "
                "graph VARCHAR(63) NOT NULL, completed BOOLEAN NOT NULL DEFAULT FALSE, "
                "result_revision VARCHAR(512), result_content_sha256 CHAR(64), "
                "result_document_bytes BYTEA, result_created_at TIMESTAMPTZ, "
                "result_updated_at TIMESTAMPTZ, result_deleted_at TIMESTAMPTZ, "
                "PRIMARY KEY (project, operation, idempotency_key_sha256), "
                "CHECK (operation IN ('create', 'delete')), "
                "CHECK (idempotency_key_sha256 ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (request_fingerprint ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (result_content_sha256 IS NULL OR "
                "result_content_sha256 ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (result_document_bytes IS NULL OR "
                "octet_length(result_document_bytes) <= {}), "
                "CHECK ((NOT completed AND result_revision IS NULL "
                "AND result_content_sha256 IS NULL AND result_document_bytes IS NULL "
                "AND result_created_at IS NULL AND result_updated_at IS NULL "
                "AND result_deleted_at IS NULL) OR "
                "(completed AND operation = 'create' AND result_revision IS NOT NULL "
                "AND result_content_sha256 IS NOT NULL AND result_document_bytes IS NOT NULL "
                "AND result_created_at IS NOT NULL AND result_updated_at IS NOT NULL "
                "AND result_deleted_at IS NULL) OR "
                "(completed AND operation = 'delete' AND result_revision IS NOT NULL "
                "AND result_content_sha256 IS NOT NULL AND result_document_bytes IS NULL "
                "AND result_created_at IS NULL AND result_updated_at IS NULL "
                "AND result_deleted_at IS NOT NULL)))"
            ).format(
                relation("dander_graph_mutation_idempotency"),
                sql.Literal(MAX_GRAPH_DOCUMENT_BYTES),
            ),
        )
        for statement in statements:
            connection.execute(statement)

    def _apply_run_store_v1(self, connection: Connection[PostgreSQLRow]) -> None:
        relation = self._database.relation
        statements = (
            sql.SQL(
                "CREATE TABLE {} ("
                "run_id VARCHAR(128) PRIMARY KEY, environment VARCHAR(63) NOT NULL, "
                "project VARCHAR(63) NOT NULL, idempotency_key_sha256 CHAR(64) NOT NULL, "
                "submission_sha256 CHAR(64) NOT NULL, record_bytes BYTEA NOT NULL, "
                "revision CHAR(64) NOT NULL, "
                "UNIQUE (environment, project, idempotency_key_sha256), "
                "CHECK (idempotency_key_sha256 ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (submission_sha256 ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (revision ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (octet_length(record_bytes) <= 262144))"
            ).format(relation("dander_runs")),
            sql.SQL(
                "CREATE TABLE {} ("
                "run_id VARCHAR(128) NOT NULL, attempt_id VARCHAR(128) NOT NULL, "
                "attempt_number INTEGER NOT NULL, record_bytes BYTEA NOT NULL, "
                "PRIMARY KEY (run_id, attempt_id), UNIQUE (run_id, attempt_number), "
                "FOREIGN KEY (run_id) REFERENCES {} (run_id), "
                "CHECK (attempt_number > 0), "
                "CHECK (octet_length(record_bytes) <= 131072))"
            ).format(
                relation("dander_attempts"),
                relation("dander_runs"),
            ),
            sql.SQL(
                "CREATE TABLE {} ("
                "key_sha256 CHAR(64) PRIMARY KEY, operation VARCHAR(63) NOT NULL, "
                "run_id VARCHAR(128) NOT NULL, result_bytes BYTEA NOT NULL, "
                "CHECK (key_sha256 ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (octet_length(result_bytes) <= 16384))"
            ).format(relation("dander_run_mutation_idempotency")),
        )
        for statement in statements:
            connection.execute(statement)

    def _apply_schedule_queue_v1(self, connection: Connection[PostgreSQLRow]) -> None:
        relation = self._database.relation
        statements = (
            sql.SQL(
                "CREATE TABLE {} ("
                "trigger_id VARCHAR(63) PRIMARY KEY, spec_sha256 CHAR(64) NOT NULL, "
                "cursor_utc TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, "
                "CHECK (spec_sha256 ~ '^[0-9a-f]{{64}}$'))"
            ).format(relation("dander_schedule_cursors")),
            sql.SQL(
                "CREATE TABLE {} ("
                "message_id CHAR(64) PRIMARY KEY, trigger_id VARCHAR(63) NOT NULL, "
                "plan_revision CHAR(64) NOT NULL, scheduled_occurrence TIMESTAMPTZ NOT NULL, "
                "body BYTEA NOT NULL, available_at TIMESTAMPTZ NOT NULL, "
                "delivery_count INTEGER NOT NULL DEFAULT 0, "
                "receipt_handle VARCHAR(64), lease_until TIMESTAMPTZ, "
                "dead_lettered_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL, "
                "UNIQUE (trigger_id, plan_revision, scheduled_occurrence), "
                "CHECK (message_id ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (plan_revision ~ '^[0-9a-f]{{64}}$'), "
                "CHECK (octet_length(body) BETWEEN 1 AND 8192), "
                "CHECK (delivery_count >= 0), "
                "CHECK ((receipt_handle IS NULL) = (lease_until IS NULL)), "
                "CHECK (dead_lettered_at IS NULL OR "
                "(receipt_handle IS NULL AND lease_until IS NULL)))"
            ).format(relation("dander_schedule_queue")),
            sql.SQL(
                "CREATE INDEX {} ON {} (available_at, created_at, message_id) "
                "WHERE dead_lettered_at IS NULL"
            ).format(
                sql.Identifier("dander_schedule_queue_visible_idx"),
                relation("dander_schedule_queue"),
            ),
        )
        for statement in statements:
            connection.execute(statement)


__all__ = [
    "CONTROL_SCHEMA_MIGRATIONS",
    "CONTROL_SCHEMA_VERSION",
    "ControlSchemaMigration",
    "PostgreSQLControlDatabase",
    "PostgreSQLControlMigrator",
    "PostgreSQLControlPool",
    "PostgreSQLRow",
]
