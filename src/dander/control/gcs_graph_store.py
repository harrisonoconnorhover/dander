"""Google Cloud Storage implementation of the provider-neutral ``GraphStore`` contract.

The module itself imports no Google SDK. Constructing the adapter without an injected client is
the explicit provider boundary that lazily imports and creates the GCS client. Object generations
remain private implementation details and are exposed only as opaque GraphStore revisions.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Literal, Protocol, Self, TypeVar, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from dander.control.graph_store import (
    MAX_GRAPH_DOCUMENT_BYTES,
    MAX_GRAPH_PAGE_SIZE,
    CanonicalGraphDocument,
    Clock,
    GraphDeleteReceipt,
    GraphPage,
    GraphRecord,
    GraphStore,
    GraphStoreAlreadyExistsError,
    GraphStoreConflictError,
    GraphStoreCorruptionError,
    GraphStoreError,
    GraphStoreIdempotencyConflictError,
    GraphStoreNotFoundError,
    GraphSummary,
    _canonical_json_bytes,
    _create_fingerprint,
    _decode_cursor,
    _delete_fingerprint,
    _encode_cursor,
    _timestamp,
    _utc_now,
    _validated_graph_key,
    _validated_idempotency_key,
    _validated_identifier,
    _validated_max_bytes,
    _validated_page_size,
    _validated_revision,
    canonicalize_graph_document,
)
from dander.control.models import PipelineGraphDocument  # noqa: TC001 - Pydantic resolves it

if TYPE_CHECKING:
    import builtins
    from collections.abc import Iterable

_MAX_GRAPH_ENVELOPE_OVERHEAD = 256 * 1024
_MAX_JOURNAL_OVERHEAD = 256 * 1024
_MAX_DELETE_JOURNAL_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,510}[A-Za-z0-9]$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class _BlobPort(Protocol):
    name: str
    generation: int | None
    size: int | None
    metadata: dict[str, str] | None

    def reload(self, *, timeout: float | None = None) -> None: ...

    def download_as_bytes(
        self,
        *,
        start: int | None = None,
        end: int | None = None,
        if_generation_match: int | None = None,
        timeout: float | None = None,
    ) -> bytes: ...

    def upload_from_string(
        self,
        data: bytes,
        *,
        content_type: str,
        if_generation_match: int,
        timeout: float | None = None,
    ) -> None: ...

    def delete(
        self,
        *,
        if_generation_match: int,
        timeout: float | None = None,
    ) -> None: ...


class _BucketPort(Protocol):
    name: str

    def blob(self, blob_name: str) -> _BlobPort: ...


class _ClientPort(Protocol):
    def bucket(self, bucket_name: str) -> _BucketPort: ...

    def list_blobs(
        self,
        bucket_or_name: _BucketPort,
        *,
        max_results: int,
        prefix: str,
        start_offset: str | None,
        timeout: float | None = None,
    ) -> Iterable[_BlobPort]: ...


class _DeleteFence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: str = Field(min_length=1, max_length=512)


class _GraphObjectMetadata(BaseModel):
    """Safe bounded summary metadata stored beside one graph object body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    project: str
    graph: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    create_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    create_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    delete_key_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    delete_request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    delete_expected_revision: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_fence_shape(self) -> Self:
        values = (
            self.delete_key_sha256,
            self.delete_request_sha256,
            self.delete_expected_revision,
        )
        if any(value is None for value in values) and any(value is not None for value in values):
            raise ValueError("graph metadata delete fence is incomplete")
        return self

    @classmethod
    def from_stored(cls, stored: _StoredGraph) -> _GraphObjectMetadata:
        fence = stored.delete_fence
        return cls(
            project=stored.project,
            graph=stored.graph,
            content_sha256=stored.content_sha256,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
            create_key_sha256=stored.create_key_sha256,
            create_request_sha256=stored.create_request_sha256,
            delete_key_sha256=fence.key_sha256 if fence is not None else None,
            delete_request_sha256=fence.request_sha256 if fence is not None else None,
            delete_expected_revision=fence.expected_revision if fence is not None else None,
        )

    def summary(self, generation: int) -> GraphSummary:
        return GraphSummary(
            project=self.project,
            graph=self.graph,
            revision=str(generation),
            content_sha256=self.content_sha256,
            created_at=_timestamp(self.created_at),
            updated_at=_timestamp(self.updated_at),
        )


