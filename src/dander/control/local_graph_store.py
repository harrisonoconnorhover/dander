"""Root-confined durable local filesystem implementation of ``GraphStore``.

Graph and idempotency journal files are private implementation data. Callers select one root at
startup and can never supply paths per request. A pending/completed mutation journal makes exact
create/delete retries recoverable when the process stops between the resource mutation and the
idempotency outcome write.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from bisect import bisect_right
from pathlib import Path
from typing import Literal, Self, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from dander.control.graph_store import (
    MAX_GRAPH_DOCUMENT_BYTES,
    CanonicalGraphDocument,
    Clock,
    GraphDeleteReceipt,
    GraphPage,
    GraphRecord,
    GraphStore,
    GraphStoreAlreadyExistsError,
    GraphStoreConflictError,
    GraphStoreCorruptionError,
    GraphStoreIdempotencyConflictError,
    GraphStoreIdentifierError,
    GraphStoreNotFoundError,
    RevisionFactory,
    _canonical_json_bytes,
    _create_fingerprint,
    _decode_cursor,
    _delete_fingerprint,
    _encode_cursor,
    _opaque_revision,
    _timestamp,
    _utc_now,
    _validated_graph_key,
    _validated_idempotency_key,
    _validated_identifier,
    _validated_max_bytes,
    _validated_new_revision,
    _validated_page_size,
    _validated_revision,
    canonicalize_graph_document,
)
from dander.control.models import PipelineGraphDocument  # noqa: TC001 - Pydantic resolves it

_MAX_ENVELOPE_OVERHEAD = 256 * 1024
_MutationOperation = Literal["create", "delete"]
_MutationStatus = Literal["pending", "completed"]
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class _StoredGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    project: str
    graph: str
    document: PipelineGraphDocument
    revision: str = Field(min_length=1, max_length=512)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_canonical(
        cls,
        *,
        project: str,
        graph: str,
        canonical: CanonicalGraphDocument,
        revision: str,
        created_at: str,
        updated_at: str,
    ) -> _StoredGraph:
        return cls(
            project=project,
            graph=graph,
            document=canonical.document,
            revision=revision,
            content_sha256=canonical.content_sha256,
            created_at=created_at,
            updated_at=updated_at,
        )

    def record(self, canonical: CanonicalGraphDocument) -> GraphRecord:
        return GraphRecord(
            project=self.project,
            graph=self.graph,
            document=canonical.document,
            revision=self.revision,
            content_sha256=self.content_sha256,
            created_at=_timestamp(self.created_at),
            updated_at=_timestamp(self.updated_at),
        )


class _StoredDeleteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str
    graph: str
    revision: str = Field(min_length=1, max_length=512)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    deleted_at: AwareDatetime

    @classmethod
    def from_receipt(cls, receipt: GraphDeleteReceipt) -> _StoredDeleteReceipt:
        return cls(
            project=receipt.project,
            graph=receipt.graph,
            revision=receipt.revision,
            content_sha256=receipt.content_sha256,
            deleted_at=receipt.deleted_at,
        )

    def receipt(self) -> GraphDeleteReceipt:
        return GraphDeleteReceipt(
            project=self.project,
            graph=self.graph,
            revision=self.revision,
            content_sha256=self.content_sha256,
            deleted_at=_timestamp(self.deleted_at),
        )


class _MutationJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation: _MutationOperation
    status: _MutationStatus
    project: str
    graph: str
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    planned_graph: _StoredGraph | None = None
    expected_revision: str | None = None
    delete_receipt: _StoredDeleteReceipt | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if self.operation == "create":
            if (
                self.planned_graph is None
                or self.expected_revision is not None
                or self.delete_receipt is not None
            ):
                raise ValueError("create journal shape is invalid")
            if self.planned_graph.project != self.project or self.planned_graph.graph != self.graph:
                raise ValueError("create journal addressing is invalid")
        elif (
            self.planned_graph is not None
            or self.expected_revision is None
            or self.delete_receipt is None
        ):
            raise ValueError("delete journal shape is invalid")
        elif (
            self.delete_receipt.project != self.project
            or self.delete_receipt.graph != self.graph
            or self.delete_receipt.revision != self.expected_revision
        ):
            raise ValueError("delete journal addressing is invalid")
        return self


class RootedLocalGraphStore(GraphStore):
    """Persist graphs under one resolved root with atomic files and restart-safe mutations.

    The adapter is designed for one hosted-control process per root. Its lock coordinates all
    requests in that process; provider adapters later replace this local coordination with native
    conditional object operations.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_graph_bytes: int = MAX_GRAPH_DOCUMENT_BYTES,
        clock: Clock | None = None,
        revision_factory: RevisionFactory | None = None,
    ) -> None:
        root.expanduser().mkdir(parents=True, exist_ok=True)
        self._root = root.expanduser().resolve(strict=True)
        if not self._root.is_dir():
            raise GraphStoreCorruptionError("The local graph-store root is not a directory.")
        self._max_graph_bytes = _validated_max_bytes(max_graph_bytes)
        self._clock = clock or _utc_now
        self._revision_factory = revision_factory or _opaque_revision
        self._lock = threading.RLock()

    def list(self, project: str, *, cursor: str | None = None, limit: int = 50) -> GraphPage:
        project = _validated_identifier(project, "project")
        limit = _validated_page_size(limit)
        after = _decode_cursor(project, cursor)
        with self._lock:
            directory = self._graph_directory(project)
            if not directory.exists():
                return GraphPage(items=(), next_cursor=None)
            self._assert_safe_path(directory)
            if not directory.is_dir():
                raise GraphStoreCorruptionError("The local graph-store layout is invalid.")
            graph_ids: list[str] = []
            for path in directory.iterdir():
                if path.name.startswith("."):
                    continue
                if path.is_symlink():
                    raise GraphStoreCorruptionError("The local graph store contains a symlink.")
                if path.suffix != ".json" or not path.is_file():
                    raise GraphStoreCorruptionError("The local graph-store layout is invalid.")
                _, graph_id = _validated_graph_key(project, path.stem)
                graph_ids.append(graph_id)
            graph_ids.sort()
            start = bisect_right(graph_ids, after) if after is not None else 0
            selected = graph_ids[start : start + limit]
            items = tuple(self._load_required(project, graph).summary() for graph in selected)
            next_cursor = (
                _encode_cursor(project, selected[-1])
                if selected and start + len(selected) < len(graph_ids)
                else None
            )
            return GraphPage(items=items, next_cursor=next_cursor)

    def get(self, project: str, graph: str) -> GraphRecord:
        project, graph = _validated_graph_key(project, graph)
        with self._lock:
            return self._load_required(project, graph)

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
        canonical = canonicalize_graph_document(document, max_bytes=self._max_graph_bytes)
        fingerprint = _create_fingerprint(project, graph, canonical.content_sha256)
        with self._lock:
            journal = self._load_journal(project, "create", idempotency_key)
            if journal is not None:
                self._validate_replay(
                    journal,
                    project,
                    graph,
                    "create",
                    idempotency_key,
                    fingerprint,
                )
                return self._resume_create(journal)
            if self._load_optional(project, graph) is not None:
                raise GraphStoreAlreadyExistsError("The graph already exists.")
            now = _timestamp(self._clock())
            planned = _StoredGraph.from_canonical(
                project=project,
                graph=graph,
                canonical=canonical,
                revision=_validated_new_revision(self._revision_factory()),
                created_at=now,
                updated_at=now,
            )
            journal = _MutationJournal(
                operation="create",
                status="pending",
                project=project,
                graph=graph,
                key_sha256=_key_sha256(idempotency_key),
                request_sha256=fingerprint,
                planned_graph=planned,
            )
            self._write_journal(journal, idempotency_key)
            self._checkpoint("after_pending")
            return self._resume_create(journal)

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
        with self._lock:
            current = self._load_optional_envelope(project, graph)
            if current is None:
                raise GraphStoreNotFoundError("The graph does not exist.")
            if current.revision != expected_revision:
                raise GraphStoreConflictError("The graph revision is stale.")
            stored = _StoredGraph.from_canonical(
                project=project,
                graph=graph,
                canonical=canonical,
                revision=_validated_new_revision(self._revision_factory()),
                created_at=_timestamp(current.created_at),
                updated_at=_timestamp(self._clock()),
            )
            self._write_graph(stored)
            return stored.record(canonical)

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
        fingerprint = _delete_fingerprint(project, graph, expected_revision)
        with self._lock:
            journal = self._load_journal(project, "delete", idempotency_key)
            if journal is not None:
                self._validate_replay(
                    journal,
                    project,
                    graph,
                    "delete",
                    idempotency_key,
                    fingerprint,
                )
                return self._resume_delete(journal)
            current = self._load_optional(project, graph)
            if current is None:
                raise GraphStoreNotFoundError("The graph does not exist.")
            if current.revision != expected_revision:
                raise GraphStoreConflictError("The graph revision is stale.")
            receipt = GraphDeleteReceipt(
                project=project,
                graph=graph,
                revision=current.revision,
                content_sha256=current.content_sha256,
                deleted_at=_timestamp(self._clock()),
            )
            journal = _MutationJournal(
                operation="delete",
                status="pending",
                project=project,
                graph=graph,
                key_sha256=_key_sha256(idempotency_key),
                request_sha256=fingerprint,
                expected_revision=expected_revision,
                delete_receipt=_StoredDeleteReceipt.from_receipt(receipt),
            )
            self._write_journal(journal, idempotency_key)
            self._checkpoint("after_pending")
            return self._resume_delete(journal)

    def _resume_create(self, journal: _MutationJournal) -> GraphRecord:
        planned = journal.planned_graph
        if planned is None:
            raise GraphStoreCorruptionError("The local idempotency journal is invalid.")
        canonical = canonicalize_graph_document(
            planned.document,
            max_bytes=self._max_graph_bytes,
        )
        if canonical.content_sha256 != planned.content_sha256:
            raise GraphStoreCorruptionError("The local idempotency journal is inconsistent.")
        if journal.status == "completed":
            return planned.record(canonical)
        current = self._load_optional_envelope(journal.project, journal.graph)
        if current is None:
            self._write_graph(planned)
        elif current != planned:
            raise GraphStoreConflictError("The pending graph create cannot be recovered safely.")
        self._checkpoint("after_mutation")
        completed = journal.model_copy(update={"status": "completed"})
        self._write_journal_by_digest(completed, journal.key_sha256)
        self._checkpoint("after_completed")
        return planned.record(canonical)

    def _resume_delete(self, journal: _MutationJournal) -> GraphDeleteReceipt:
        stored_receipt = journal.delete_receipt
        expected_revision = journal.expected_revision
        if stored_receipt is None or expected_revision is None:
            raise GraphStoreCorruptionError("The local idempotency journal is invalid.")
        if journal.status == "completed":
            return stored_receipt.receipt()
        current = self._load_optional(journal.project, journal.graph)
        if current is not None:
            if current.revision != expected_revision:
                raise GraphStoreConflictError(
                    "The pending graph delete cannot be recovered safely."
                )
            self._delete_graph_file(journal.project, journal.graph)
        self._checkpoint("after_mutation")
        completed = journal.model_copy(update={"status": "completed"})
        self._write_journal_by_digest(completed, journal.key_sha256)
        self._checkpoint("after_completed")
        return stored_receipt.receipt()

    def _validate_replay(
        self,
        journal: _MutationJournal,
        project: str,
        graph: str,
        operation: _MutationOperation,
        idempotency_key: str,
        fingerprint: str,
    ) -> None:
        if (
            journal.project != project
            or journal.operation != operation
            or journal.key_sha256 != _key_sha256(idempotency_key)
        ):
            raise GraphStoreCorruptionError("The local idempotency journal is inconsistent.")
        if journal.request_sha256 != fingerprint:
            raise GraphStoreIdempotencyConflictError(
                "The idempotency key was already used for a different request."
            )
        if journal.graph != graph:
            raise GraphStoreCorruptionError("The local idempotency journal is inconsistent.")

    def _load_required(self, project: str, graph: str) -> GraphRecord:
        record = self._load_optional(project, graph)
        if record is None:
            raise GraphStoreNotFoundError("The graph does not exist.")
        return record

    def _load_optional(self, project: str, graph: str) -> GraphRecord | None:
        stored = self._load_optional_envelope(project, graph)
        if stored is None:
            return None
        canonical = canonicalize_graph_document(stored.document, max_bytes=self._max_graph_bytes)
        if canonical.content_sha256 != stored.content_sha256:
            raise GraphStoreCorruptionError("The local graph record has an invalid content hash.")
        return stored.record(canonical)

    def _load_optional_envelope(self, project: str, graph: str) -> _StoredGraph | None:
        path = self._graph_path(project, graph)
        if path.is_symlink():
            raise GraphStoreCorruptionError("The local graph store contains a symlink.")
        if not path.exists():
            return None
        stored = self._read_model(path, _StoredGraph)
        if stored.project != project or stored.graph != graph:
            raise GraphStoreCorruptionError("The local graph record is addressed incorrectly.")
        try:
            _validated_revision(stored.revision)
        except GraphStoreIdentifierError as error:
            raise GraphStoreCorruptionError(
                "The local graph record has an invalid revision."
            ) from error
        canonical = canonicalize_graph_document(stored.document, max_bytes=self._max_graph_bytes)
        if canonical.content_sha256 != stored.content_sha256:
            raise GraphStoreCorruptionError("The local graph record has an invalid content hash.")
        return stored

    def _load_journal(
        self,
        project: str,
        operation: _MutationOperation,
        idempotency_key: str,
    ) -> _MutationJournal | None:
        key_digest = _key_sha256(idempotency_key)
        path = self._journal_path(project, operation, key_digest)
        if path.is_symlink():
            raise GraphStoreCorruptionError("The local graph store contains a symlink.")
        if not path.exists():
            return None
        return self._read_model(path, _MutationJournal)

    def _write_graph(self, stored: _StoredGraph) -> None:
        self._atomic_write(
            self._graph_path(stored.project, stored.graph),
            _canonical_json_bytes(stored.model_dump(mode="json")),
        )

    def _delete_graph_file(self, project: str, graph: str) -> None:
        path = self._graph_path(project, graph)
        self._assert_safe_path(path)
        if path.is_symlink():
            raise GraphStoreCorruptionError("The local graph store contains a symlink.")
        try:
            path.unlink()
        except FileNotFoundError:
            return
        _sync_directory(path.parent)

    def _write_journal(self, journal: _MutationJournal, idempotency_key: str) -> None:
        self._write_journal_by_digest(journal, _key_sha256(idempotency_key))

    def _write_journal_by_digest(self, journal: _MutationJournal, key_digest: str) -> None:
        self._atomic_write(
            self._journal_path(journal.project, journal.operation, key_digest),
            _canonical_json_bytes(journal.model_dump(mode="json")),
        )

    def _read_model(self, path: Path, model: type[_ModelT]) -> _ModelT:
        self._assert_safe_path(path)
        try:
            if path.stat().st_size > self._max_graph_bytes + _MAX_ENVELOPE_OVERHEAD:
                raise GraphStoreCorruptionError("A local graph-store file exceeds its size bound.")
            return model.model_validate_json(path.read_bytes())
        except GraphStoreCorruptionError:
            raise
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
            raise GraphStoreCorruptionError("A local graph-store file is invalid.") from error

    def _atomic_write(self, path: Path, data: bytes) -> None:
        self._ensure_parent(path)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if path.is_symlink():
                raise GraphStoreCorruptionError("The local graph store contains a symlink.")
            os.replace(temporary, path)
            _sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _graph_directory(self, project: str) -> Path:
        path = self._root / "projects" / project / "graphs"
        self._assert_safe_path(path)
        return path

    def _graph_path(self, project: str, graph: str) -> Path:
        path = self._graph_directory(project) / f"{graph}.json"
        self._assert_safe_path(path)
        return path

    def _journal_path(
        self,
        project: str,
        operation: _MutationOperation,
        key_digest: str,
    ) -> Path:
        path = (
            self._root
            / ".dander-control"
            / "idempotency"
            / project
            / operation
            / f"{key_digest}.json"
        )
        self._assert_safe_path(path)
        return path

    def _ensure_parent(self, path: Path) -> None:
        self._assert_safe_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_safe_path(path)
        if not path.parent.is_dir():
            raise GraphStoreCorruptionError("The local graph-store layout is invalid.")

    def _assert_safe_path(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self._root)
        except ValueError as error:
            raise GraphStoreCorruptionError(
                "The local graph-store path escapes its root."
            ) from error
        current = path
        while current != self._root:
            if current.is_symlink():
                raise GraphStoreCorruptionError("The local graph store contains a symlink.")
            current = current.parent

    def _checkpoint(self, stage: str) -> None:
        """Test seam invoked after each durable mutation boundary."""


def _key_sha256(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _sync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
