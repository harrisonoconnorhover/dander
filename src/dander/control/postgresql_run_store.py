"""PostgreSQL implementation of Control's provider-neutral ``RunStore`` contract.

Canonical run and attempt JSON is persisted as ``BYTEA`` so PostgreSQL cannot normalize the
bytes that define durable replay.  A non-repeating opaque token provides compare-and-swap updates
without becoming reusable after backup restore.  The injected Control database owns the shared
connection pool; closing this adapter therefore never closes that pool.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
from collections.abc import Mapping
from typing import TYPE_CHECKING

from psycopg import sql

from dander.control.orchestration import (
    AttemptRecord,
    HostedRunState,
    RunClaim,
    RunRecord,
    RunStoreConflictError,
    RunStoreCorruptionError,
    RunStoreError,
    RunStoreIdempotencyConflictError,
    StoredRun,
    StoredRunPage,
)
from dander.control.orchestration_serialization import (
    OrchestrationSerializationError,
    deserialize_attempt_record,
    deserialize_run_record,
    serialize_attempt_record,
    serialize_run_record,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from dander.control.postgresql_control_database import (
        PostgreSQLControlDatabase,
        PostgreSQLRow,
    )

_MAX_RUN_BYTES = 256 * 1024
_MAX_ATTEMPT_BYTES = 128 * 1024
_MAX_MUTATION_BYTES = 16 * 1024
_PORTABLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PostgreSQLRunStore:
    """Persist conditional run snapshots and immutable attempt history in PostgreSQL."""

    def __init__(
        self,
        database: PostgreSQLControlDatabase,
        *,
        revision_factory: Callable[[], str] | None = None,
    ) -> None:
        self._database = database
        self._revision_factory = revision_factory or _new_revision

    def claim(self, record: RunRecord) -> RunClaim:
        """Atomically claim one scoped idempotency key and its pristine run snapshot."""
        self._validate_initial_record(record)
        data = serialize_run_record(record)
        revision = _checked_revision(self._revision_factory())
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                row = connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (run_id, environment, project, "
                        "idempotency_key_sha256, submission_sha256, record_bytes, revision) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT DO NOTHING "
                        "RETURNING run_id, environment, project, idempotency_key_sha256, "
                        "submission_sha256, record_bytes, revision"
                    ).format(self._database.relation("dander_runs")),
                    (
                        record.run_id,
                        record.environment,
                        record.project,
                        record.idempotency_key_sha256,
                        record.submission_sha256,
                        data,
                        revision,
                    ),
                ).fetchone()
                if row is not None:
                    return RunClaim(
                        stored=_stored_run_from_row(row, expected_run_id=record.run_id),
                        created=True,
                    )

                row = connection.execute(
                    sql.SQL(
                        "SELECT run_id, environment, project, idempotency_key_sha256, "
                        "submission_sha256, record_bytes, revision FROM {} "
                        "WHERE environment = %s AND project = %s "
                        "AND idempotency_key_sha256 = %s FOR UPDATE"
                    ).format(self._database.relation("dander_runs")),
                    (record.environment, record.project, record.idempotency_key_sha256),
                ).fetchone()
                if row is None:
                    # The only remaining conflict is an impossible run-id hash collision.  Fail
                    # closed instead of attaching the new request to an unrelated durable run.
                    raise RunStoreConflictError(
                        "The run identity is already claimed by a different request."
                    )
                stored = _stored_run_from_row(row)
                self._validate_claim_replay(stored.record, record)
                return RunClaim(stored=stored, created=False)
        except RunStoreError:
            raise
        except Exception as error:
            raise RunStoreError("The PostgreSQL run-store claim failed.") from error

    def get(self, run_id: str) -> StoredRun | None:
        """Read one canonical run snapshot and its opaque compare-and-swap revision."""
        run_id = _checked_opaque(run_id, "run")
        try:
            with self._database.pool.connection() as connection:
                row = connection.execute(
                    sql.SQL(
                        "SELECT run_id, environment, project, idempotency_key_sha256, "
                        "submission_sha256, record_bytes, revision FROM {} WHERE run_id = %s"
                    ).format(self._database.relation("dander_runs")),
                    (run_id,),
                ).fetchone()
            return None if row is None else _stored_run_from_row(row, expected_run_id=run_id)
        except RunStoreError:
            raise
        except Exception as error:
            raise RunStoreError("The PostgreSQL run-store read failed.") from error

    def find_idempotency(
        self,
        *,
        environment: str,
        project: str,
        idempotency_key_sha256: str,
    ) -> StoredRun | None:
        """Resolve one scoped durable submission claim."""
        environment = _checked_portable(environment, "environment")
        project = _checked_portable(project, "project")
        key_sha256 = _checked_sha256(idempotency_key_sha256, "idempotency key")
        try:
            with self._database.pool.connection() as connection:
                row = connection.execute(
                    sql.SQL(
                        "SELECT run_id, environment, project, idempotency_key_sha256, "
                        "submission_sha256, record_bytes, revision FROM {} "
                        "WHERE environment = %s AND project = %s "
                        "AND idempotency_key_sha256 = %s"
                    ).format(self._database.relation("dander_runs")),
                    (environment, project, key_sha256),
                ).fetchone()
            if row is None:
                return None
            return _stored_run_from_row(
                row,
                expected_lookup=(environment, project, key_sha256),
            )
        except RunStoreError:
            raise
        except Exception as error:
            raise RunStoreError("The PostgreSQL run-store idempotency lookup failed.") from error

    def save(self, stored: StoredRun, record: RunRecord) -> StoredRun:
        """Replace one snapshot only when its opaque revision still matches durable state."""
        if record.run_id != stored.record.run_id or not _same_run_identity(stored.record, record):
            raise RunStoreConflictError("A run snapshot cannot change its durable identity.")
        expected_revision = _checked_revision(stored.revision)
        next_revision = _checked_revision(self._revision_factory())
        if next_revision == expected_revision:
            raise RunStoreCorruptionError("The PostgreSQL run revision provider did not advance.")
        data = serialize_run_record(record)
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                row = connection.execute(
                    sql.SQL(
                        "UPDATE {} SET record_bytes = %s, revision = %s "
                        "WHERE run_id = %s AND revision = %s "
                        "RETURNING run_id, environment, project, idempotency_key_sha256, "
                        "submission_sha256, record_bytes, revision"
                    ).format(self._database.relation("dander_runs")),
                    (data, next_revision, record.run_id, expected_revision),
                ).fetchone()
                if row is None:
                    raise RunStoreConflictError("The PostgreSQL run-store precondition failed.")
                return _stored_run_from_row(row, expected_run_id=record.run_id)
        except RunStoreError:
            raise
        except Exception as error:
            raise RunStoreError("The PostgreSQL run-store save failed.") from error

    def append_attempt(self, attempt: AttemptRecord) -> None:
        """Create one immutable attempt, allowing only byte-identical replay."""
        data = serialize_attempt_record(attempt)
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                inserted = connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (run_id, attempt_id, attempt_number, record_bytes) "
                        "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING"
                    ).format(self._database.relation("dander_attempts")),
                    (
                        attempt.run_id,
                        attempt.attempt_id,
                        attempt.attempt_number,
                        data,
                    ),
                ).rowcount
                if inserted == 1:
                    return
                if inserted != 0:
                    raise RunStoreCorruptionError(
                        "The PostgreSQL attempt append returned an invalid row count."
                    )
                row = connection.execute(
                    sql.SQL(
                        "SELECT run_id, attempt_id, attempt_number, record_bytes FROM {} "
                        "WHERE run_id = %s AND attempt_id = %s FOR UPDATE"
                    ).format(self._database.relation("dander_attempts")),
                    (attempt.run_id, attempt.attempt_id),
                ).fetchone()
                if row is None:
                    raise RunStoreConflictError(
                        "The attempt number already contains different immutable input."
                    )
                existing = _attempt_from_row(
                    row,
                    expected_key=(attempt.run_id, attempt.attempt_id),
                )
                if serialize_attempt_record(existing) != data:
                    raise RunStoreConflictError(
                        "The attempt identity already contains different immutable input."
                    )
        except RunStoreError:
            raise
        except Exception as error:
            raise RunStoreError("The PostgreSQL attempt append failed.") from error

    def claim_mutation(
        self,
        *,
        key_sha256: str,
        operation: str,
        run_id: str,
        result: bytes,
    ) -> bytes:
        """Create one mutation claim and replay its original canonical result."""
        key_sha256 = _checked_sha256(key_sha256, "mutation key")
        operation = _checked_portable(operation, "mutation operation")
        run_id = _checked_opaque(run_id, "run")
        canonical = _canonical_mutation_result(result)
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                row = connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (key_sha256, operation, run_id, result_bytes) "
                        "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING "
                        "RETURNING key_sha256, operation, run_id, result_bytes"
                    ).format(self._database.relation("dander_run_mutation_idempotency")),
                    (key_sha256, operation, run_id, canonical),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        sql.SQL(
                            "SELECT key_sha256, operation, run_id, result_bytes FROM {} "
                            "WHERE key_sha256 = %s FOR UPDATE"
                        ).format(self._database.relation("dander_run_mutation_idempotency")),
                        (key_sha256,),
                    ).fetchone()
                if row is None:
                    raise RunStoreCorruptionError(
                        "The PostgreSQL mutation idempotency claim disappeared."
                    )
                stored_key = _checked_sha256(row.get("key_sha256"), "mutation key")
                stored_operation = _checked_portable(row.get("operation"), "mutation operation")
                stored_run_id = _checked_opaque(row.get("run_id"), "run")
                if (
                    stored_key != key_sha256
                    or stored_operation != operation
                    or stored_run_id != run_id
                ):
                    raise RunStoreIdempotencyConflictError(
                        "The mutation idempotency key belongs to a different operation."
                    )
                stored_result = _stored_bytes(
                    row.get("result_bytes"),
                    _MAX_MUTATION_BYTES,
                    "mutation result",
                )
                canonical_stored_result = _canonical_mutation_result(stored_result)
                if canonical_stored_result != stored_result:
                    raise RunStoreCorruptionError(
                        "The PostgreSQL mutation result is not canonical."
                    )
                return stored_result
        except RunStoreError:
            raise
        except Exception as error:
            raise RunStoreError("The PostgreSQL mutation idempotency claim failed.") from error

    def list(self, *, cursor: str | None, limit: int) -> StoredRunPage:
        """Return a bounded deterministic page ordered by logical run identity."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise RunStoreCorruptionError("The PostgreSQL run-store page size is invalid.")
        after = _decode_cursor(cursor) if cursor is not None else None
        try:
            with self._database.pool.connection() as connection:
                if after is None:
                    rows = connection.execute(
                        sql.SQL(
                            "SELECT run_id, environment, project, idempotency_key_sha256, "
                            "submission_sha256, record_bytes, revision FROM {} "
                            "ORDER BY run_id LIMIT %s"
                        ).format(self._database.relation("dander_runs")),
                        (limit + 1,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        sql.SQL(
                            "SELECT run_id, environment, project, idempotency_key_sha256, "
                            "submission_sha256, record_bytes, revision FROM {} "
                            "WHERE run_id > %s ORDER BY run_id LIMIT %s"
                        ).format(self._database.relation("dander_runs")),
                        (after, limit + 1),
                    ).fetchall()
            selected = rows[:limit]
            items = tuple(_stored_run_from_row(row) for row in selected)
            next_cursor = (
                _encode_cursor(items[-1].record.run_id) if len(rows) > limit and items else None
            )
            return StoredRunPage(items=items, next_cursor=next_cursor)
        except RunStoreError:
            raise
        except Exception as error:
            raise RunStoreError("The PostgreSQL run-store list failed.") from error

    def close(self) -> None:
        """Release no resource; the injected Control database owns its shared pool."""

    @staticmethod
    def _validate_initial_record(record: RunRecord) -> None:
        if (
            record.run_state is not HostedRunState.QUEUED
            or record.attempt_count != 0
            or record.backend_handle is not None
        ):
            raise RunStoreConflictError("A run claim must contain a pristine queued snapshot.")

    @staticmethod
    def _validate_claim_replay(existing: RunRecord, proposed: RunRecord) -> None:
        if (
            existing.environment != proposed.environment
            or existing.project != proposed.project
            or existing.idempotency_key_sha256 != proposed.idempotency_key_sha256
            or existing.run_id != proposed.run_id
        ):
            raise RunStoreCorruptionError(
                "The PostgreSQL run snapshot contradicts its idempotency address."
            )
        if existing.submission_sha256 != proposed.submission_sha256:
            raise RunStoreIdempotencyConflictError(
                "The idempotency key belongs to a different logical submission."
            )


def _stored_run_from_row(
    row: PostgreSQLRow,
    *,
    expected_run_id: str | None = None,
    expected_lookup: tuple[str, str, str] | None = None,
) -> StoredRun:
    run_id = _checked_opaque(row.get("run_id"), "run")
    environment = _checked_portable(row.get("environment"), "environment")
    project = _checked_portable(row.get("project"), "project")
    key_sha256 = _checked_sha256(row.get("idempotency_key_sha256"), "idempotency key")
    submission_sha256 = _checked_sha256(row.get("submission_sha256"), "submission")
    data = _stored_bytes(row.get("record_bytes"), _MAX_RUN_BYTES, "run snapshot")
    if expected_run_id is not None and run_id != expected_run_id:
        raise RunStoreCorruptionError("The PostgreSQL run snapshot is addressed incorrectly.")
    if expected_lookup is not None and (environment, project, key_sha256) != expected_lookup:
        raise RunStoreCorruptionError(
            "The PostgreSQL run idempotency lookup is addressed incorrectly."
        )
    try:
        record = deserialize_run_record(data)
    except OrchestrationSerializationError as error:
        raise RunStoreCorruptionError("The PostgreSQL run snapshot is not canonical.") from error
    if (
        record.run_id != run_id
        or record.environment != environment
        or record.project != project
        or record.idempotency_key_sha256 != key_sha256
        or record.submission_sha256 != submission_sha256
    ):
        raise RunStoreCorruptionError(
            "The PostgreSQL run snapshot contradicts its indexed identity."
        )
    return StoredRun(record=record, revision=_checked_revision(row.get("revision")))


def _attempt_from_row(
    row: PostgreSQLRow,
    *,
    expected_key: tuple[str, str] | None = None,
) -> AttemptRecord:
    run_id = _checked_opaque(row.get("run_id"), "run")
    attempt_id = _checked_opaque(row.get("attempt_id"), "attempt")
    attempt_number = row.get("attempt_number")
    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 1
    ):
        raise RunStoreCorruptionError("The PostgreSQL attempt number is invalid.")
    if expected_key is not None and (run_id, attempt_id) != expected_key:
        raise RunStoreCorruptionError("The PostgreSQL attempt record is addressed incorrectly.")
    data = _stored_bytes(row.get("record_bytes"), _MAX_ATTEMPT_BYTES, "attempt record")
    try:
        attempt = deserialize_attempt_record(data)
    except OrchestrationSerializationError as error:
        raise RunStoreCorruptionError("The PostgreSQL attempt record is not canonical.") from error
    if (
        attempt.run_id != run_id
        or attempt.attempt_id != attempt_id
        or attempt.attempt_number != attempt_number
    ):
        raise RunStoreCorruptionError(
            "The PostgreSQL attempt record contradicts its indexed identity."
        )
    return attempt