class _StoredGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    project: str
    graph: str
    document: PipelineGraphDocument
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    create_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    create_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    delete_fence: _DeleteFence | None = None

    @classmethod
    def from_canonical(
        cls,
        *,
        project: str,
        graph: str,
        canonical: CanonicalGraphDocument,
        created_at: str,
        updated_at: str,
        create_key_sha256: str,
        create_request_sha256: str,
    ) -> _StoredGraph:
        return cls(
            project=project,
            graph=graph,
            document=canonical.document,
            content_sha256=canonical.content_sha256,
            created_at=created_at,
            updated_at=updated_at,
            create_key_sha256=create_key_sha256,
            create_request_sha256=create_request_sha256,
        )

    def record(self, canonical: CanonicalGraphDocument, generation: int) -> GraphRecord:
        return GraphRecord(
            project=self.project,
            graph=self.graph,
            document=canonical.document,
            revision=str(generation),
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


class _CreateJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["pending", "completed"] = "pending"
    project: str
    graph: str
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    planned_graph: _StoredGraph
    result_revision: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if (self.status == "completed") != (self.result_revision is not None):
            raise ValueError("create journal status is inconsistent")
        if self.planned_graph.delete_fence is not None:
            raise ValueError("planned create graph cannot be fenced")
        if self.planned_graph.project != self.project or self.planned_graph.graph != self.graph:
            raise ValueError("create journal addressing is invalid")
        return self


class _DeleteJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["pending", "completed"] = "pending"
    project: str
    graph: str
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: str = Field(min_length=1, max_length=512)
    receipt: _StoredDeleteReceipt
    fence_generation: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if self.receipt.project != self.project or self.receipt.graph != self.graph:
            raise ValueError("delete journal addressing is invalid")
        if self.receipt.revision != self.expected_revision:
            raise ValueError("delete journal revision is invalid")
        if self.status == "completed" and self.fence_generation is None:
            raise ValueError("completed delete journal has no fence generation")
        return self


class GCSGraphStore(GraphStore):
    """Persist canonical graph envelopes in one explicitly bound GCS bucket and prefix."""

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "dander-control/v1",
        client: _ClientPort | None = None,
        not_found_errors: tuple[type[BaseException], ...] = (),
        precondition_errors: tuple[type[BaseException], ...] = (),
        max_graph_bytes: int = MAX_GRAPH_DOCUMENT_BYTES,
        clock: Clock | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(bucket, str) or not bucket or "/" in bucket:
            raise GraphStoreCorruptionError("The GCS graph-store bucket binding is invalid.")
        prefix = prefix.strip("/")
        if (
            not prefix
            or _PREFIX.fullmatch(prefix) is None
            or any(part in {"", ".", ".."} for part in prefix.split("/"))
        ):
            raise GraphStoreCorruptionError("The GCS graph-store prefix binding is invalid.")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise GraphStoreCorruptionError("The GCS graph-store timeout is invalid.")
        if client is None:
            from google.api_core.exceptions import NotFound, PreconditionFailed
            from google.cloud.storage import Client  # type: ignore[import-untyped]

            client = cast("_ClientPort", Client())
            not_found_errors = (NotFound,)
            precondition_errors = (PreconditionFailed,)
        self._client = client
        self._bucket = client.bucket(bucket)
        self._bucket_name = bucket
        self._prefix = prefix
        self._not_found_errors = not_found_errors
        self._precondition_errors = precondition_errors
        self._max_graph_bytes = _validated_max_bytes(max_graph_bytes)
        self._max_graph_envelope_bytes = max_graph_bytes + _MAX_GRAPH_ENVELOPE_OVERHEAD
        self._max_create_journal_bytes = max_graph_bytes + _MAX_JOURNAL_OVERHEAD
        self._clock = clock or _utc_now
        self._timeout = float(timeout_seconds)

    @property
    def bucket_name(self) -> str:
        """Return the immutable bucket binding without provider client details."""
        return self._bucket_name

    @property
    def prefix(self) -> str:
        """Return the immutable object-prefix binding."""
        return self._prefix

    def list(self, project: str, *, cursor: str | None = None, limit: int = 50) -> GraphPage:
        project = _validated_identifier(project, "project")
        limit = _validated_page_size(limit)
        after = _decode_cursor(project, cursor)
        graph_prefix = self._graph_prefix(project)
        start_name = self._graph_name(project, after) if after is not None else None
        items: builtins.list[GraphSummary] = []
        exhausted = False
        while len(items) <= limit and not exhausted:
            max_results = min(MAX_GRAPH_PAGE_SIZE + 2, limit + 2)
            blobs = self._list_blobs(
                prefix=graph_prefix,
                start_offset=start_name,
                max_results=max_results,
            )
            if not blobs:
                break
            exhausted = len(blobs) < max_results
            last_seen = start_name
            for blob in blobs:
                if blob.name == start_name:
                    continue
                last_seen = blob.name
                graph = self._graph_from_name(project, blob.name)
                summary = self._summary_from_blob(blob, project, graph)
                if summary is not None:
                    items.append(summary)
                    if len(items) > limit:
                        break
            if len(items) > limit or exhausted:
                break
            if last_seen is None or last_seen == start_name:
                break
            start_name = last_seen
        selected = tuple(items[:limit])
        next_cursor = _encode_cursor(project, selected[-1].graph) if len(items) > limit else None
        return GraphPage(items=selected, next_cursor=next_cursor)

    def get(self, project: str, graph: str) -> GraphRecord:
        project, graph = _validated_graph_key(project, graph)
        resolved = self._load_resolved(project, graph)
        if resolved is None:
            raise GraphStoreNotFoundError("The graph does not exist.")
        return resolved[0]

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
            generation = self._write_model(journal_name, journal, if_generation_match=0)
        except GraphStoreConflictError:
            loaded = self._read_model(journal_name, _CreateJournal, self._max_create_journal_bytes)
            if loaded is None:
                raise GraphStoreConflictError(
                    "The graph create could not be coordinated."
                ) from None
            self._validate_create_replay(loaded[0], project, graph, key_sha256, fingerprint)
            return self._resume_create(loaded[0], loaded[1])
        self._checkpoint("after_create_pending")
        return self._resume_create(journal, generation)

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
        current, stored, generation = resolved
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
        new_generation = self._write_model(
            self._graph_name(project, graph),
            replacement,
            if_generation_match=generation,
            object_metadata=_GraphObjectMetadata.from_stored(replacement),
        )
        return replacement.record(canonical, new_generation)

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
            generation = self._write_model(journal_name, journal, if_generation_match=0)
        except GraphStoreConflictError:
            loaded = self._read_model(journal_name, _DeleteJournal, _MAX_DELETE_JOURNAL_BYTES)
            if loaded is None:
                raise GraphStoreConflictError(
                    "The graph delete could not be coordinated."
                ) from None
            self._validate_delete_replay(loaded[0], project, graph, key_sha256, fingerprint)
            return self._resume_delete(loaded[0], loaded[1])
        self._checkpoint("after_delete_pending")
        return self._resume_delete(journal, generation)

    def _resume_create(self, journal: _CreateJournal, journal_generation: int) -> GraphRecord:
        for _ in range(8):
            canonical = canonicalize_graph_document(
                journal.planned_graph.document,
                max_bytes=self._max_graph_bytes,
            )
            if canonical.content_sha256 != journal.planned_graph.content_sha256:
                raise GraphStoreCorruptionError("The GCS create journal is inconsistent.")
            if journal.status == "completed":
                if journal.result_revision is None:
                    raise GraphStoreCorruptionError("The GCS create journal is invalid.")
                return journal.planned_graph.record(
                    canonical,
                    _generation(journal.result_revision),
                )
            loaded = self._read_graph(journal.project, journal.graph)
            if loaded is None:
                try:
                    generation = self._write_model(
                        self._graph_name(journal.project, journal.graph),
                        journal.planned_graph,
                        if_generation_match=0,
                        object_metadata=_GraphObjectMetadata.from_stored(journal.planned_graph),
                    )
                except GraphStoreConflictError:
                    loaded = self._read_graph(journal.project, journal.graph)
                    if loaded is None:
                        raise GraphStoreConflictError(
                            "The graph create could not be recovered."
                        ) from None
                else:
                    self._checkpoint("after_graph_create")
                    loaded = (journal.planned_graph, generation)
            current, generation = loaded
            if (
                current.create_key_sha256 != journal.key_sha256
                or current.create_request_sha256 != journal.request_sha256
            ):
                self._delete_object(
                    self._journal_name(journal.project, "create", journal.key_sha256),
                    journal_generation,
                )
                raise GraphStoreAlreadyExistsError("The graph already exists.")
            if current != journal.planned_graph:
                journal, journal_generation = self._reload_create_journal(journal)
                if journal.status == "completed":
                    continue
                raise GraphStoreCorruptionError("The pending GCS create graph is inconsistent.")
            completed = journal.model_copy(
                update={"status": "completed", "result_revision": str(generation)}
            )
            try:
                self._write_model(
                    self._journal_name(journal.project, "create", journal.key_sha256),
                    completed,
                    if_generation_match=journal_generation,
                )
            except GraphStoreConflictError:
                journal, journal_generation = self._reload_create_journal(journal)
                continue
            self._checkpoint("after_create_completed")
            return current.record(canonical, generation)
        raise GraphStoreConflictError("The graph create did not converge.")

    def _reload_create_journal(
        self,
        expected: _CreateJournal,
    ) -> tuple[_CreateJournal, int]:
        loaded = self._read_model(
            self._journal_name(expected.project, "create", expected.key_sha256),
            _CreateJournal,
            self._max_create_journal_bytes,
        )
        if loaded is None:
            raise GraphStoreConflictError("The GCS create journal disappeared.")
        self._validate_create_replay(
            loaded[0],
            expected.project,
            expected.graph,
            expected.key_sha256,
            expected.request_sha256,
        )
        return loaded

    def _resume_delete(
        self,
        journal: _DeleteJournal,
        journal_generation: int,
    ) -> GraphDeleteReceipt:
        for _ in range(12):
            if journal.status == "completed":
                return journal.receipt.receipt()
            loaded = self._read_graph(journal.project, journal.graph)
            if journal.fence_generation is not None:
                if loaded is not None:
                    current, generation = loaded
                    if generation == journal.fence_generation:
                        if not self._owns_fence(current, journal):
                            raise GraphStoreCorruptionError("The GCS delete fence is inconsistent.")
                        try:
                            self._delete_object(
                                self._graph_name(journal.project, journal.graph),
                                generation,
                            )
                        except GraphStoreConflictError:
                            continue
                        self._checkpoint("after_graph_delete")
                    elif current.delete_fence is not None:
                        self._resolve_loaded_graph(current, generation)
                return self._complete_delete(journal, journal_generation)
            if loaded is None:
                try:
                    self._delete_object(
                        self._journal_name(journal.project, "delete", journal.key_sha256),
                        journal_generation,
                    )
                except GraphStoreConflictError:
                    journal, journal_generation = self._reload_delete_journal(journal)
                    continue
                raise GraphStoreNotFoundError("The graph does not exist.")
            current, generation = loaded
            if current.delete_fence is not None:
                if not self._owns_fence(current, journal):
                    self._resolve_loaded_graph(current, generation)
                    try:
                        self._delete_object(
                            self._journal_name(journal.project, "delete", journal.key_sha256),
                            journal_generation,
                        )
                    except GraphStoreConflictError:
                        journal, journal_generation = self._reload_delete_journal(journal)
                        continue
                    raise GraphStoreNotFoundError("The graph does not exist.")
                fence_generation = generation
            else:
                if str(generation) != journal.expected_revision:
                    try:
                        self._delete_object(
                            self._journal_name(journal.project, "delete", journal.key_sha256),
                            journal_generation,
                        )
                    except GraphStoreConflictError:
                        journal, journal_generation = self._reload_delete_journal(journal)
                        continue
                    raise GraphStoreConflictError("The graph revision is stale.")
                fenced = current.model_copy(
                    update={
                        "delete_fence": _DeleteFence(
                            key_sha256=journal.key_sha256,
                            request_sha256=journal.request_sha256,
                            expected_revision=journal.expected_revision,
                        )
                    }
                )
                try:
                    fence_generation = self._write_model(
                        self._graph_name(journal.project, journal.graph),
                        fenced,
                        if_generation_match=generation,
                        object_metadata=_GraphObjectMetadata.from_stored(fenced),
                    )
                except GraphStoreConflictError:
                    continue
                self._checkpoint("after_delete_fence")
            with_fence = journal.model_copy(update={"fence_generation": fence_generation})
            try:
                new_journal_generation = self._write_model(
                    self._journal_name(journal.project, "delete", journal.key_sha256),
                    with_fence,
                    if_generation_match=journal_generation,
                )
            except GraphStoreConflictError:
                journal, journal_generation = self._reload_delete_journal(journal)
                continue
            journal = with_fence
            journal_generation = new_journal_generation
        raise GraphStoreConflictError("The graph delete did not converge.")

    def _complete_delete(
        self,
        journal: _DeleteJournal,
        journal_generation: int,
    ) -> GraphDeleteReceipt:
        for _ in range(8):
            if journal.status == "completed":
                return journal.receipt.receipt()
            completed = journal.model_copy(update={"status": "completed"})
            try:
                self._write_model(
                    self._journal_name(journal.project, "delete", journal.key_sha256),
                    completed,
                    if_generation_match=journal_generation,
                )
            except GraphStoreConflictError:
                journal, journal_generation = self._reload_delete_journal(journal)
                continue
            self._checkpoint("after_delete_completed")
            return journal.receipt.receipt()
        raise GraphStoreConflictError("The graph delete completion did not converge.")

    def _reload_delete_journal(
        self,
        expected: _DeleteJournal,
    ) -> tuple[_DeleteJournal, int]:
        loaded = self._read_model(
            self._journal_name(expected.project, "delete", expected.key_sha256),
            _DeleteJournal,
            _MAX_DELETE_JOURNAL_BYTES,
        )
        if loaded is None:
            raise GraphStoreConflictError("The GCS delete journal disappeared.")
        self._validate_delete_replay(
            loaded[0],
            expected.project,
            expected.graph,
            expected.key_sha256,
            expected.request_sha256,
        )
        return loaded

    def _load_resolved(
        self,
        project: str,
        graph: str,
    ) -> tuple[GraphRecord, _StoredGraph, int] | None:
        loaded = self._read_graph(project, graph)
        if loaded is None:
            return None
        return self._resolve_loaded_graph(*loaded)

    def _summary_from_blob(
        self,
        blob: _BlobPort,
        project: str,
        graph: str,
    ) -> GraphSummary | None:
        for _ in range(2):
            metadata_and_generation = self._graph_metadata_from_blob(blob, project, graph)
            if metadata_and_generation is None:
                return None
            metadata, generation = metadata_and_generation
            if metadata.delete_key_sha256 is None:
                return metadata.summary(generation)
            loaded = self._read_blob_model(blob, _StoredGraph, self._max_graph_envelope_bytes)
            if loaded is None:
                return None
            stored, loaded_generation = loaded
            self._validate_graph_address(stored, project, graph)
            if loaded_generation != generation:
                blob = self._bucket.blob(blob.name)
                continue
            if metadata != _GraphObjectMetadata.from_stored(stored):
                raise GraphStoreCorruptionError(
                    "The GCS graph metadata does not match its document."
                )
            resolved = self._resolve_loaded_graph(stored, generation)
            return resolved[0].summary() if resolved is not None else None
        raise GraphStoreConflictError("The GCS graph changed during listing.")

    def _resolve_loaded_graph(
        self,
        stored: _StoredGraph,
        generation: int,
    ) -> tuple[GraphRecord, _StoredGraph, int] | None:
        if stored.delete_fence is not None:
            loaded = self._read_model(
                self._journal_name(stored.project, "delete", stored.delete_fence.key_sha256),
                _DeleteJournal,
                _MAX_DELETE_JOURNAL_BYTES,
            )
            if loaded is None:
                raise GraphStoreCorruptionError("The GCS graph has an orphaned delete fence.")
            self._resume_delete(loaded[0], loaded[1])
            return None
        create_journal = self._read_model(
            self._journal_name(stored.project, "create", stored.create_key_sha256),
            _CreateJournal,
            self._max_create_journal_bytes,
        )
        if create_journal is None:
            raise GraphStoreCorruptionError("The GCS graph has no create journal.")
        self._validate_create_replay(
            create_journal[0],
            stored.project,
            stored.graph,
            stored.create_key_sha256,
            stored.create_request_sha256,
        )
        if create_journal[0].status == "pending":
            self._resume_create(create_journal[0], create_journal[1])
        canonical = canonicalize_graph_document(stored.document, max_bytes=self._max_graph_bytes)
        if canonical.content_sha256 != stored.content_sha256:
            raise GraphStoreCorruptionError("The GCS graph record has an invalid content hash.")
        return stored.record(canonical, generation), stored, generation

    def _read_graph(self, project: str, graph: str) -> tuple[_StoredGraph, int] | None:
        blob = self._bucket.blob(self._graph_name(project, graph))
        loaded = self._read_blob_model(
            blob,
            _StoredGraph,
            self._max_graph_envelope_bytes,
        )
        if loaded is not None:
            self._validate_graph_address(loaded[0], project, graph)
            metadata = self._graph_metadata_from_blob(blob, project, graph)
            if metadata is None or metadata[1] != loaded[1]:
                raise GraphStoreConflictError("The GCS graph changed during the read.")
            if metadata[0] != _GraphObjectMetadata.from_stored(loaded[0]):
                raise GraphStoreCorruptionError(
                    "The GCS graph metadata does not match its document."
                )
        return loaded

    def _graph_metadata_from_blob(
        self,
        blob: _BlobPort,
        project: str,
        graph: str,
    ) -> tuple[_GraphObjectMetadata, int] | None:
        try:
            if blob.generation is None or blob.size is None or blob.metadata is None:
                blob.reload(timeout=self._timeout)
            generation = _checked_generation(blob.generation)
            if (
                not isinstance(blob.size, int)
                or blob.size < 0
                or blob.size > self._max_graph_envelope_bytes
            ):
                raise GraphStoreCorruptionError("A GCS graph-store object exceeds its bound.")
            raw_metadata = blob.metadata
            if not isinstance(raw_metadata, dict):
                raise GraphStoreCorruptionError("A GCS graph-store object has no metadata.")
            metadata = _GraphObjectMetadata.model_validate(raw_metadata)
            if metadata.project != project or metadata.graph != graph:
                raise GraphStoreCorruptionError("The GCS graph metadata is addressed incorrectly.")
            return metadata, generation
        except Exception as error:
            if self._is_not_found(error):
                return None
            if isinstance(error, GraphStoreError):
                raise
            if isinstance(error, ValidationError):
                raise GraphStoreCorruptionError(
                    "A GCS graph-store object has invalid metadata."
                ) from error
            raise GraphStoreError("The GCS graph-store metadata read failed.") from error

    @staticmethod
    def _validate_graph_address(stored: _StoredGraph, project: str, graph: str) -> None:
        if stored.project != project or stored.graph != graph:
            raise GraphStoreCorruptionError("The GCS graph record is addressed incorrectly.")

    @staticmethod
    def _owns_fence(stored: _StoredGraph, journal: _DeleteJournal) -> bool:
        return stored.delete_fence == _DeleteFence(
            key_sha256=journal.key_sha256,
            request_sha256=journal.request_sha256,
            expected_revision=journal.expected_revision,
        )

    @staticmethod
    def _validate_create_replay(
        journal: _CreateJournal,
        project: str,
        graph: str,
        key_sha256: str,
        fingerprint: str,
    ) -> None:
        if journal.project != project or journal.key_sha256 != key_sha256:
            raise GraphStoreCorruptionError("The GCS create journal is addressed incorrectly.")
        if journal.graph != graph or journal.request_sha256 != fingerprint:
            raise GraphStoreIdempotencyConflictError(
                "The idempotency key was already used for a different request."
            )

    @staticmethod
    def _validate_delete_replay(
        journal: _DeleteJournal,
        project: str,
        graph: str,
        key_sha256: str,
        fingerprint: str,
    ) -> None:
        if journal.project != project or journal.key_sha256 != key_sha256:
            raise GraphStoreCorruptionError("The GCS delete journal is addressed incorrectly.")
        if journal.graph != graph or journal.request_sha256 != fingerprint:
            raise GraphStoreIdempotencyConflictError(
                "The idempotency key was already used for a different request."
            )

    def _read_model(
        self,
        name: str,
        model_type: type[_ModelT],
        max_bytes: int,
    ) -> tuple[_ModelT, int] | None:
        return self._read_blob_model(self._bucket.blob(name), model_type, max_bytes)

    def _read_blob_model(
        self,
        blob: _BlobPort,
        model_type: type[_ModelT],
        max_bytes: int,
    ) -> tuple[_ModelT, int] | None:
        for _ in range(2):
            try:
                if blob.generation is None or blob.size is None:
                    blob.reload(timeout=self._timeout)
                generation = _checked_generation(blob.generation)
                size = blob.size
                if not isinstance(size, int) or size < 0 or size > max_bytes:
                    raise GraphStoreCorruptionError("A GCS graph-store object exceeds its bound.")
                data = blob.download_as_bytes(
                    start=0,
                    end=max_bytes,
                    if_generation_match=generation,
                    timeout=self._timeout,
                )
                if len(data) > max_bytes:
                    raise GraphStoreCorruptionError("A GCS graph-store object exceeds its bound.")
                return model_type.model_validate_json(data), generation
            except Exception as error:
                if self._is_not_found(error):
                    return None
                if self._is_precondition(error):
                    blob = self._bucket.blob(blob.name)
                    continue
                if isinstance(error, GraphStoreError):
                    raise
                if isinstance(error, ValidationError):
                    raise GraphStoreCorruptionError(
                        "A GCS graph-store object is invalid."
                    ) from error
                raise GraphStoreError("The GCS graph-store read failed.") from error
        raise GraphStoreConflictError("The GCS graph-store object changed during the read.")

    def _write_model(
        self,
        name: str,
        model: BaseModel,
        *,
        if_generation_match: int,
        object_metadata: _GraphObjectMetadata | None = None,
    ) -> int:
        data = _canonical_json_bytes(model.model_dump(mode="json"))
        blob = self._bucket.blob(name)
        blob.metadata = (
            cast(
                "dict[str, str]",
                object_metadata.model_dump(mode="json", exclude_none=True),
            )
            if object_metadata is not None
            else None
        )
        try:
            blob.upload_from_string(
                data,
                content_type="application/json",
                if_generation_match=if_generation_match,
                timeout=self._timeout,
            )
            if blob.generation is None:
                blob.reload(timeout=self._timeout)
            return _checked_generation(blob.generation)
        except Exception as error:
            if self._is_precondition(error):
                raise GraphStoreConflictError("The GCS graph-store precondition failed.") from error
            if isinstance(error, GraphStoreError):
                raise
            raise GraphStoreError("The GCS graph-store write failed.") from error

    def _delete_object(self, name: str, generation: int) -> None:
        try:
            self._bucket.blob(name).delete(
                if_generation_match=generation,
                timeout=self._timeout,
            )
        except Exception as error:
            if self._is_not_found(error) or self._is_precondition(error):
                raise GraphStoreConflictError(
                    "The GCS graph-store delete precondition failed."
                ) from error
            raise GraphStoreError("The GCS graph-store delete failed.") from error

    def _list_blobs(
        self,
        *,
        prefix: str,
        start_offset: str | None,
        max_results: int,
    ) -> builtins.list[_BlobPort]:
        try:
            return list(
                self._client.list_blobs(
                    self._bucket,
                    prefix=prefix,
                    start_offset=start_offset,
                    max_results=max_results,
                    timeout=self._timeout,
                )
            )
        except Exception as error:
            raise GraphStoreError("The GCS graph-store list failed.") from error

    def _is_not_found(self, error: BaseException) -> bool:
        return bool(self._not_found_errors) and isinstance(error, self._not_found_errors)

    def _is_precondition(self, error: BaseException) -> bool:
        return bool(self._precondition_errors) and isinstance(error, self._precondition_errors)

    def _graph_prefix(self, project: str) -> str:
        return f"{self._prefix}/projects/{project}/graphs/"

    def _graph_name(self, project: str, graph: str | None) -> str:
        if graph is None:
            return self._graph_prefix(project)
        return f"{self._graph_prefix(project)}{graph}.json"

    def _graph_from_name(self, project: str, name: str) -> str:
        prefix = self._graph_prefix(project)
        if not name.startswith(prefix) or not name.endswith(".json"):
            raise GraphStoreCorruptionError("The GCS graph-store object layout is invalid.")
        graph = name[len(prefix) : -5]
        return _validated_identifier(graph, "graph")

    def _journal_name(
        self,
        project: str,
        operation: Literal["create", "delete"],
        key_sha256: str,
    ) -> str:
        return f"{self._prefix}/idempotency/{project}/{operation}/{key_sha256}.json"

    def _checkpoint(self, stage: str) -> None:
        """Test seam invoked after each durable object mutation boundary."""


def _key_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _checked_generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GraphStoreCorruptionError("A GCS object has an invalid generation.")
    return value


def _generation(value: str) -> int:
    _validated_revision(value)
    try:
        generation = int(value)
    except ValueError as error:
        raise GraphStoreCorruptionError("A GCS journal has an invalid generation.") from error
    return _checked_generation(generation)
