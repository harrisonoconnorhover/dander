"""Focused PostgreSQL RunStore decoding and live conformance."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.control.orchestration import (
    AttemptRecord,
    BackendHandle,
    CleanupState,
    HostedRunState,
    ResultsState,
    RunOutcome,
    RunRecord,
    RunStoreConflictError,
    RunStoreCorruptionError,
    RunStoreIdempotencyConflictError,
    RunTrigger,
    TriggerKind,
    attempt_identity,
    run_needs_reconciliation,
    transition_run,
)
from dander.control.orchestration_serialization import (
    serialize_attempt_record,
    serialize_run_record,
)
from dander.control.postgresql_control_database import (
    CONTROL_SCHEMA_MIGRATIONS,
    CONTROL_SCHEMA_VERSION,
    PostgreSQLControlDatabase,
    PostgreSQLControlMigrator,
    PostgreSQLControlPool,
)
from dander.control.postgresql_run_store import (
    PostgreSQLRunStore,
    _attempt_from_row,
    _decode_cursor,
    _encode_cursor,
    _stored_run_from_row,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

NOW = datetime(2026, 8, 30, 16, tzinfo=UTC)


def _run(*, run_id: str = "run-one", key: str = "key-one") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        environment="onprem",
        project="demo",
        graph="hosted-graph",
        graph_revision="graph-r1",
        graph_content_sha256="a" * 64,
        plan_id="default",
        plan_revision="b" * 64,
        trigger=RunTrigger(kind=TriggerKind.API, trigger_id="api"),
        idempotency_key_sha256=hashlib.sha256(key.encode()).hexdigest(),
        submission_sha256=hashlib.sha256(f"submission:{key}".encode()).hexdigest(),
        requested_at=NOW,
        requested_deadline_seconds=600,
        run_state=HostedRunState.QUEUED,
        outcome=RunOutcome.UNKNOWN,
        results_state=ResultsState.PENDING,
        cleanup_state=CleanupState.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )


def _attempt(record: RunRecord) -> AttemptRecord:
    return AttemptRecord(
        run_id=record.run_id,
        attempt_id=attempt_identity(record.run_id, 1),
        attempt_number=1,
        plan_id=record.plan_id,
        plan_revision=record.plan_revision,
        backend_id="spark_yarn",
        trigger=record.trigger,
        created_at=NOW + timedelta(seconds=1),
    )


def _recovery_records() -> tuple[tuple[RunRecord, bool], ...]:
    active = tuple(
        (
            replace(
                _run(run_id=state.value, key=state.value),
                run_state=state,
                backend_handle=BackendHandle("spark_yarn", f"provider:{state.value}"),
                attempt_count=1,
                current_attempt_id=attempt_identity(state.value, 1),
            ),
            True,
        )
        for state in HostedRunState
        if state is not HostedRunState.TERMINAL
    )
    terminal = replace(
        _run(),
        run_state=HostedRunState.TERMINAL,
        terminal_at=NOW,
        outcome=RunOutcome.FAILED,
        results_state=ResultsState.UNAVAILABLE,
        cleanup_state=CleanupState.CONFIRMED,
    )
    variants = (
        ("finished", terminal, False),
        ("unknown-outcome", replace(terminal, outcome=RunOutcome.UNKNOWN), True),
        (
            "pending-results",
            replace(terminal, outcome=RunOutcome.SUCCEEDED, results_state=ResultsState.PENDING),
            True,
        ),
        ("pending-cleanup", replace(terminal, cleanup_state=CleanupState.PENDING), True),
        ("uncertain-cleanup", replace(terminal, cleanup_state=CleanupState.UNCERTAIN), True),
    )
    return active + tuple(
        (
            replace(
                record,
                run_id=run_id,
                idempotency_key_sha256=hashlib.sha256(run_id.encode()).hexdigest(),
                submission_sha256=hashlib.sha256(f"submission:{run_id}".encode()).hexdigest(),
            ),
            expected,
        )
        for run_id, record, expected in variants
    )


def _create_v3_run_schema(database: PostgreSQLControlDatabase) -> None:
    migrator = PostgreSQLControlMigrator(database)
    with database.pool.connection() as connection, connection.transaction():
        migrator._ensure_ledger(connection)  # noqa: SLF001 - construct the prior schema
        for migration in CONTROL_SCHEMA_MIGRATIONS[:3]:
            migrator._apply(connection, migration)  # noqa: SLF001 - construct the prior schema
            connection.execute(
                sql.SQL(
                    "INSERT INTO {} (version, name, applied_at) VALUES (%s, %s, clock_timestamp())"
                ).format(database.relation("dander_control_schema_migrations")),
                (migration.version, migration.name),
            )


@pytest.fixture
def postgresql_run_control() -> Iterator[
    tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str]
]:
    dsn = os.environ.get("DANDER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("DANDER_TEST_POSTGRES_DSN is not configured")
    schema_name = f"dander_run_{uuid.uuid4().hex}"
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


def test_postgresql_run_decoder_requires_canonical_indexed_bytes() -> None:
    record = _run()
    data = serialize_run_record(record)
    row = {
        "run_id": record.run_id,
        "environment": record.environment,
        "project": record.project,
        "idempotency_key_sha256": record.idempotency_key_sha256,
        "submission_sha256": record.submission_sha256,
        "record_bytes": data,
        "revision": "d" * 64,
    }

    stored = _stored_run_from_row(row, expected_run_id=record.run_id)

    assert stored.record == record
    assert stored.revision == "d" * 64
    with pytest.raises(RunStoreCorruptionError, match="canonical"):
        _stored_run_from_row({**row, "record_bytes": data + b" "})
    with pytest.raises(RunStoreCorruptionError, match="indexed identity"):
        _stored_run_from_row({**row, "submission_sha256": "c" * 64})
    with pytest.raises(RunStoreCorruptionError, match="revision"):
        _stored_run_from_row({**row, "revision": "r1"})


def test_postgresql_run_cursor_is_canonical_and_bounded() -> None:
    cursor = _encode_cursor("run-one")

    assert _decode_cursor(cursor) == "run-one"
    with pytest.raises(RunStoreCorruptionError, match="cursor"):
        _decode_cursor(cursor.rstrip("="))
    with pytest.raises(RunStoreCorruptionError, match="cursor"):
        _decode_cursor("not base64!!")


def test_postgresql_attempt_decoder_requires_canonical_indexed_bytes() -> None:
    attempt = _attempt(_run())
    data = serialize_attempt_record(attempt)
    row = {
        "run_id": attempt.run_id,
        "attempt_id": attempt.attempt_id,
        "attempt_number": attempt.attempt_number,
        "record_bytes": data,
    }

    assert _attempt_from_row(row, expected_key=(attempt.run_id, attempt.attempt_id)) == attempt
    with pytest.raises(RunStoreCorruptionError, match="canonical"):
        _attempt_from_row({**row, "record_bytes": data + b" "})
    with pytest.raises(RunStoreCorruptionError, match="indexed identity"):
        _attempt_from_row({**row, "attempt_number": 2})


def test_postgresql_run_store_conforms_across_restart(
    postgresql_run_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_run_control
    assert PostgreSQLControlMigrator(database).migrate() == CONTROL_SCHEMA_VERSION
    first = _run()
    store = PostgreSQLRunStore(database)

    claimed = store.claim(first)
    restarted = PostgreSQLRunStore(database)
    replay = restarted.claim(replace(first, requested_at=NOW + timedelta(minutes=1)))

    assert claimed.created is True
    assert len(claimed.stored.revision) == 64
    assert replay.created is False
    assert replay.stored == claimed.stored
    assert restarted.get(first.run_id) == claimed.stored
    assert (
        restarted.find_idempotency(
            environment=first.environment,
            project=first.project,
            idempotency_key_sha256=first.idempotency_key_sha256,
        )
        == claimed.stored
    )
    with pytest.raises(RunStoreIdempotencyConflictError):
        restarted.claim(replace(first, submission_sha256="c" * 64))

    canceling = transition_run(
        claimed.stored.record,
        HostedRunState.CANCELING,
        now=NOW + timedelta(seconds=2),
    )
    saved = restarted.save(claimed.stored, canceling)
    assert saved.revision != claimed.stored.revision
    assert PostgreSQLRunStore(database).get(first.run_id) == saved
    with pytest.raises(RunStoreConflictError, match="precondition"):
        restarted.save(claimed.stored, canceling)

    attempt = _attempt(first)
    restarted.append_attempt(attempt)
    PostgreSQLRunStore(database).append_attempt(attempt)
    with pytest.raises(RunStoreConflictError, match="different immutable"):
        restarted.append_attempt(replace(attempt, backend_id="dataproc_serverless"))

    second = _run(run_id="run-two", key="key-two")
    restarted.claim(second)
    page_one = restarted.list(cursor=None, limit=1)
    page_two = restarted.list(cursor=page_one.next_cursor, limit=1)
    assert [item.record.run_id for item in page_one.items] == ["run-one"]
    assert [item.record.run_id for item in page_two.items] == ["run-two"]
    assert page_two.next_cursor is None

    mutation_key = hashlib.sha256(b"cancel-key-0001").hexdigest()
    original = b'{"accepted":true,"operation":"cancel","run_id":"run-one"}'
    changed = b'{"accepted":false,"operation":"cancel","run_id":"run-one"}'
    assert (
        restarted.claim_mutation(
            key_sha256=mutation_key,
            operation="cancel",
            run_id="run-one",
            result=original,
        )
        == original
    )
    assert (
        PostgreSQLRunStore(database).claim_mutation(
            key_sha256=mutation_key,
            operation="cancel",
            run_id="run-one",
            result=changed,
        )
        == original
    )
    with pytest.raises(RunStoreIdempotencyConflictError):
        restarted.claim_mutation(
            key_sha256=mutation_key,
            operation="cancel",
            run_id="run-two",
            result=original,
        )

    restarted.close()
    assert not pool.closed


def test_pending_pages_skip_completed_history_without_changing_public_history(
    postgresql_run_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, _, _ = postgresql_run_control
    PostgreSQLControlMigrator(database).migrate()
    store = PostgreSQLRunStore(database)
    pending_ids = {"run-011", "run-051", "run-099"}
    for index in range(100):
        run_id = f"run-{index:03}"
        claimed = store.claim(_run(run_id=run_id, key=run_id)).stored
        if run_id not in pending_ids:
            store.save(
                claimed,
                transition_run(
                    claimed.record,
                    HostedRunState.TERMINAL,
                    now=NOW + timedelta(seconds=1),
                    outcome=RunOutcome.CANCELED,
                    results_state=ResultsState.UNAVAILABLE,
                    cleanup_state=CleanupState.CONFIRMED,
                ),
            )

    first = store.list_pending(cursor=None, limit=2)
    assert [item.record.run_id for item in first.items] == ["run-011", "run-051"]
    assert first.next_cursor is not None
    # The cursor remains valid when the last returned row leaves the pending index.
    finished = first.items[-1]
    store.save(
        finished,
        transition_run(
            finished.record,
            HostedRunState.TERMINAL,
            now=NOW + timedelta(seconds=2),
            outcome=RunOutcome.CANCELED,
            results_state=ResultsState.UNAVAILABLE,
            cleanup_state=CleanupState.CONFIRMED,
        ),
    )
    second = PostgreSQLRunStore(database).list_pending(cursor=first.next_cursor, limit=2)
    assert [item.record.run_id for item in second.items] == ["run-099"]
    assert second.next_cursor is None
    assert len(store.list(cursor=None, limit=100).items) == 100


def test_recovery_projection_preserves_all_unresolved_dimensions(
    postgresql_run_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_run_control
    PostgreSQLControlMigrator(database).migrate()
    store = PostgreSQLRunStore(database)
    expected_ids = []
    for record, expected in _recovery_records():
        assert run_needs_reconciliation(record) is expected
        initial = _run(run_id=record.run_id, key=record.run_id)
        claimed = store.claim(initial).stored
        store.save(claimed, record)
        if expected:
            expected_ids.append(record.run_id)
    with pool.connection() as connection:
        rows = connection.execute(
            sql.SQL("SELECT run_id, needs_reconciliation FROM {} ORDER BY run_id").format(
                database.relation("dander_runs")
            )
        ).fetchall()
    assert {row["run_id"]: row["needs_reconciliation"] for row in rows} == {
        record.run_id: expected for record, expected in _recovery_records()
    }
    assert [item.record.run_id for item in store.list_pending(cursor=None, limit=100).items] == (
        sorted(expected_ids)
    )


def test_pending_projection_changes_atomically_with_cas_and_idempotent_claim(
    postgresql_run_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, _, _ = postgresql_run_control
    PostgreSQLControlMigrator(database).migrate()
    store = PostgreSQLRunStore(database)
    initial = _run()
    claimed = store.claim(initial).stored
    pending_cleanup = transition_run(
        initial,
        HostedRunState.TERMINAL,
        now=NOW + timedelta(seconds=1),
        outcome=RunOutcome.FAILED,
        results_state=ResultsState.UNAVAILABLE,
        cleanup_state=CleanupState.UNCERTAIN,
    )
    pending = store.save(claimed, pending_cleanup)
    completed = transition_run(
        pending.record,
        HostedRunState.TERMINAL,
        now=NOW + timedelta(seconds=2),
        cleanup_state=CleanupState.CONFIRMED,
    )
    with pytest.raises(RunStoreConflictError, match="precondition"):
        store.save(claimed, completed)
    assert store.list_pending(cursor=None, limit=1).items == (pending,)

    settled = store.save(pending, completed)
    with pytest.raises(RunStoreConflictError, match="precondition"):
        store.save(pending, pending_cleanup)
    replay = PostgreSQLRunStore(database).claim(initial)
    assert not replay.created
    assert replay.stored == settled
    assert store.list_pending(cursor=None, limit=1).items == ()
    assert store.get(initial.run_id) == settled


def test_v4_backfill_preserves_canonical_bytes_revisions_and_reopens(
    postgresql_run_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, schema_name = postgresql_run_control
    _create_v3_run_schema(database)
    records = _recovery_records()
    records += tuple(
        (_run(run_id=f"archive-{index:03}", key=f"archive-{index:03}"), True)
        for index in range(100)
    )
    with pool.connection() as connection, connection.transaction():
        for record, _ in records:
            connection.execute(
                sql.SQL(
                    "INSERT INTO {} (run_id, environment, project, idempotency_key_sha256, "
                    "submission_sha256, record_bytes, revision) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                ).format(database.relation("dander_runs")),
                (
                    record.run_id,
                    record.environment,
                    record.project,
                    record.idempotency_key_sha256,
                    record.submission_sha256,
                    serialize_run_record(record),
                    "d" * 64,
                ),
            )

    migrator = PostgreSQLControlMigrator(database)
    assert migrator.current_version() == 3
    assert migrator.migrate() == 4
    assert PostgreSQLControlMigrator(database).migrate() == 4
    with pool.connection() as connection:
        rows = connection.execute(
            sql.SQL("SELECT run_id, record_bytes, revision, needs_reconciliation FROM {}").format(
                database.relation("dander_runs")
            )
        ).fetchall()
        index = connection.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND indexname = %s",
            (schema_name, "dander_runs_pending_recovery"),
        ).fetchone()
    assert index is not None
    assert "(run_id) WHERE needs_reconciliation" in index["indexdef"]
    assert {
        row["run_id"]: (row["record_bytes"], row["revision"], row["needs_reconciliation"])
        for row in rows
    } == {
        record.run_id: (serialize_run_record(record), "d" * 64, expected)
        for record, expected in records
    }
    reopened = PostgreSQLRunStore(database)
    for record, _ in records:
        stored = reopened.get(record.run_id)
        assert stored is not None
        assert stored.record == record
        assert stored.revision == "d" * 64


def test_v4_backfill_rejects_invalid_canonical_history_and_rolls_back(
    postgresql_run_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, schema_name = postgresql_run_control
    _create_v3_run_schema(database)
    record = _run()
    with pool.connection() as connection:
        connection.execute(
            sql.SQL(
                "INSERT INTO {} (run_id, environment, project, idempotency_key_sha256, "
                "submission_sha256, record_bytes, revision) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            ).format(database.relation("dander_runs")),
            (
                record.run_id,
                record.environment,
                record.project,
                record.idempotency_key_sha256,
                record.submission_sha256,
                serialize_run_record(record) + b" ",
                "d" * 64,
            ),
        )
    migrator = PostgreSQLControlMigrator(database)
    with pytest.raises(RunStoreCorruptionError, match="canonical"):
        migrator.migrate()
    assert migrator.current_version() == 3
    with pool.connection() as connection:
        column = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'dander_runs' "
            "AND column_name = 'needs_reconciliation'",
            (schema_name,),
        ).fetchone()
    assert column is None


def test_postgresql_run_store_rejects_noncanonical_restored_mutation_result(
    postgresql_run_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_run_control
    PostgreSQLControlMigrator(database).migrate()
    store = PostgreSQLRunStore(database)
    store.claim(_run())
    key = hashlib.sha256(b"cancel-key-corrupt").hexdigest()
    canonical = b'{"accepted":true,"run_id":"run-one"}'
    assert (
        store.claim_mutation(
            key_sha256=key,
            operation="cancel",
            run_id="run-one",
            result=canonical,
        )
        == canonical
    )
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("UPDATE {} SET result_bytes = %s WHERE key_sha256 = %s").format(
                database.relation("dander_run_mutation_idempotency")
            ),
            (b'{"run_id": "run-one", "accepted": true}', key),
        )

    with pytest.raises(RunStoreCorruptionError, match="not canonical"):
        store.claim_mutation(
            key_sha256=key,
            operation="cancel",
            run_id="run-one",
            result=canonical,
        )


def test_postgresql_run_revision_cannot_repeat_across_restore(
    postgresql_run_control: tuple[PostgreSQLControlDatabase, PostgreSQLControlPool, str],
) -> None:
    database, pool, _ = postgresql_run_control
    PostgreSQLControlMigrator(database).migrate()
    initial_token = "1" * 64
    pre_restore_token = "2" * 64
    post_restore_token = "3" * 64
    record = _run()
    initial_store = PostgreSQLRunStore(
        database,
        revision_factory=iter((initial_token, pre_restore_token)).__next__,
    )
    claimed = initial_store.claim(record).stored
    assert claimed.revision == initial_token
    canceling = transition_run(
        record,
        HostedRunState.CANCELING,
        now=NOW + timedelta(seconds=2),
    )
    stale = initial_store.save(claimed, canceling)
    assert stale.revision == pre_restore_token

    # Simulate restoring the earlier durable snapshot, then advancing along a different branch.
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("UPDATE {} SET record_bytes = %s, revision = %s WHERE run_id = %s").format(
                database.relation("dander_runs")
            ),
            (serialize_run_record(claimed.record), claimed.revision, record.run_id),
        )
    restored = PostgreSQLRunStore(database).get(record.run_id)
    assert restored == claimed
    restored_store = PostgreSQLRunStore(
        database,
        revision_factory=iter((post_restore_token, "4" * 64)).__next__,
    )
    post_restore = restored_store.save(restored, canceling)
    assert post_restore.revision == post_restore_token

    with pytest.raises(RunStoreConflictError, match="precondition"):
        restored_store.save(stale, stale.record)
