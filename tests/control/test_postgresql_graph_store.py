"""PostgreSQL Control-schema and GraphStore conformance."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import TYPE_CHECKING, cast

import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.control.bundle import PACKAGED_BUNDLE_DIRECTORY
from dander.control.graph_store import (
    GraphStoreAlreadyExistsError,
    GraphStoreConflictError,
    GraphStoreCorruptionError,
    GraphStoreIdempotencyConflictError,
    GraphStoreNotFoundError,
    canonicalize_graph_document,
)
from dander.control.models import PipelineGraphDocument
from dander.control.postgresql_control_database import (
    CONTROL_SCHEMA_MIGRATIONS,
    CONTROL_SCHEMA_VERSION,
    PostgreSQLControlDatabase,
    PostgreSQLControlMigrator,
    PostgreSQLControlPool,
)
from dander.control.postgresql_graph_store import PostgreSQLGraphStore, _record_from_row
from dander.pipeline.graph import graph_to_payload

if TYPE_CHECKING:
    from collections.abc import Iterator


def _document() -> PipelineGraphDocument:
    payload = json.loads(
        (PACKAGED_BUNDLE_DIRECTORY / "fixtures/pipeline-graph.json").read_text(encoding="utf-8")
    )
    return PipelineGraphDocument.model_validate(payload)


def _changed(document: PipelineGraphDocument, name: str) -> PipelineGraphDocument:
    payload = graph_to_payload(document.to_domain())
    payload["name"] = name
    return PipelineGraphDocument.model_validate(payload)


@pytest.fixture
def postgresql_control() -> Iterator[tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str]]:
    dsn = os.environ.get("DANDER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("DANDER_TEST_POSTGRES_DSN is not configured")
    schema_name = f"dander_control_{uuid.uuid4().hex}"
    pool = cast(
        "PostgreSQLControlPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=5,
            timeout=2,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=5)
    database = PostgreSQLControlDatabase(pool=pool, schema_name=schema_name)
    try:
        yield database, pool, schema_name
    finally:
        with pool.connection() as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
            )
        pool.close()


def test_postgresql_control_database_rejects_unsafe_schema_names() -> None:
    pool = cast("PostgreSQLControlPool", object())

    with pytest.raises(ValueError, match="schema name"):
        PostgreSQLControlDatabase(pool=pool, schema_name="public; DROP SCHEMA public")
    with pytest.raises(ValueError, match="schema name"):
        PostgreSQLControlDatabase(pool=pool, schema_name="Dander_Control")


def test_postgresql_record_decoder_requires_exact_canonical_bytes() -> None:
    canonical = canonicalize_graph_document(_document())
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    row = {
        "project": "default",
        "graph": "example",
        "document_bytes": canonical.data,
        "revision": "opaque-r1",
        "content_sha256": canonical.content_sha256,
        "created_at": now,
        "updated_at": now,
    }

    decoded = _record_from_row(
        row,
        max_graph_bytes=len(canonical.data) + 1,
        expected_key=("default", "example"),
    )

    assert canonicalize_graph_document(decoded.document).data == canonical.data
    with pytest.raises(GraphStoreCorruptionError, match="canonical content digest"):
        _record_from_row(
            {**row, "document_bytes": canonical.data + b" "},
            max_graph_bytes=len(canonical.data) + 1,
        )
    with pytest.raises(GraphStoreCorruptionError, match="content digest"):
        _record_from_row(
            {**row, "content_sha256": "0" * 64},
            max_graph_bytes=len(canonical.data) + 1,
        )
    with pytest.raises(GraphStoreCorruptionError, match="timestamp"):
        _record_from_row(
            {**row, "updated_at": datetime(2026, 8, 30, 12)},
            max_graph_bytes=len(canonical.data) + 1,
        )


def test_postgresql_control_migration_is_versioned_and_idempotent(
    postgresql_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, schema_name = postgresql_control
    migrator = PostgreSQLControlMigrator(database)

    assert CONTROL_SCHEMA_VERSION == 3
    assert migrator.migrations == CONTROL_SCHEMA_MIGRATIONS
    assert migrator.current_version() == 0
    assert migrator.migrate() == 3
    assert migrator.migrate() == 3
    assert migrator.current_version() == 3
    with pool.connection() as connection:
        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
            "ORDER BY table_name",
            (schema_name,),
        ).fetchall()
    assert [row["table_name"] for row in tables] == [
        "dander_attempts",
        "dander_control_schema_migrations",
        "dander_graph_mutation_idempotency",
        "dander_graphs",
        "dander_run_mutation_idempotency",
        "dander_runs",
        "dander_schedule_cursors",
        "dander_schedule_queue",
    ]


def test_postgresql_control_migrates_an_existing_graph_only_schema(
    postgresql_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_control
    migrator = PostgreSQLControlMigrator(database)
    graph_migration = CONTROL_SCHEMA_MIGRATIONS[0]
    with pool.connection() as connection, connection.transaction():
        migrator._ensure_ledger(connection)  # noqa: SLF001 - exercise the prior public version
        migrator._apply(connection, graph_migration)  # noqa: SLF001 - construct v1 state
        connection.execute(
            sql.SQL(
                "INSERT INTO {} (version, name, applied_at) VALUES (%s, %s, clock_timestamp())"
            ).format(database.relation("dander_control_schema_migrations")),
            (graph_migration.version, graph_migration.name),
        )

    assert migrator.current_version() == 1
    assert migrator.migrate() == 3
    assert migrator.current_version() == 3


def test_postgresql_control_serializes_concurrent_first_migrations(
    postgresql_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, _, _ = postgresql_control
    barrier = Barrier(2)

    def migrate() -> int:
        barrier.wait(timeout=5)
        return PostgreSQLControlMigrator(database).migrate()

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = tuple(executor.map(lambda _: migrate(), range(2)))

    assert versions == (CONTROL_SCHEMA_VERSION, CONTROL_SCHEMA_VERSION)
    assert PostgreSQLControlMigrator(database).current_version() == CONTROL_SCHEMA_VERSION


def test_postgresql_graph_store_conforms_across_restart(
    postgresql_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_control
    PostgreSQLControlMigrator(database).migrate()
    document = _document()
    store = PostgreSQLGraphStore(database)

    created = store.create(
        "default",
        "graph_one",
        document,
        idempotency_key="create-request-0001",
    )
    with pool.connection() as connection:
        reservation = connection.execute(
            sql.SQL(
                "SELECT idempotency_key_sha256 FROM {} WHERE project = %s AND operation = 'create'"
            ).format(database.relation("dander_graph_mutation_idempotency")),
            ("default",),
        ).fetchone()
    assert reservation == {
        "idempotency_key_sha256": hashlib.sha256(b"create-request-0001").hexdigest()
    }
    restarted = PostgreSQLGraphStore(database)
    assert restarted.get("default", "graph_one") == created
    assert (
        restarted.create(
            "default",
            "graph_one",
            document,
            idempotency_key="create-request-0001",
        )
        == created
    )
    with pytest.raises(GraphStoreIdempotencyConflictError):
        restarted.create(
            "default",
            "another_graph",
            document,
            idempotency_key="create-request-0001",
        )
    with pytest.raises(GraphStoreAlreadyExistsError):
        restarted.create(
            "default",
            "graph_one",
            document,
            idempotency_key="create-precondition-0002",
        )

    second = restarted.create(
        "default",
        "graph_two",
        _changed(document, "second"),
        idempotency_key="create-request-0003",
    )
    first_page = restarted.list("default", limit=1)
    second_page = restarted.list("default", cursor=first_page.next_cursor, limit=1)
    assert [item.graph for item in first_page.items] == ["graph_one"]
    assert [item.graph for item in second_page.items] == ["graph_two"]
    assert second_page.items[0] == second.summary()

    updated = restarted.put(
        "default",
        "graph_one",
        _changed(document, "updated"),
        expected_revision=created.revision,
    )
    assert updated.revision != created.revision
    assert updated.created_at == created.created_at
    with pytest.raises(GraphStoreConflictError):
        restarted.put(
            "default",
            "graph_one",
            document,
            expected_revision=created.revision,
        )

    receipt = restarted.delete(
        "default",
        "graph_one",
        expected_revision=updated.revision,
        idempotency_key="delete-request-0001",
    )
    after_restart = PostgreSQLGraphStore(database)
    assert (
        after_restart.delete(
            "default",
            "graph_one",
            expected_revision=updated.revision,
            idempotency_key="delete-request-0001",
        )
        == receipt
    )
    with pytest.raises(GraphStoreNotFoundError):
        after_restart.get("default", "graph_one")

    # The earlier create precondition rolled back its reservation with the failed mutation.
    recreated = after_restart.create(
        "default",
        "graph_one",
        document,
        idempotency_key="create-precondition-0002",
    )
    assert recreated.graph == "graph_one"

    # Delete precondition failures likewise do not consume an idempotency key.
    with pytest.raises(GraphStoreNotFoundError):
        after_restart.delete(
            "default",
            "later",
            expected_revision="absent-revision",
            idempotency_key="delete-precondition-0002",
        )
    later = after_restart.create(
        "default",
        "later",
        document,
        idempotency_key="create-later-0001",
    )
    assert (
        after_restart.delete(
            "default",
            "later",
            expected_revision=later.revision,
            idempotency_key="delete-precondition-0002",
        ).revision
        == later.revision
    )


def test_postgresql_graph_store_serializes_idempotency_and_cas_races(
    postgresql_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, _, _ = postgresql_control
    PostgreSQLControlMigrator(database).migrate()
    document = _document()
    create_barrier = Barrier(2)

    def create() -> object:
        create_barrier.wait(timeout=5)
        return PostgreSQLGraphStore(database).create(
            "default",
            "concurrent",
            document,
            idempotency_key="create-concurrent-0001",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        created = tuple(executor.map(lambda _: create(), range(2)))
    assert created[0] == created[1]

    current = PostgreSQLGraphStore(database).get("default", "concurrent")
    put_barrier = Barrier(2)

    def put(name: str) -> object:
        put_barrier.wait(timeout=5)
        try:
            return PostgreSQLGraphStore(database).put(
                "default",
                "concurrent",
                _changed(document, name),
                expected_revision=current.revision,
            )
        except GraphStoreConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(put, ("winner-a", "winner-b")))
    assert sum(isinstance(result, GraphStoreConflictError) for result in results) == 1


def test_postgresql_graph_store_rejects_noncanonical_persisted_bytes(
    postgresql_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_control
    PostgreSQLControlMigrator(database).migrate()
    store = PostgreSQLGraphStore(database)
    created = store.create(
        "default",
        "tampered",
        _document(),
        idempotency_key="create-tampered-0001",
    )
    canonical = canonicalize_graph_document(created.document)
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("UPDATE {} SET document_bytes = %s WHERE project = %s AND graph = %s").format(
                database.relation("dander_graphs")
            ),
            (canonical.data + b" ", "default", "tampered"),
        )

    with pytest.raises(GraphStoreCorruptionError, match="canonical content digest"):
        store.get("default", "tampered")


def test_postgresql_control_migrator_rejects_newer_ledgers(
    postgresql_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_control
    migrator = PostgreSQLControlMigrator(database)
    assert migrator.migrate() == CONTROL_SCHEMA_VERSION
    future_version = CONTROL_SCHEMA_VERSION + 1
    with pool.connection() as connection:
        connection.execute(
            sql.SQL(
                "INSERT INTO {} (version, name, applied_at) "
                "VALUES (%s, 'future_schema', clock_timestamp())"
            ).format(database.relation("dander_control_schema_migrations")),
            (future_version,),
        )

    with pytest.raises(RuntimeError, match="newer"):
        migrator.current_version()
