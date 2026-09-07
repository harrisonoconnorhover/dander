"""Shared mutation policy for the ETag-based, journaled graph stores.

Only the identical create, put, and delete orchestration is shared here. S3, Azure Blob,
and OCI supply their existing conditional I/O and recovery methods; GCS and PostgreSQL
retain their different revision and transaction protocols.
"""

from __future__ import annotations

import hashlib
from abc import abstractmethod
from typing import TYPE_CHECKING, Literal, TypeVar

from pydantic import BaseModel

from dander.control.graph_store import (
    Clock,
    GraphDeleteReceipt,
    GraphRecord,
    GraphStore,
    GraphStoreAlreadyExistsError,
    GraphStoreConflictError,
    GraphStoreNotFoundError,
    _create_fingerprint,
    _delete_fingerprint,
    _timestamp,
    _validated_graph_key,
    _validated_idempotency_key,
    _validated_revision,
    canonicalize_graph_document,
)
from dander.control.object_graph_records import (
    _CreateJournal,
    _DeleteJournal,
    _GraphObjectMetadata,
    _StoredDeleteReceipt,
    _StoredGraph,
)

if TYPE_CHECKING:
    from dander.control.models import PipelineGraphDocument

_MAX_DELETE_JOURNAL_BYTES = 64 * 1024
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class _JournaledGraphMutations(GraphStore):
    """Keep validation and journal planning consistent across the three ETag adapters."""

    _max_graph_bytes: int
    _max_create_journal_bytes: int
    _clock: Clock

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
        key_sha256 = _key_sha256(idempotency_key)
        fingerprint = _create_fingerprint(project, graph, canonical.content_sha256)
        journal_name = self._journal_name(project, "create", key_sha256)
        loaded = self._read_model(journal_name, _CreateJournal, self._max_create_journal_bytes)
        if loaded is not None:
            self._validate_create_replay(loaded[0], project, graph, key_sha256, fingerprint)
            return self._resume_create(loaded[0], loaded[1])
        existing = self._read_graph(project, graph)
        if existing is not None and self._resolve_loaded_graph(*existing) is not None:
            raise GraphStoreAlreadyExistsError("The graph already exists.")
        now = _timestamp(self._clock())
        planned = _StoredGraph.from_canonical(
            project=project,
            graph=graph,
            canonical=canonical,
            created_at=now,
            updated_at=now,
            create_key_sha256=key_sha256,
            create_request_sha256=fingerprint,
        )
        journal = _CreateJournal(
            project=project,
            graph=graph,
            key_sha256=key_sha256,
            request_sha256=fingerprint,
            planned_graph=planned,
        )
        try:
            revision = self._write_model(journal_name, journal, expected_etag=None)
        except GraphStoreConflictError:
            loaded = self._read_model(journal_name, _CreateJournal, self._max_create_journal_bytes)
            if loaded is None:
                raise GraphStoreConflictError(
                    "The graph create could not be coordinated."
                ) from None
            self._validate_create_replay(loaded[0], project, graph, key_sha256, fingerprint)
            return self._resume_create(loaded[0], loaded[1])
        self._checkpoint("after_create_pending")
        return self._resume_create(journal, revision)

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
        resolved = self._load_resolved(project, graph)
        if resolved is None:
            raise GraphStoreNotFoundError("The graph does not exist.")
        current, stored, etag = resolved
        if current.revision != expected_revision:
            raise GraphStoreConflictError("The graph revision is stale.")
        replacement = _StoredGraph.from_canonical(
            project=project,
            graph=graph,
            canonical=canonical,
            created_at=current.created_at,
            updated_at=_timestamp(self._clock()),
            create_key_sha256=stored.create_key_sha256,
            create_request_sha256=stored.create_request_sha256,
        )
        new_etag = self._write_model(
            self._graph_name(project, graph),
            replacement,
            expected_etag=etag,
            object_metadata=_GraphObjectMetadata.from_stored(replacement),
        )
        return replacement.record(canonical, new_etag)

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
        key_sha256 = _key_sha256(idempotency_key)
        fingerprint = _delete_fingerprint(project, graph, expected_revision)
        journal_name = self._journal_name(project, "delete", key_sha256)
        loaded = self._read_model(journal_name, _DeleteJournal, _MAX_DELETE_JOURNAL_BYTES)
        if loaded is not None:
            self._validate_delete_replay(loaded[0], project, graph, key_sha256, fingerprint)
            return self._resume_delete(loaded[0], loaded[1])
        resolved = self._load_resolved(project, graph)
        if resolved is None:
            raise GraphStoreNotFoundError("The graph does not exist.")
        current = resolved[0]
        if current.revision != expected_revision:
            raise GraphStoreConflictError("The graph revision is stale.")
        receipt = GraphDeleteReceipt(
            project=project,
            graph=graph,
            revision=current.revision,
            content_sha256=current.content_sha256,
            deleted_at=_timestamp(self._clock()),
        )
        journal = _DeleteJournal(
            project=project,
            graph=graph,
            key_sha256=key_sha256,
            request_sha256=fingerprint,
            expected_revision=expected_revision,
            receipt=_StoredDeleteReceipt.from_receipt(receipt),
        )
        try:
            revision = self._write_model(journal_name, journal, expected_etag=None)
        except GraphStoreConflictError:
            loaded = self._read_model(journal_name, _DeleteJournal, _MAX_DELETE_JOURNAL_BYTES)
            if loaded is None:
                raise GraphStoreConflictError(
                    "The graph delete could not be coordinated."
                ) from None
            self._validate_delete_replay(loaded[0], project, graph, key_sha256, fingerprint)
            return self._resume_delete(loaded[0], loaded[1])
        self._checkpoint("after_delete_pending")
        return self._resume_delete(journal, revision)

    @abstractmethod
    def _read_model(
        self, name: str, model_type: type[_ModelT], max_bytes: int
    ) -> tuple[_ModelT, str, dict[str, str]] | None: ...

    @abstractmethod
    def _write_model(
        self,
        name: str,
        model: BaseModel,
        *,
        expected_etag: str | None,
        object_metadata: _GraphObjectMetadata | None = None,
    ) -> str: ...

    @abstractmethod
    def _read_graph(self, project: str, graph: str) -> tuple[_StoredGraph, str] | None: ...

    @abstractmethod
    def _load_resolved(
        self, project: str, graph: str
    ) -> tuple[GraphRecord, _StoredGraph, str] | None: ...

    @abstractmethod
    def _resolve_loaded_graph(
        self, stored: _StoredGraph, etag: str
    ) -> tuple[GraphRecord, _StoredGraph, str] | None: ...

    @abstractmethod
    def _graph_name(self, project: str, graph: str | None) -> str: ...

    @abstractmethod
    def _journal_name(
        self, project: str, operation: Literal["create", "delete"], key_sha256: str
    ) -> str: ...

    @staticmethod
    @abstractmethod
    def _validate_create_replay(
        journal: _CreateJournal, project: str, graph: str, key_sha256: str, fingerprint: str
    ) -> None: ...

    @staticmethod
    @abstractmethod
    def _validate_delete_replay(
        journal: _DeleteJournal, project: str, graph: str, key_sha256: str, fingerprint: str
    ) -> None: ...

    @abstractmethod
    def _resume_create(self, journal: _CreateJournal, journal_etag: str) -> GraphRecord: ...

    @abstractmethod
    def _resume_delete(self, journal: _DeleteJournal, journal_etag: str) -> GraphDeleteReceipt: ...

    @abstractmethod
    def _checkpoint(self, stage: str) -> None: ...


def _key_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
