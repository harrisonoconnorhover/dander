"""Oracle Cloud Infrastructure Object Storage implementation of ``GraphStore``.

The module itself imports no OCI SDK. Default construction lazily creates a resource-principal
client; profile and security-token authentication remain available only through client injection.
Object ETags stay private implementation details and surface only as opaque GraphStore revisions.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
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

_MAX_GRAPH_ENVELOPE_OVERHEAD = 256 * 1024
_MAX_JOURNAL_OVERHEAD = 256 * 1024
_MAX_DELETE_JOURNAL_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,510}[A-Za-z0-9]$")
_OCI_BINDING = re.compile(r"^[A-Za-z0-9._-]{1,256}$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class _BodyPort(Protocol):
    def read(self, amount: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class _StreamResponsePort(Protocol):
    raw: _BodyPort

    def close(self) -> None: ...


class _ResponseDataPort(Protocol):
    data: object


class _ListObjectPort(Protocol):
    name: object


class _ListDataPort(Protocol):
    objects: object
    next_start_with: object


class _ClientPort(Protocol):
    def put_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        put_object_body: bytes,
        **kwargs: object,
    ) -> object: ...

    def head_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        **kwargs: object,
    ) -> object: ...

    def get_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        **kwargs: object,
    ) -> object: ...

    def delete_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        **kwargs: object,
    ) -> object: ...

    def list_objects(
        self,
        namespace_name: str,
        bucket_name: str,
        **kwargs: object,
    ) -> object: ...


@dataclass(frozen=True)
class _ObjectHead:
    etag: str
    size: int
    metadata: dict[str, str]


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

    def summary(self, etag: str) -> GraphSummary:
        return GraphSummary(
            project=self.project,
            graph=self.graph,
            revision=etag,
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

    def record(self, canonical: CanonicalGraphDocument, etag: str) -> GraphRecord:
        return GraphRecord(
            project=self.project,
            graph=self.graph,
            document=canonical.document,
            revision=etag,
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
    fence_revision: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if self.receipt.project != self.project or self.receipt.graph != self.graph:
            raise ValueError("delete journal addressing is invalid")
        if self.receipt.revision != self.expected_revision:
            raise ValueError("delete journal revision is invalid")
        if self.status == "completed" and self.fence_revision is None:
            raise ValueError("completed delete journal has no fence revision")
        return self


class OCIObjectGraphStore(GraphStore):
    """Persist canonical graph envelopes in one OCI Object Storage namespace and bucket."""

    def __init__(
        self,
        namespace: str,
        bucket: str,
        *,
        prefix: str = "dander-control/v1",
        client: _ClientPort | None = None,
        max_graph_bytes: int = MAX_GRAPH_DOCUMENT_BYTES,
        clock: Clock | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(namespace, str) or _OCI_BINDING.fullmatch(namespace) is None:
            raise GraphStoreCorruptionError("The OCI Object Storage namespace binding is invalid.")
        if not isinstance(bucket, str) or _OCI_BINDING.fullmatch(bucket) is None:
            raise GraphStoreCorruptionError("The OCI Object Storage bucket binding is invalid.")
        prefix = prefix.strip("/")
        if (
            not prefix
            or _PREFIX.fullmatch(prefix) is None
            or any(part in {"", ".", ".."} for part in prefix.split("/"))
        ):
            raise GraphStoreCorruptionError(
                "The OCI Object Storage graph-store prefix binding is invalid."
            )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise GraphStoreCorruptionError(
                "The OCI Object Storage graph-store timeout is invalid."
            )
        if client is None:
            import oci  # type: ignore
            from oci.auth.signers import get_resource_principals_signer  # type: ignore

            signer = get_resource_principals_signer()
            client = cast(
                "_ClientPort",
                oci.object_storage.ObjectStorageClient(
                    {},
                    signer=signer,
                    timeout=(float(timeout_seconds), float(timeout_seconds)),
                ),
            )
        self._client = client
        self._namespace = namespace
        self._bucket_name = bucket
        self._prefix = prefix
        self._max_graph_bytes = _validated_max_bytes(max_graph_bytes)
        self._max_graph_envelope_bytes = self._max_graph_bytes + _MAX_GRAPH_ENVELOPE_OVERHEAD
        self._max_create_journal_bytes = self._max_graph_bytes + _MAX_JOURNAL_OVERHEAD
        self._clock = clock or _utc_now

    @property
    def namespace(self) -> str:
        """Return the immutable Object Storage namespace binding."""
        return self._namespace

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
        start_after = self._graph_name(project, after) if after is not None else None
        provider_start: str | None = None
        items: builtins.list[GraphSummary] = []
        for _ in range(MAX_GRAPH_PAGE_SIZE + 2):
            names, next_start = self._list_entries(
                prefix=graph_prefix,
                start_after=start_after if provider_start is None else None,
                start=provider_start,
                max_keys=min(1000, MAX_GRAPH_PAGE_SIZE + 2, limit + 2),
            )
            if not names:
                if next_start is not None:
                    raise GraphStoreCorruptionError(
                        "The OCI Object Storage graph-store page is invalid."
                    )
                break
            for name in names:
                graph = self._graph_from_name(project, name)
                summary = self._summary_from_key(name, project, graph)
                if summary is not None:
                    items.append(summary)
                    if len(items) > limit:
                        break
            if len(items) > limit or next_start is None:
                break
            if next_start in {provider_start, start_after} or next_start <= names[-1]:
                raise GraphStoreCorruptionError(
                    "The OCI Object Storage graph-store page did not advance."
                )
            start_after = None
            provider_start = next_start
        else:
            raise GraphStoreConflictError(
                "The OCI Object Storage graph-store list did not converge."
            )
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

    def _resume_create(self, journal: _CreateJournal, journal_etag: str) -> GraphRecord:
        for _ in range(8):
            canonical = canonicalize_graph_document(
                journal.planned_graph.document,
                max_bytes=self._max_graph_bytes,
            )
            if canonical.content_sha256 != journal.planned_graph.content_sha256:
                raise GraphStoreCorruptionError(
                    "The OCI Object Storage create journal is inconsistent."
                )
            if journal.status == "completed":
                if journal.result_revision is None:
                    raise GraphStoreCorruptionError(
                        "The OCI Object Storage create journal is invalid."
                    )
                return journal.planned_graph.record(
                    canonical,
                    _checked_etag(journal.result_revision),
                )
            loaded = self._read_graph(journal.project, journal.graph)
            if loaded is None:
                try:
                    etag = self._write_model(
                        self._graph_name(journal.project, journal.graph),
                        journal.planned_graph,
                        expected_etag=None,
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
                    loaded = (journal.planned_graph, etag)
            current, etag = loaded
            if (
                current.create_key_sha256 != journal.key_sha256
                or current.create_request_sha256 != journal.request_sha256
            ):
                self._delete_object(
                    self._journal_name(journal.project, "create", journal.key_sha256),
                    journal_etag,
                )
                raise GraphStoreAlreadyExistsError("The graph already exists.")
            if current != journal.planned_graph:
                journal, journal_etag = self._reload_create_journal(journal)
                if journal.status == "completed":
                    continue
                raise GraphStoreCorruptionError(
                    "The pending OCI Object Storage create graph is inconsistent."
                )
            completed = journal.model_copy(update={"status": "completed", "result_revision": etag})
            try:
                self._write_model(
                    self._journal_name(journal.project, "create", journal.key_sha256),
                    completed,
                    expected_etag=journal_etag,
                )
            except GraphStoreConflictError:
                journal, journal_etag = self._reload_create_journal(journal)
                continue
            self._checkpoint("after_create_completed")
            return current.record(canonical, etag)
        raise GraphStoreConflictError("The graph create did not converge.")

    def _reload_create_journal(
        self,
        expected: _CreateJournal,
    ) -> tuple[_CreateJournal, str]:
        loaded = self._read_model(
            self._journal_name(expected.project, "create", expected.key_sha256),
            _CreateJournal,
            self._max_create_journal_bytes,
        )
        if loaded is None:
            raise GraphStoreConflictError("The OCI Object Storage create journal disappeared.")
        self._validate_create_replay(
            loaded[0],
            expected.project,
            expected.graph,
            expected.key_sha256,
            expected.request_sha256,
        )
        return loaded[0], loaded[1]

    def _resume_delete(
        self,
        journal: _DeleteJournal,
        journal_etag: str,
    ) -> GraphDeleteReceipt:
        for _ in range(12):
            if journal.status == "completed":
                return journal.receipt.receipt()
            loaded = self._read_graph(journal.project, journal.graph)
            if journal.fence_revision is not None:
                if loaded is not None:
                    current, etag = loaded
                    if etag == journal.fence_revision:
                        if not self._owns_fence(current, journal):
                            raise GraphStoreCorruptionError(
                                "The OCI Object Storage delete fence is inconsistent."
                            )
                        try:
                            self._delete_object(
                                self._graph_name(journal.project, journal.graph),
                                etag,
                            )
                        except GraphStoreConflictError:
                            continue
                        self._checkpoint("after_graph_delete")
                    elif current.delete_fence is not None:
                        self._resolve_loaded_graph(current, etag)
                return self._complete_delete(journal, journal_etag)
            if loaded is None:
                try:
                    self._delete_object(
                        self._journal_name(journal.project, "delete", journal.key_sha256),
                        journal_etag,
                    )
                except GraphStoreConflictError:
                    journal, journal_etag = self._reload_delete_journal(journal)
                    continue
                raise GraphStoreNotFoundError("The graph does not exist.")
            current, etag = loaded
            if current.delete_fence is not None:
                if not self._owns_fence(current, journal):
                    self._resolve_loaded_graph(current, etag)
                    try:
                        self._delete_object(
                            self._journal_name(journal.project, "delete", journal.key_sha256),
                            journal_etag,
                        )
                    except GraphStoreConflictError:
                        journal, journal_etag = self._reload_delete_journal(journal)
                        continue
                    raise GraphStoreNotFoundError("The graph does not exist.")
                fence_revision = etag
            else:
                if etag != journal.expected_revision:
                    try:
                        self._delete_object(
                            self._journal_name(journal.project, "delete", journal.key_sha256),
                            journal_etag,
                        )
                    except GraphStoreConflictError:
                        journal, journal_etag = self._reload_delete_journal(journal)
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
                    fence_revision = self._write_model(
                        self._graph_name(journal.project, journal.graph),
                        fenced,
                        expected_etag=etag,
                        object_metadata=_GraphObjectMetadata.from_stored(fenced),
                    )
                except GraphStoreConflictError:
                    continue
                self._checkpoint("after_delete_fence")
            with_fence = journal.model_copy(update={"fence_revision": fence_revision})
            try:
                new_journal_etag = self._write_model(
                    self._journal_name(journal.project, "delete", journal.key_sha256),
                    with_fence,
                    expected_etag=journal_etag,
                )
            except GraphStoreConflictError:
                journal, journal_etag = self._reload_delete_journal(journal)
                continue
            journal = with_fence
            journal_etag = new_journal_etag
        raise GraphStoreConflictError("The graph delete did not converge.")

    def _complete_delete(
        self,
        journal: _DeleteJournal,
        journal_etag: str,
    ) -> GraphDeleteReceipt:
        for _ in range(8):
            if journal.status == "completed":
                return journal.receipt.receipt()
            completed = journal.model_copy(update={"status": "completed"})
            try:
                self._write_model(
                    self._journal_name(journal.project, "delete", journal.key_sha256),
                    completed,
                    expected_etag=journal_etag,
                )
            except GraphStoreConflictError:
                journal, journal_etag = self._reload_delete_journal(journal)
                continue
            self._checkpoint("after_delete_completed")
            return journal.receipt.receipt()
        raise GraphStoreConflictError("The graph delete completion did not converge.")

    def _reload_delete_journal(
        self,
        expected: _DeleteJournal,
    ) -> tuple[_DeleteJournal, str]:
        loaded = self._read_model(
            self._journal_name(expected.project, "delete", expected.key_sha256),
            _DeleteJournal,
            _MAX_DELETE_JOURNAL_BYTES,
        )
        if loaded is None:
            raise GraphStoreConflictError("The OCI Object Storage delete journal disappeared.")
        self._validate_delete_replay(
            loaded[0],
            expected.project,
            expected.graph,
            expected.key_sha256,
            expected.request_sha256,
        )
        return loaded[0], loaded[1]

    def _load_resolved(
        self,
        project: str,
        graph: str,
    ) -> tuple[GraphRecord, _StoredGraph, str] | None:
        loaded = self._read_graph(project, graph)
        if loaded is None:
            return None
        return self._resolve_loaded_graph(*loaded)

    def _summary_from_key(
        self,
        name: str,
        project: str,
        graph: str,
    ) -> GraphSummary | None:
        for _ in range(2):
            head = self._head_object(name)
            if head is None:
                return None
            if head.size > self._max_graph_envelope_bytes:
                raise GraphStoreCorruptionError(
                    "An OCI Object Storage graph-store object exceeds its bound."
                )
            try:
                metadata = _GraphObjectMetadata.model_validate(head.metadata)
            except ValidationError as error:
                raise GraphStoreCorruptionError(
                    "An OCI Object Storage graph-store object has invalid metadata."
                ) from error
            if metadata.project != project or metadata.graph != graph:
                raise GraphStoreCorruptionError(
                    "The OCI Object Storage graph metadata is addressed incorrectly."
                )
            if metadata.delete_key_sha256 is None:
                return metadata.summary(head.etag)
            loaded = self._read_graph(project, graph)
            if loaded is None:
                return None
            if loaded[1] != head.etag:
                continue
            resolved = self._resolve_loaded_graph(*loaded)
            return resolved[0].summary() if resolved is not None else None
        raise GraphStoreConflictError("The OCI Object Storage graph changed during listing.")

    def _resolve_loaded_graph(
        self,
        stored: _StoredGraph,
        etag: str,
    ) -> tuple[GraphRecord, _StoredGraph, str] | None:
        if stored.delete_fence is not None:
            loaded = self._read_model(
                self._journal_name(stored.project, "delete", stored.delete_fence.key_sha256),
                _DeleteJournal,
                _MAX_DELETE_JOURNAL_BYTES,
            )
            if loaded is None:
                raise GraphStoreCorruptionError(
                    "The OCI Object Storage graph has an orphaned delete fence."
                )
            self._resume_delete(loaded[0], loaded[1])
            return None
        create_journal = self._read_model(
            self._journal_name(stored.project, "create", stored.create_key_sha256),
            _CreateJournal,
            self._max_create_journal_bytes,
        )
        if create_journal is None:
            raise GraphStoreCorruptionError("The OCI Object Storage graph has no create journal.")
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
            raise GraphStoreCorruptionError(
                "The OCI Object Storage graph record has an invalid content hash."
            )
        return stored.record(canonical, etag), stored, etag

    def _read_graph(self, project: str, graph: str) -> tuple[_StoredGraph, str] | None:
        loaded = self._read_model(
            self._graph_name(project, graph),
            _StoredGraph,
            self._max_graph_envelope_bytes,
        )
        if loaded is None:
            return None
        stored, etag, metadata = loaded
        self._validate_graph_address(stored, project, graph)
        if metadata != _metadata_from_model(_GraphObjectMetadata.from_stored(stored)):
            raise GraphStoreCorruptionError(
                "The OCI Object Storage graph metadata does not match its document."
            )
        return stored, etag

    @staticmethod
    def _validate_graph_address(stored: _StoredGraph, project: str, graph: str) -> None:
        if stored.project != project or stored.graph != graph:
            raise GraphStoreCorruptionError(
                "The OCI Object Storage graph record is addressed incorrectly."
            )

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
            raise GraphStoreCorruptionError(
                "The OCI Object Storage create journal is addressed incorrectly."
            )
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
            raise GraphStoreCorruptionError(
                "The OCI Object Storage delete journal is addressed incorrectly."
            )
        if journal.graph != graph or journal.request_sha256 != fingerprint:
            raise GraphStoreIdempotencyConflictError(
                "The idempotency key was already used for a different request."
            )

    def _read_model(
        self,
        name: str,
        model_type: type[_ModelT],
        max_bytes: int,
    ) -> tuple[_ModelT, str, dict[str, str]] | None:
        for _ in range(3):
            head = self._head_object(name)
            if head is None:
                return None
            if head.size > max_bytes:
                raise GraphStoreCorruptionError(
                    "An OCI Object Storage graph-store object exceeds its bound."
                )
            try:
                response = self._client.get_object(
                    self._namespace,
                    self._bucket_name,
                    name,
                    if_match=head.etag,
                    range=f"bytes=0-{max_bytes}",
                )
            except Exception as error:
                if _is_conditional_read_conflict(error):
                    continue
                raise GraphStoreError("The OCI Object Storage graph-store read failed.") from error
            stream_response = _stream_response(_response_data(response))
            try:
                response_headers = _response_headers(response)
                response_etag = _checked_etag(_header(response_headers, "etag"))
                if response_etag != head.etag:
                    continue
                stream = stream_response.raw
                data = stream.read(max_bytes + 1)
                extra = stream.read(1)
            except Exception as error:
                if isinstance(error, GraphStoreError):
                    raise
                raise GraphStoreError(
                    "The OCI Object Storage graph-store body read failed."
                ) from error
            finally:
                try:
                    stream_response.close()
                except Exception as error:
                    raise GraphStoreError(
                        "The OCI Object Storage graph-store body close failed."
                    ) from error
            if not isinstance(data, bytes) or not isinstance(extra, bytes):
                raise GraphStoreCorruptionError(
                    "The OCI Object Storage graph-store body is invalid."
                )
            if len(data) > max_bytes or extra:
                raise GraphStoreCorruptionError(
                    "An OCI Object Storage graph-store object exceeds its bound."
                )
            try:
                return model_type.model_validate_json(data), head.etag, head.metadata
            except ValidationError as error:
                raise GraphStoreCorruptionError(
                    "An OCI Object Storage graph-store object is invalid."
                ) from error
        raise GraphStoreConflictError(
            "The OCI Object Storage graph-store object changed during the read."
        )

    def _head_object(self, name: str) -> _ObjectHead | None:
        try:
            response = self._client.head_object(self._namespace, self._bucket_name, name)
        except Exception as error:
            if _is_object_not_found(error):
                return None
            raise GraphStoreError(
                "The OCI Object Storage graph-store metadata read failed."
            ) from error
        headers = _response_headers(response)
        etag = _checked_etag(_header(headers, "etag"))
        size = _content_length(headers)
        if _header(headers, "content-type") != "application/json":
            raise GraphStoreCorruptionError(
                "The OCI Object Storage graph-store content type is invalid."
            )
        metadata = _object_metadata(headers)
        return _ObjectHead(etag=etag, size=size, metadata=metadata)

    def _write_model(
        self,
        name: str,
        model: BaseModel,
        *,
        expected_etag: str | None,
        object_metadata: _GraphObjectMetadata | None = None,
    ) -> str:
        data = _canonical_json_bytes(model.model_dump(mode="json"))
        condition = (
            {"if_none_match": "*"}
            if expected_etag is None
            else {"if_match": _checked_etag(expected_etag)}
        )
        try:
            response = self._client.put_object(
                self._namespace,
                self._bucket_name,
                name,
                data,
                content_length=len(data),
                content_type="application/json",
                opc_meta=(
                    _metadata_from_model(object_metadata) if object_metadata is not None else {}
                ),
                **condition,
            )
            return _checked_etag(_header(_response_headers(response), "etag"))
        except Exception as error:
            if _is_no_etag_match(error) or (
                expected_etag is not None and _is_object_not_found(error)
            ):
                raise GraphStoreConflictError(
                    "The OCI Object Storage graph-store precondition failed."
                ) from error
            if isinstance(error, GraphStoreError):
                raise
            raise GraphStoreError("The OCI Object Storage graph-store write failed.") from error

    def _delete_object(self, name: str, etag: str) -> None:
        try:
            self._client.delete_object(
                self._namespace,
                self._bucket_name,
                name,
                if_match=_checked_etag(etag),
            )
        except Exception as error:
            if _is_no_etag_match(error) or _is_object_not_found(error):
                raise GraphStoreConflictError(
                    "The OCI Object Storage graph-store delete precondition failed."
                ) from error
            raise GraphStoreError("The OCI Object Storage graph-store delete failed.") from error

    def _list_entries(
        self,
        *,
        prefix: str,
        start_after: str | None,
        start: str | None,
        max_keys: int,
    ) -> tuple[builtins.list[str], str | None]:
        if start is not None and start_after is not None:
            raise GraphStoreCorruptionError("The OCI Object Storage graph-store page is invalid.")
        request: dict[str, object] = {"prefix": prefix, "limit": max_keys}
        if start_after is not None:
            request["start_after"] = start_after
        if start is not None:
            request["start"] = start
        try:
            response = self._client.list_objects(
                self._namespace,
                self._bucket_name,
                **request,
            )
        except Exception as error:
            raise GraphStoreError("The OCI Object Storage graph-store list failed.") from error
        data = _response_data(response)
        if not hasattr(data, "objects") or not hasattr(data, "next_start_with"):
            raise GraphStoreCorruptionError("The OCI Object Storage graph-store page is invalid.")
        page = cast("_ListDataPort", data)
        objects = page.objects
        next_start = page.next_start_with
        if (
            not isinstance(objects, list)
            or len(objects) > max_keys
            or not all(hasattr(item, "name") for item in objects)
        ):
            raise GraphStoreCorruptionError("The OCI Object Storage graph-store page is invalid.")
        names = [cast("_ListObjectPort", item).name for item in objects]
        if not all(isinstance(name, str) and name for name in names):
            raise GraphStoreCorruptionError("The OCI Object Storage graph-store page is invalid.")
        checked_names = cast("builtins.list[str]", names)
        if (
            checked_names != sorted(set(checked_names))
            or (start_after is not None and any(name <= start_after for name in checked_names))
            or (start is not None and any(name < start for name in checked_names))
        ):
            raise GraphStoreCorruptionError("The OCI Object Storage graph-store page is invalid.")
        if next_start is not None and (
            not isinstance(next_start, str) or not next_start.startswith(prefix)
        ):
            raise GraphStoreCorruptionError("The OCI Object Storage graph-store page is invalid.")
        return checked_names, next_start

    def _graph_prefix(self, project: str) -> str:
        return f"{self._prefix}/projects/{project}/graphs/"

    def _graph_name(self, project: str, graph: str | None) -> str:
        if graph is None:
            return self._graph_prefix(project)
        return f"{self._graph_prefix(project)}{graph}.json"

    def _graph_from_name(self, project: str, name: str) -> str:
        prefix = self._graph_prefix(project)
        if not name.startswith(prefix) or not name.endswith(".json"):
            raise GraphStoreCorruptionError(
                "The OCI Object Storage graph-store object layout is invalid."
            )
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


def _metadata_from_model(model: BaseModel) -> dict[str, str]:
    raw = model.model_dump(mode="json", exclude_none=True)
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw.items()):
        raise GraphStoreCorruptionError("The OCI Object Storage graph-store metadata is invalid.")
    return cast("dict[str, str]", raw)


def _key_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _checked_etag(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise GraphStoreCorruptionError("An OCI Object Storage object has an invalid ETag.")
    return value


def _error_code(error: BaseException) -> str | None:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


def _error_status(error: BaseException) -> int | None:
    status = getattr(error, "status", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _is_object_not_found(error: BaseException) -> bool:
    return _error_status(error) == 404 and _error_code(error) == "NotAuthorizedOrNotFound"


def _is_conditional_read_conflict(error: BaseException) -> bool:
    return _is_object_not_found(error) or _is_no_etag_match(error)


def _is_no_etag_match(error: BaseException) -> bool:
    return _error_status(error) == 412 and _error_code(error) == "NoEtagMatch"


def _response_headers(response: object) -> Mapping[str, str]:
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
    ):
        raise GraphStoreCorruptionError(
            "The OCI Object Storage graph-store response headers are invalid."
        )
    return cast("Mapping[str, str]", headers)


def _response_data(response: object) -> object:
    if not hasattr(response, "data"):
        raise GraphStoreCorruptionError(
            "The OCI Object Storage graph-store response body is invalid."
        )
    return cast("_ResponseDataPort", response).data


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.lower()
    matches = [value for key, value in headers.items() if key.lower() == expected]
    if len(matches) > 1:
        raise GraphStoreCorruptionError(
            "The OCI Object Storage graph-store response headers are invalid."
        )
    return matches[0] if matches else None


def _content_length(headers: Mapping[str, str]) -> int:
    raw = _header(headers, "content-length")
    try:
        size = int(raw) if raw is not None else -1
    except ValueError as error:
        raise GraphStoreCorruptionError(
            "The OCI Object Storage graph-store object size is invalid."
        ) from error
    if size < 0:
        raise GraphStoreCorruptionError(
            "The OCI Object Storage graph-store object size is invalid."
        )
    return size


def _object_metadata(headers: Mapping[str, str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if not lowered.startswith("opc-meta-"):
            continue
        name = lowered.removeprefix("opc-meta-")
        if not name or name in metadata:
            raise GraphStoreCorruptionError(
                "The OCI Object Storage graph-store object metadata is invalid."
            )
        metadata[name] = value
    return metadata


def _stream_response(value: object) -> _StreamResponsePort:
    if not hasattr(value, "raw") or not hasattr(value, "close"):
        raise GraphStoreCorruptionError("The OCI Object Storage graph-store body is invalid.")
    stream = cast("_StreamResponsePort", value)
    if not hasattr(stream.raw, "read"):
        raise GraphStoreCorruptionError("The OCI Object Storage graph-store body is invalid.")
    return stream
