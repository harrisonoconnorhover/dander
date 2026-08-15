"""Azure Blob Storage implementation of the provider-neutral ``GraphStore`` contract.

The module itself imports no Azure SDK. Constructing the adapter without an injected client is the
explicit provider boundary that lazily imports and creates an Azure Blob client. Object ETags remain
private implementation details and are exposed only as opaque GraphStore revisions.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, Self, TypeVar, cast
from urllib.parse import urlsplit

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
    from collections.abc import Iterable, Iterator

_MAX_GRAPH_ENVELOPE_OVERHEAD = 256 * 1024
_MAX_JOURNAL_OVERHEAD = 256 * 1024
_MAX_DELETE_JOURNAL_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,510}[A-Za-z0-9]$")
_CONTAINER = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class _ContentSettingsPort(Protocol):
    @property
    def content_type(self) -> str | None: ...


class _ContentSettingsFactory(Protocol):
    def __call__(self, *, content_type: str) -> _ContentSettingsPort: ...


@dataclass(frozen=True)
class _ContentSettingsValue:
    content_type: str


class _PropertiesPort(Protocol):
    name: str | None
    etag: str | None
    size: int | None
    metadata: dict[str, str] | None
    content_settings: _ContentSettingsPort | None


class _DownloaderPort(Protocol):
    properties: _PropertiesPort

    def readall(self) -> bytes: ...


class _BlobClientPort(Protocol):
    def get_blob_properties(self, **kwargs: object) -> _PropertiesPort: ...

    def download_blob(self, **kwargs: object) -> _DownloaderPort: ...

    def upload_blob(self, data: bytes, **kwargs: object) -> Mapping[str, object]: ...

    def delete_blob(self, **kwargs: object) -> None: ...


class _PageIteratorPort(Protocol):
    continuation_token: str | None

    def __iter__(self) -> Iterator[Iterable[_PropertiesPort]]: ...

    def __next__(self) -> Iterable[_PropertiesPort]: ...


class _PagedPort(Protocol):
    def by_page(self, continuation_token: str | None = None) -> _PageIteratorPort: ...


class _ClientPort(Protocol):
    def get_blob_client(self, blob: str) -> _BlobClientPort: ...

    def list_blobs(self, **kwargs: object) -> _PagedPort: ...


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


class AzureBlobGraphStore(GraphStore):
    """Persist canonical graph envelopes in one Azure Blob container and prefix."""

    def __init__(
        self,
        account_url: str,
        container: str,
        *,
        prefix: str = "dander-control/v1",
        client: _ClientPort | None = None,
        max_graph_bytes: int = MAX_GRAPH_DOCUMENT_BYTES,
        clock: Clock | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        parsed_url = urlsplit(account_url) if isinstance(account_url, str) else None
        if (
            parsed_url is None
            or parsed_url.scheme != "https"
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.path not in {"", "/"}
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise GraphStoreCorruptionError("The Azure Blob account URL binding is invalid.")
        if (
            not isinstance(container, str)
            or _CONTAINER.fullmatch(container) is None
            or "--" in container
        ):
            raise GraphStoreCorruptionError("The Azure Blob container binding is invalid.")
        prefix = prefix.strip("/")
        if (
            not prefix
            or _PREFIX.fullmatch(prefix) is None
            or any(part in {"", ".", ".."} for part in prefix.split("/"))
        ):
            raise GraphStoreCorruptionError("The Azure Blob graph-store prefix binding is invalid.")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise GraphStoreCorruptionError("The Azure Blob graph-store timeout is invalid.")
        if client is None:
            from azure.core import MatchConditions  # type: ignore[import-not-found]
            from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]
            from azure.storage.blob import (  # type: ignore[import-not-found]
                BlobServiceClient,
                ContentSettings,
            )

            client = cast(
                "_ClientPort",
                BlobServiceClient(
                    account_url,
                    credential=DefaultAzureCredential(),
                    connection_timeout=float(timeout_seconds),
                    read_timeout=float(timeout_seconds),
                    retry_total=3,
                ).get_container_client(container),
            )
            if_not_modified: object = MatchConditions.IfNotModified
            content_settings_factory = cast("_ContentSettingsFactory", ContentSettings)
        else:
            if_not_modified = "if_not_modified"
            content_settings_factory = _content_settings_value
        self._client = client
        self._account_url = account_url.rstrip("/")
        self._container_name = container
        self._prefix = prefix
        self._if_not_modified = if_not_modified
        self._content_settings_factory = content_settings_factory
        self._max_graph_bytes = _validated_max_bytes(max_graph_bytes)
        self._max_graph_envelope_bytes = max_graph_bytes + _MAX_GRAPH_ENVELOPE_OVERHEAD
        self._max_create_journal_bytes = max_graph_bytes + _MAX_JOURNAL_OVERHEAD
        self._clock = clock or _utc_now
        self._timeout = max(1, math.ceil(float(timeout_seconds)))

    @property
    def account_url(self) -> str:
        """Return the immutable account endpoint binding."""
        return self._account_url

    @property
    def container_name(self) -> str:
        """Return the immutable Blob container binding."""
        return self._container_name

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
        continuation_token = None
        items: builtins.list[GraphSummary] = []
        for _ in range(MAX_GRAPH_PAGE_SIZE + 2):
            entries, next_token = self._list_entries(
                prefix=graph_prefix,
                start_from=start_name,
                continuation_token=continuation_token,
                max_results=min(MAX_GRAPH_PAGE_SIZE + 2, limit + 2),
            )
            if not entries:
                if next_token is not None:
                    raise GraphStoreCorruptionError("The Azure Blob graph-store page is invalid.")
                break
            for entry in entries:
                name = entry.name
                if not isinstance(name, str):
                    raise GraphStoreCorruptionError("The Azure Blob graph-store page is invalid.")
                if name == start_name:
                    continue
                graph = self._graph_from_name(project, name)
                summary = self._summary_from_entry(entry, project, graph)
                if summary is not None:
                    items.append(summary)
                    if len(items) > limit:
                        break
            if len(items) > limit or next_token is None:
                break
            if next_token == continuation_token:
                raise GraphStoreCorruptionError("The Azure Blob graph-store page did not advance.")
            continuation_token = next_token
        else:
            raise GraphStoreConflictError("The Azure Blob graph-store list did not converge.")
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
                raise GraphStoreCorruptionError("The Azure Blob create journal is inconsistent.")
            if journal.status == "completed":
                if journal.result_revision is None:
                    raise GraphStoreCorruptionError("The Azure Blob create journal is invalid.")
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
                    "The pending Azure Blob create graph is inconsistent."
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
            raise GraphStoreConflictError("The Azure Blob create journal disappeared.")
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
                                "The Azure Blob delete fence is inconsistent."
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
            raise GraphStoreConflictError("The Azure Blob delete journal disappeared.")
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

    def _summary_from_entry(
        self,
        entry: _PropertiesPort,
        project: str,
        graph: str,
    ) -> GraphSummary | None:
        name = entry.name
        if not isinstance(name, str):
            raise GraphStoreCorruptionError("The Azure Blob graph-store page is invalid.")
        head = _head_from_properties(entry)
        for _ in range(2):
            if head.size > self._max_graph_envelope_bytes:
                raise GraphStoreCorruptionError(
                    "An Azure Blob graph-store object exceeds its bound."
                )
            try:
                metadata = _GraphObjectMetadata.model_validate(head.metadata)
            except ValidationError as error:
                raise GraphStoreCorruptionError(
                    "An Azure Blob graph-store object has invalid metadata."
                ) from error
            if metadata.project != project or metadata.graph != graph:
                raise GraphStoreCorruptionError(
                    "The Azure Blob graph metadata is addressed incorrectly."
                )
            if metadata.delete_key_sha256 is None:
                return metadata.summary(head.etag)
            loaded = self._read_graph(project, graph)
            if loaded is None:
                return None
            if loaded[1] != head.etag:
                refreshed = self._head_object(name)
                if refreshed is None:
                    return None
                head = refreshed
                continue
            resolved = self._resolve_loaded_graph(*loaded)
            return resolved[0].summary() if resolved is not None else None
        raise GraphStoreConflictError("The Azure Blob graph changed during listing.")

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
                    "The Azure Blob graph has an orphaned delete fence."
                )
            self._resume_delete(loaded[0], loaded[1])
            return None
        create_journal = self._read_model(
            self._journal_name(stored.project, "create", stored.create_key_sha256),
            _CreateJournal,
            self._max_create_journal_bytes,
        )
        if create_journal is None:
            raise GraphStoreCorruptionError("The Azure Blob graph has no create journal.")
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
                "The Azure Blob graph record has an invalid content hash."
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
                "The Azure Blob graph metadata does not match its document."
            )
        return stored, etag

    @staticmethod
    def _validate_graph_address(stored: _StoredGraph, project: str, graph: str) -> None:
        if stored.project != project or stored.graph != graph:
            raise GraphStoreCorruptionError("The Azure Blob graph record is addressed incorrectly.")

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
                "The Azure Blob create journal is addressed incorrectly."
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
                "The Azure Blob delete journal is addressed incorrectly."
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
                    "An Azure Blob graph-store object exceeds its bound."
                )
            try:
                downloader = self._client.get_blob_client(name).download_blob(
                    offset=0,
                    length=max_bytes + 1,
                    etag=head.etag,
                    match_condition=self._if_not_modified,
                    max_concurrency=1,
                    timeout=self._timeout,
                )
                response_etag = _checked_etag(downloader.properties.etag)
                data = downloader.readall()
            except Exception as error:
                if _is_conditional_read_conflict(error):
                    continue
                if isinstance(error, GraphStoreError):
                    raise
                raise GraphStoreError("The Azure Blob graph-store read failed.") from error
            if response_etag != head.etag:
                continue
            if not isinstance(data, bytes):
                raise GraphStoreCorruptionError("The Azure Blob graph-store body is invalid.")
            if len(data) > max_bytes:
                raise GraphStoreCorruptionError(
                    "An Azure Blob graph-store object exceeds its bound."
                )
            try:
                return model_type.model_validate_json(data), head.etag, head.metadata
            except ValidationError as error:
                raise GraphStoreCorruptionError(
                    "An Azure Blob graph-store object is invalid."
                ) from error
        raise GraphStoreConflictError("The Azure Blob graph-store object changed during the read.")

    def _head_object(self, name: str) -> _ObjectHead | None:
        try:
            properties = self._client.get_blob_client(name).get_blob_properties(
                timeout=self._timeout
            )
        except Exception as error:
            if _is_read_not_found(error):
                return None
            raise GraphStoreError("The Azure Blob graph-store metadata read failed.") from error
        content_settings = properties.content_settings
        if content_settings is None or content_settings.content_type != "application/json":
            raise GraphStoreCorruptionError(
                "The Azure Blob graph-store object content type is invalid."
            )
        return _head_from_properties(properties)

    def _write_model(
        self,
        name: str,
        model: BaseModel,
        *,
        expected_etag: str | None,
        object_metadata: _GraphObjectMetadata | None = None,
    ) -> str:
        data = _canonical_json_bytes(model.model_dump(mode="json"))
        create = expected_etag is None
        condition: dict[str, object] = {"overwrite": False}
        if expected_etag is not None:
            condition = {
                "overwrite": True,
                "etag": _checked_etag(expected_etag),
                "match_condition": self._if_not_modified,
            }
        try:
            response = self._client.get_blob_client(name).upload_blob(
                data,
                length=len(data),
                metadata=(
                    _metadata_from_model(object_metadata) if object_metadata is not None else {}
                ),
                content_settings=self._content_settings_factory(content_type="application/json"),
                timeout=self._timeout,
                **condition,
            )
            return _checked_etag(response.get("etag"))
        except Exception as error:
            if (create and _is_create_conflict(error)) or (
                not create and _is_conditional_mutation_conflict(error)
            ):
                raise GraphStoreConflictError(
                    "The Azure Blob graph-store precondition failed."
                ) from error
            if isinstance(error, GraphStoreError):
                raise
            raise GraphStoreError("The Azure Blob graph-store write failed.") from error

    def _delete_object(self, name: str, etag: str) -> None:
        try:
            self._client.get_blob_client(name).delete_blob(
                etag=_checked_etag(etag),
                match_condition=self._if_not_modified,
                timeout=self._timeout,
            )
        except Exception as error:
            if _is_conditional_mutation_conflict(error):
                raise GraphStoreConflictError(
                    "The Azure Blob graph-store delete precondition failed."
                ) from error
            raise GraphStoreError("The Azure Blob graph-store delete failed.") from error

    def _list_entries(
        self,
        *,
        prefix: str,
        start_from: str | None,
        continuation_token: str | None,
        max_results: int,
    ) -> tuple[builtins.list[_PropertiesPort], str | None]:
        try:
            paged = self._client.list_blobs(
                name_starts_with=prefix,
                include=["metadata"],
                start_from=start_from,
                results_per_page=max_results,
                timeout=self._timeout,
            )
            pages = paged.by_page(continuation_token=continuation_token)
            try:
                entries = list(next(pages))
            except StopIteration:
                return [], None
            next_token = pages.continuation_token
        except Exception as error:
            raise GraphStoreError("The Azure Blob graph-store list failed.") from error
        if next_token is not None and not isinstance(next_token, str):
            raise GraphStoreCorruptionError("The Azure Blob graph-store page is invalid.")
        return entries, next_token

    def _graph_prefix(self, project: str) -> str:
        return f"{self._prefix}/projects/{project}/graphs/"

    def _graph_name(self, project: str, graph: str | None) -> str:
        if graph is None:
            return self._graph_prefix(project)
        return f"{self._graph_prefix(project)}{graph}.json"

    def _graph_from_name(self, project: str, name: str) -> str:
        prefix = self._graph_prefix(project)
        if not name.startswith(prefix) or not name.endswith(".json"):
            raise GraphStoreCorruptionError("The Azure Blob graph-store object layout is invalid.")
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
        raise GraphStoreCorruptionError("The Azure Blob graph-store metadata is invalid.")
    return cast("dict[str, str]", raw)


def _content_settings_value(*, content_type: str) -> _ContentSettingsPort:
    return _ContentSettingsValue(content_type=content_type)


def _key_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _checked_etag(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise GraphStoreCorruptionError("An Azure Blob object has an invalid ETag.")
    if value.startswith('"') or value.endswith('"'):
        if not (value.startswith('"') and value.endswith('"')) or len(value) <= 2:
            raise GraphStoreCorruptionError("An Azure Blob object has an invalid ETag.")
        normalized = value
    else:
        if '"' in value:
            raise GraphStoreCorruptionError("An Azure Blob object has an invalid ETag.")
        normalized = f'"{value}"'
    if len(normalized) > 512:
        raise GraphStoreCorruptionError("An Azure Blob object has an invalid ETag.")
    return normalized


def _head_from_properties(properties: _PropertiesPort) -> _ObjectHead:
    etag = _checked_etag(properties.etag)
    size = properties.size
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise GraphStoreCorruptionError("The Azure Blob graph-store object size is invalid.")
    metadata = properties.metadata
    if not isinstance(metadata, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()
    ):
        raise GraphStoreCorruptionError("The Azure Blob graph-store object metadata is invalid.")
    return _ObjectHead(etag=etag, size=size, metadata=dict(metadata))


def _error_code(error: BaseException) -> str | None:
    code = getattr(error, "error_code", None)
    if isinstance(code, str):
        return code
    value = getattr(code, "value", None)
    return value if isinstance(value, str) else None


def _is_read_not_found(error: BaseException) -> bool:
    return _error_code(error) == "BlobNotFound"


def _is_conditional_read_conflict(error: BaseException) -> bool:
    return _error_code(error) in {"BlobNotFound", "ConditionNotMet"}


def _is_create_conflict(error: BaseException) -> bool:
    return _error_code(error) == "BlobAlreadyExists"


def _is_conditional_mutation_conflict(error: BaseException) -> bool:
    return _is_conditional_read_conflict(error)
