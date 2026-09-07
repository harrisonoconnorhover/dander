"""PostgreSQL implementation of the provider-neutral :class:`GraphStore` contract.

Canonical graph JSON is persisted as ``BYTEA`` so PostgreSQL cannot normalize the bytes that
define Dander's portable content digest.  Create/delete idempotency reservations, their exact
replay result, and the graph mutation commit in one transaction.  Graph revisions are opaque
compare-and-swap tokens and never expose PostgreSQL transaction identifiers.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from psycopg import Connection, sql

from dander.control.graph_store import (
    MAX_GRAPH_DOCUMENT_BYTES,
    GraphDeleteReceipt,
    GraphPage,
    GraphRecord,
    GraphStore,
    GraphStoreAlreadyExistsError,
    GraphStoreConflictError,
    GraphStoreCorruptionError,
    GraphStoreDocumentError,
    GraphStoreError,
    GraphStoreIdempotencyConflictError,
    GraphStoreIdentifierError,
    GraphStoreNotFoundError,
    GraphSummary,
    RevisionFactory,
    _create_fingerprint,
    _decode_cursor,
    _delete_fingerprint,
    _encode_cursor,
    _opaque_revision,
    _timestamp,
    _validated_graph_key,
    _validated_idempotency_key,
    _validated_identifier,
    _validated_max_bytes,
    _validated_new_revision,
    _validated_page_size,
    _validated_revision,
    canonicalize_graph_document,
)

if TYPE_CHECKING:
    from dander.control.models import PipelineGraphDocument
    from dander.control.postgresql_control_database import (
        PostgreSQLControlDatabase,
        PostgreSQLRow,
    )

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_Operation = Literal["create", "delete"]


class PostgreSQLGraphStore(GraphStore):
    """Persist canonical graphs and transactional idempotency results in PostgreSQL."""

    def __init__(
        self,
        database: PostgreSQLControlDatabase,
        *,
        max_graph_bytes: int = MAX_GRAPH_DOCUMENT_BYTES,
        revision_factory: RevisionFactory | None = None,
    ) -> None:
        self._database = database
        self._max_graph_bytes = _validated_max_bytes(max_graph_bytes)
        if self._max_graph_bytes > MAX_GRAPH_DOCUMENT_BYTES:
            raise GraphStoreDocumentError(
                "The PostgreSQL graph document bound exceeds the Control schema limit."
            )
        self._revision_factory = revision_factory or _opaque_revision

    def list(self, project: str, *, cursor: str | None = None, limit: int = 50) -> GraphPage:
        project = _validated_identifier(project, "project")
        limit = _validated_page_size(limit)
        after = _decode_cursor(project, cursor)
        try:
            with self._database.pool.connection() as connection:
                if after is None:
                    rows = connection.execute(
                        sql.SQL(
                            "SELECT project, graph, revision, content_sha256, "
                            "created_at, updated_at FROM {} WHERE project = %s "
                            "ORDER BY graph LIMIT %s"
                        ).format(self._database.relation("dander_graphs")),
                        (project, limit + 1),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        sql.SQL(
                            "SELECT project, graph, revision, content_sha256, "
                            "created_at, updated_at FROM {} WHERE project = %s AND graph > %s "
                            "ORDER BY graph LIMIT %s"
                        ).format(self._database.relation("dander_graphs")),
                        (project, after, limit + 1),
                    ).fetchall()
            selected = rows[:limit]
            items = tuple(_summary_from_row(row, expected_project=project) for row in selected)
            next_cursor = (
                _encode_cursor(project, items[-1].graph) if len(rows) > limit and items else None
            )
            return GraphPage(items=items, next_cursor=next_cursor)
        except GraphStoreError:
            raise
        except Exception as error:
            raise GraphStoreError("The PostgreSQL graph-store list failed.") from error

    def get(self, project: str, graph: str) -> GraphRecord:
        project, graph = _validated_graph_key(project, graph)
        try:
            with self._database.pool.connection() as connection:
                row = connection.execute(
                    sql.SQL(
                        "SELECT project, graph, document_bytes, revision, content_sha256, "
                        "created_at, updated_at FROM {} WHERE project = %s AND graph = %s"
                    ).format(self._database.relation("dander_graphs")),
                    (project, graph),
                ).fetchone()
            if row is None:
                raise GraphStoreNotFoundError("The graph does not exist.")
            return _record_from_row(
                row,
                max_graph_bytes=self._max_graph_bytes,
                expected_key=(project, graph),
            )
        except GraphStoreError:
            raise
        except Exception as error:
            raise GraphStoreError("The PostgreSQL graph-store read failed.") from error

    def create(
        self,
        project: str,
        graph: str,
        document: PipelineGraphDocument,
        *,
        idempotency_key: str,
    ) -> GraphRecord:
        project, graph = _validated_graph_key(project, graph)
        idempotency_key = _validated_idempotency_key(idempotency_key)
        idempotency_key_sha256 = _idempotency_key_sha256(idempotency_key)
        canonical = canonicalize_graph_document(document, max_bytes=self._max_graph_bytes)
        fingerprint = _create_fingerprint(project, graph, canonical.content_sha256)
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                claimed, reservation = self._reserve_idempotency(
                    connection,
                    project=project,
                    operation="create",
                    idempotency_key_sha256=idempotency_key_sha256,
                    graph=graph,
                    fingerprint=fingerprint,
                )
                if not claimed:
                    return _create_replay_from_row(
                        reservation,
                        max_graph_bytes=self._max_graph_bytes,
                        expected_key=(project, graph),
                    )

                revision = _validated_new_revision(self._revision_factory())
                row = connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (project, graph, document_bytes, revision, "
                        "content_sha256, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, clock_timestamp(), clock_timestamp()) "
                        "ON CONFLICT (project, graph) DO NOTHING "
                        "RETURNING project, graph, document_bytes, revision, content_sha256, "
                        "created_at, updated_at"
                    ).format(self._database.relation("dander_graphs")),
                    (project, graph, canonical.data, revision, canonical.content_sha256),
                ).fetchone()
                if row is None:
                    raise GraphStoreAlreadyExistsError("The graph already exists.")
                record = _record_from_row(
                    row,
                    max_graph_bytes=self._max_graph_bytes,
                    expected_key=(project, graph),
                )
                completed = connection.execute(
                    sql.SQL(
                        "UPDATE {} SET completed = TRUE, result_revision = %s, "
                        "result_content_sha256 = %s, result_document_bytes = %s, "
                        "result_created_at = %s, result_updated_at = %s "
                        "WHERE project = %s AND operation = 'create' "
                        "AND idempotency_key_sha256 = %s "
                        "AND request_fingerprint = %s AND completed = FALSE"
                    ).format(self._database.relation("dander_graph_mutation_idempotency")),
                    (
                        record.revision,
                        record.content_sha256,
                        canonical.data,
                        _datetime_from_timestamp(row, "created_at"),
                        _datetime_from_timestamp(row, "updated_at"),
                        project,
                        idempotency_key_sha256,
                        fingerprint,
                    ),
                )
                if completed.rowcount != 1:
                    raise GraphStoreCorruptionError(
                        "The PostgreSQL create idempotency result could not be committed."
                    )
                return record
        except GraphStoreError:
            raise
        except Exception as error:
            raise GraphStoreError("The PostgreSQL graph-store create failed.") from error

    def put(
        self,
        project: str,
        graph: str,
        document: PipelineGraphDocument,
        *,
        expected_revision: str,
    ) -> GraphRecord:
        project, graph = _validated_graph_key(project, graph)
        expected_revision = _validated_revision(expected_revision)
        canonical = canonicalize_graph_document(document, max_bytes=self._max_graph_bytes)
        revision = _validated_new_revision(self._revision_factory())
        if revision == expected_revision:
            raise GraphStoreCorruptionError(
                "The revision provider did not advance the PostgreSQL graph revision."
            )
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                row = connection.execute(
                    sql.SQL(
                        "UPDATE {} SET document_bytes = %s, revision = %s, "
                        "content_sha256 = %s, updated_at = clock_timestamp() "
                        "WHERE project = %s AND graph = %s AND revision = %s "
                        "RETURNING project, graph, document_bytes, revision, content_sha256, "
                        "created_at, updated_at"
                    ).format(self._database.relation("dander_graphs")),
                    (
                        canonical.data,
                        revision,
                        canonical.content_sha256,
                        project,
                        graph,
                        expected_revision,
                    ),
                ).fetchone()
                if row is None:
                    self._raise_missing_or_conflict(connection, project, graph)
                assert row is not None
                return _record_from_row(
                    row,
                    max_graph_bytes=self._max_graph_bytes,
                    expected_key=(project, graph),
                )
        except GraphStoreError:
            raise
        except Exception as error:
            raise GraphStoreError("The PostgreSQL graph-store update failed.") from error

    def delete(
        self,
        project: str,
        graph: str,
        *,
        expected_revision: str,
        idempotency_key: str,
    ) -> GraphDeleteReceipt:
        project, graph = _validated_graph_key(project, graph)
        expected_revision = _validated_revision(expected_revision)
        idempotency_key = _validated_idempotency_key(idempotency_key)
        idempotency_key_sha256 = _idempotency_key_sha256(idempotency_key)
        fingerprint = _delete_fingerprint(project, graph, expected_revision)
        try:
            with self._database.pool.connection() as connection, connection.transaction():
                claimed, reservation = self._reserve_idempotency(
                    connection,
                    project=project,
                    operation="delete",
                    idempotency_key_sha256=idempotency_key_sha256,
                    graph=graph,
                    fingerprint=fingerprint,
                )
                if not claimed:
                    return _delete_replay_from_row(
                        reservation,
                        expected_key=(project, graph),
                    )

                row = connection.execute(
                    sql.SQL(
                        "DELETE FROM {} WHERE project = %s AND graph = %s AND revision = %s "
                        "RETURNING project, graph, revision, content_sha256, "
                        "clock_timestamp() AS deleted_at"
                    ).format(self._database.relation("dander_graphs")),
                    (project, graph, expected_revision),
                ).fetchone()
                if row is None:
                    self._raise_missing_or_conflict(connection, project, graph)
                assert row is not None
                receipt = _delete_receipt_from_row(row, expected_key=(project, graph))
                completed = connection.execute(
                    sql.SQL(
                        "UPDATE {} SET completed = TRUE, result_revision = %s, "
                        "result_content_sha256 = %s, result_deleted_at = %s "
                        "WHERE project = %s AND operation = 'delete' "
                        "AND idempotency_key_sha256 = %s "
                        "AND request_fingerprint = %s AND completed = FALSE"
                    ).format(self._database.relation("dander_graph_mutation_idempotency")),
                    (
                        receipt.revision,
                        receipt.content_sha256,
                        _datetime_from_timestamp(row, "deleted_at"),
                        project,
                        idempotency_key_sha256,
                        fingerprint,
                    ),
                )
                if completed.rowcount != 1:
                    raise GraphStoreCorruptionError(
                        "The PostgreSQL delete idempotency result could not be committed."
                    )
                return receipt
        except GraphStoreError:
            raise
        except Exception as error:
            raise GraphStoreError("The PostgreSQL graph-store delete failed.") from error

    def _reserve_idempotency(
        self,
        connection: Connection[PostgreSQLRow],
        *,
        project: str,
        operation: _Operation,
        idempotency_key_sha256: str,
        graph: str,
        fingerprint: str,
    ) -> tuple[bool, PostgreSQLRow]:
        inserted = connection.execute(
            sql.SQL(
                "INSERT INTO {} (project, operation, idempotency_key_sha256, "
                "request_fingerprint, "
                "graph) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (project, operation, idempotency_key_sha256) DO NOTHING"
            ).format(self._database.relation("dander_graph_mutation_idempotency")),
            (project, operation, idempotency_key_sha256, fingerprint, graph),
        ).rowcount
        if inserted not in (0, 1):
            raise GraphStoreCorruptionError(
                "The PostgreSQL idempotency reservation returned an invalid row count."
            )
        row = connection.execute(
            sql.SQL(
                "SELECT graph, request_fingerprint, completed, result_revision, "
                "result_content_sha256, result_document_bytes, result_created_at, "
                "result_updated_at, result_deleted_at FROM {} "
                "WHERE project = %s AND operation = %s "
                "AND idempotency_key_sha256 = %s FOR UPDATE"
            ).format(self._database.relation("dander_graph_mutation_idempotency")),
            (project, operation, idempotency_key_sha256),
        ).fetchone()
        if row is None:
            raise GraphStoreCorruptionError("The PostgreSQL idempotency reservation disappeared.")
        if row.get("request_fingerprint") != fingerprint or row.get("graph") != graph:
            raise GraphStoreIdempotencyConflictError(
                "The idempotency key was already used for a different request."
            )
        completed = row.get("completed")
        if not isinstance(completed, bool):
            raise GraphStoreCorruptionError("The PostgreSQL idempotency reservation is invalid.")
        if inserted == 1 and completed:
            raise GraphStoreCorruptionError(
                "The new PostgreSQL idempotency reservation is inconsistent."
            )
        if inserted == 0 and not completed:
            raise GraphStoreCorruptionError(
                "The PostgreSQL idempotency reservation has no durable result."
            )
        return inserted == 1, row

    def _raise_missing_or_conflict(
        self,
        connection: Connection[PostgreSQLRow],
        project: str,
        graph: str,
    ) -> None:
        row = connection.execute(
            sql.SQL("SELECT revision FROM {} WHERE project = %s AND graph = %s").format(
                self._database.relation("dander_graphs")
            ),
            (project, graph),
        ).fetchone()
        if row is None:
            raise GraphStoreNotFoundError("The graph does not exist.")
        raise GraphStoreConflictError("The graph revision is stale.")


def _record_from_row(
    row: PostgreSQLRow,
    *,
    max_graph_bytes: int,
    expected_key: tuple[str, str] | None = None,
) -> GraphRecord:
    project, graph = _stored_key(row)
    if expected_key is not None and (project, graph) != expected_key:
        raise GraphStoreCorruptionError("The PostgreSQL graph record is addressed incorrectly.")
    revision = _stored_revision(row.get("revision"))
    content_sha256 = _stored_sha256(row.get("content_sha256"))
    data = _stored_bytes(row.get("document_bytes"))
    try:
        payload = json.loads(data.decode("utf-8"))
        canonical = canonicalize_graph_document(payload, max_bytes=max_graph_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError, GraphStoreDocumentError) as error:
        raise GraphStoreCorruptionError(
            "The PostgreSQL graph record does not contain a valid canonical graph."
        ) from error
    if canonical.data != data or canonical.content_sha256 != content_sha256:
        raise GraphStoreCorruptionError(
            "The PostgreSQL graph record does not match its canonical content digest."
        )
    created = _datetime_from_timestamp(row, "created_at")
    updated = _datetime_from_timestamp(row, "updated_at")
    if created > updated:
        raise GraphStoreCorruptionError("The PostgreSQL graph timestamps are inconsistent.")
    return GraphRecord(
        project=project,
        graph=graph,
        document=canonical.document,
        revision=revision,
        content_sha256=content_sha256,
        created_at=_timestamp(created),
        updated_at=_timestamp(updated),
    )


def _summary_from_row(row: PostgreSQLRow, *, expected_project: str) -> GraphSummary:
    project, graph = _stored_key(row)
    if project != expected_project:
        raise GraphStoreCorruptionError("The PostgreSQL graph page crossed a project boundary.")
    created = _datetime_from_timestamp(row, "created_at")
    updated = _datetime_from_timestamp(row, "updated_at")
    if created > updated:
        raise GraphStoreCorruptionError("The PostgreSQL graph timestamps are inconsistent.")
    return GraphSummary(
        project=project,
        graph=graph,
        revision=_stored_revision(row.get("revision")),
        content_sha256=_stored_sha256(row.get("content_sha256")),
        created_at=_timestamp(created),
        updated_at=_timestamp(updated),
    )


def _create_replay_from_row(
    row: PostgreSQLRow,
    *,
    max_graph_bytes: int,
    expected_key: tuple[str, str],
) -> GraphRecord:
    project, graph = expected_key
    return _record_from_row(
        {
            "project": project,
            "graph": graph,
            "document_bytes": row.get("result_document_bytes"),
            "revision": row.get("result_revision"),
            "content_sha256": row.get("result_content_sha256"),
            "created_at": row.get("result_created_at"),
            "updated_at": row.get("result_updated_at"),
        },
        max_graph_bytes=max_graph_bytes,
        expected_key=expected_key,
    )


def _delete_replay_from_row(
    row: PostgreSQLRow,
    *,
    expected_key: tuple[str, str],
) -> GraphDeleteReceipt:
    project, graph = expected_key
    return _delete_receipt_from_row(
        {
            "project": project,
            "graph": graph,
            "revision": row.get("result_revision"),
            "content_sha256": row.get("result_content_sha256"),
            "deleted_at": row.get("result_deleted_at"),
        },
        expected_key=expected_key,
    )


def _delete_receipt_from_row(
    row: PostgreSQLRow,
    *,
    expected_key: tuple[str, str],
) -> GraphDeleteReceipt:
    project, graph = _stored_key(row)
    if (project, graph) != expected_key:
        raise GraphStoreCorruptionError("The PostgreSQL delete receipt is addressed incorrectly.")
    return GraphDeleteReceipt(
        project=project,
        graph=graph,
        revision=_stored_revision(row.get("revision")),
        content_sha256=_stored_sha256(row.get("content_sha256")),
        deleted_at=_timestamp(_datetime_from_timestamp(row, "deleted_at")),
    )


def _stored_key(row: PostgreSQLRow) -> tuple[str, str]:
    project = row.get("project")
    graph = row.get("graph")
    try:
        return _validated_graph_key(project, graph)  # type: ignore[arg-type]
    except GraphStoreIdentifierError as error:
        raise GraphStoreCorruptionError("The PostgreSQL graph identity is invalid.") from error


def _stored_revision(value: object) -> str:
    try:
        return _validated_revision(value)  # type: ignore[arg-type]
    except GraphStoreIdentifierError as error:
        raise GraphStoreCorruptionError("The PostgreSQL graph revision is invalid.") from error


def _stored_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise GraphStoreCorruptionError("The PostgreSQL graph content digest is invalid.")
    return value


def _stored_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise GraphStoreCorruptionError("The PostgreSQL graph document bytes are invalid.")


def _idempotency_key_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _datetime_from_timestamp(row: PostgreSQLRow, field: str) -> datetime:
    value = row.get(field)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GraphStoreCorruptionError("The PostgreSQL graph timestamp is invalid.")
    return value


__all__ = ["PostgreSQLGraphStore"]