def _same_run_identity(first: RunRecord, second: RunRecord) -> bool:
    return (
        first.run_id,
        first.environment,
        first.project,
        first.graph,
        first.graph_revision,
        first.graph_content_sha256,
        first.plan_id,
        first.plan_revision,
        first.trigger,
        first.idempotency_key_sha256,
        first.submission_sha256,
        first.requested_at,
        first.requested_deadline_seconds,
        first.created_at,
    ) == (
        second.run_id,
        second.environment,
        second.project,
        second.graph,
        second.graph_revision,
        second.graph_content_sha256,
        second.plan_id,
        second.plan_revision,
        second.trigger,
        second.idempotency_key_sha256,
        second.submission_sha256,
        second.requested_at,
        second.requested_deadline_seconds,
        second.created_at,
    )


def _canonical_mutation_result(value: object) -> bytes:
    data = _stored_bytes(value, _MAX_MUTATION_BYTES, "mutation result")
    try:
        decoded = json.loads(data)
    except (UnicodeDecodeError, ValueError) as error:
        raise RunStoreCorruptionError("The PostgreSQL mutation result is invalid.") from error
    if not isinstance(decoded, Mapping) or not all(isinstance(key, str) for key in decoded):
        raise RunStoreCorruptionError("The PostgreSQL mutation result is invalid.")
    canonical = json.dumps(
        decoded,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if len(canonical) > _MAX_MUTATION_BYTES:
        raise RunStoreCorruptionError("The PostgreSQL mutation result exceeds its durable bound.")
    return canonical


def _stored_bytes(value: object, maximum: int, label: str) -> bytes:
    if not isinstance(value, bytes) or not value or len(value) > maximum:
        raise RunStoreCorruptionError(
            f"The PostgreSQL {label} is missing or exceeds its durable bound."
        )
    return value


def _checked_portable(value: object, label: str) -> str:
    if not isinstance(value, str) or _PORTABLE_ID.fullmatch(value) is None:
        raise RunStoreCorruptionError(f"The PostgreSQL run-store {label} identifier is invalid.")
    return value


def _checked_opaque(value: object, label: str) -> str:
    if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
        raise RunStoreCorruptionError(f"The PostgreSQL run-store {label} identifier is invalid.")
    return value


def _checked_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RunStoreCorruptionError(f"The PostgreSQL run-store {label} identity is invalid.")
    return value


def _checked_revision(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RunStoreCorruptionError("The PostgreSQL run revision is invalid.")
    return value


def _new_revision() -> str:
    return secrets.token_hex(32)


def _encode_cursor(run_id: str) -> str:
    return base64.urlsafe_b64encode(_checked_opaque(run_id, "run").encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> str:
    if not isinstance(cursor, str) or not cursor or len(cursor) > 1024:
        raise RunStoreCorruptionError("The PostgreSQL run-store cursor is invalid.")
    try:
        data = base64.b64decode(cursor, altchars=b"-_", validate=True)
        run_id = data.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise RunStoreCorruptionError("The PostgreSQL run-store cursor is invalid.") from error
    if _encode_cursor(run_id) != cursor:
        raise RunStoreCorruptionError("The PostgreSQL run-store cursor is invalid.")
    return run_id


__all__ = ["PostgreSQLRunStore"]
