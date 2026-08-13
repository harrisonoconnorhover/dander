"""Provider-neutral graph persistence semantics for the hosted Control API.

The store owns document identity and optimistic-concurrency behavior, not graph semantics.
Every document is first routed through Dander's published Control transport model and canonical
domain serializer. Provider adapters may use generations, versions, or ETags internally, while
callers see only an opaque revision plus a portable canonical-content digest.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import threading
import uuid
from abc import ABC, abstractmethod
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError

from dander.control.models import PipelineGraphDocument
from dander.pipeline.graph import graph_to_payload

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_GRAPH_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_GRAPH_PAGE_SIZE = 100

_PORTABLE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_MAX_CURSOR_BYTES = 512
_MAX_REVISION_LENGTH = 512

type Clock = Callable[[], datetime]
type RevisionFactory = Callable[[], str]


class GraphStoreError(RuntimeError):
    """Base for graph-store failures safe to map to a structured API error."""


class GraphStoreIdentifierError(GraphStoreError):
    """A project, graph, idempotency key, cursor, or revision is malformed."""


class GraphStoreDocumentError(GraphStoreError):
    """A graph is invalid, non-canonicalizable, or larger than the configured bound."""


class GraphStoreNotFoundError(GraphStoreError):
    """The addressed graph does not exist."""


class GraphStoreAlreadyExistsError(GraphStoreError):
    """A create operation addressed an existing graph."""


class GraphStoreConflictError(GraphStoreError):
    """A conditional mutation no longer matches the current opaque revision."""


class GraphStoreIdempotencyConflictError(GraphStoreError):
    """An idempotency key was already consumed by a different request."""


class GraphStoreCorruptionError(GraphStoreError):
    """A durable local store contains data that cannot be trusted or recovered safely."""


@dataclass(frozen=True, slots=True)
class CanonicalGraphDocument:
    """One validated graph and the exact canonical bytes used for size and identity."""

    document: PipelineGraphDocument
    data: bytes
    content_sha256: str


@dataclass(frozen=True, slots=True)
class GraphSummary:
    """Document-free graph metadata suitable for a bounded list page."""

    project: str
    graph: str
    revision: str
    content_sha256: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class GraphRecord:
    """One canonical graph document with concurrency and portable identity metadata."""

    project: str
    graph: str
    document: PipelineGraphDocument
    revision: str
    content_sha256: str
    created_at: str
    updated_at: str

    def summary(self) -> GraphSummary:
        """Return the document-free list projection for this graph."""
        return GraphSummary(
            project=self.project,
            graph=self.graph,
            revision=self.revision,
            content_sha256=self.content_sha256,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass(frozen=True, slots=True)
class GraphPage:
    """A bounded page of graph summaries and an opaque continuation cursor."""

    items: tuple[GraphSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class GraphDeleteReceipt:
    """The exact replayable outcome of one successful conditional graph deletion."""

    project: str
    graph: str
    revision: str
    content_sha256: str
    deleted_at: str


class GraphStore(ABC):
    """Persist canonical graph documents behind provider-neutral conditional semantics.

    Create and delete idempotency keys are scoped by ``(project, operation, key)``. A successful
    identical retry returns its original result, while key reuse for different input fails.
    Validation and precondition failures must not consume a key.
    """

    @abstractmethod
    def list(self, project: str, *, cursor: str | None = None, limit: int = 50) -> GraphPage:
        """Return one project-bound page containing summaries but no graph documents."""

    @abstractmethod
    def get(self, project: str, graph: str) -> GraphRecord:
        """Return one graph or raise ``GraphStoreNotFoundError``."""

    @abstractmethod
    def create(
        self,
        project: str,
        graph: str,
        document: PipelineGraphDocument,
        *,
        idempotency_key: str,
    ) -> GraphRecord:
        """Create one absent graph and replay an identical successful request exactly."""

    @abstractmethod
    def put(
        self,
        project: str,
        graph: str,
        document: PipelineGraphDocument,
        *,
        expected_revision: str,
    ) -> GraphRecord:
        """Replace one graph only when its opaque revision still matches."""

    @abstractmethod
    def delete(
        self,
        project: str,
        graph: str,
        *,
        expected_revision: str,
        idempotency_key: str,
    ) -> GraphDeleteReceipt:
        """Delete one matching graph and replay an identical successful request exactly."""


def canonicalize_graph_document(
    value: object,
    *,
    max_bytes: int = MAX_GRAPH_DOCUMENT_BYTES,
) -> CanonicalGraphDocument:
    """Validate and encode a graph using Dander's one portable canonical byte representation.

    The encoding is UTF-8 JSON with sorted keys, compact separators, unescaped Unicode, no
    non-finite numbers, and no trailing newline. Both the size limit and SHA-256 apply to these
    exact bytes.

    Args:
        value: A graph transport model or JSON-compatible input accepted by that model.
        max_bytes: Maximum encoded canonical document size.

    Returns:
        The canonical transport document, bytes, and lowercase SHA-256.

    Raises:
        GraphStoreDocumentError: If validation, semantic validation, encoding, or size fails.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise GraphStoreDocumentError("The graph document size bound is invalid.")
    try:
        transport = PipelineGraphDocument.model_validate(value)
        _reject_non_finite(transport.model_dump(mode="python", by_alias=True))
        payload = graph_to_payload(transport.to_domain())
        document = PipelineGraphDocument.model_validate(payload)
        data = _canonical_json_bytes(payload)
    except (TypeError, ValueError, ValidationError) as error:
        raise GraphStoreDocumentError(
            "The graph document does not match Dander's canonical graph contract."
        ) from error
    if len(data) > max_bytes:
        raise GraphStoreDocumentError("The canonical graph document exceeds the configured limit.")
    return CanonicalGraphDocument(
        document=document,
        data=data,
        content_sha256=hashlib.sha256(data).hexdigest(),
    )


class InMemoryGraphStore(GraphStore):
    """Thread-safe non-durable GraphStore for hosted tests and explicit ephemeral use."""

    def __init__(
        self,
        *,
        max_graph_bytes: int = MAX_GRAPH_DOCUMENT_BYTES,
        clock: Clock | None = None,
        revision_factory: RevisionFactory | None = None,
    ) -> None:
        self._max_graph_bytes = _validated_max_bytes(max_graph_bytes)
        self._clock = clock or _utc_now
        self._revision_factory = revision_factory or _opaque_revision
        self._records: dict[tuple[str, str], GraphRecord] = {}
        self._idempotency: dict[
            tuple[str, str, str],
            tuple[str, GraphRecord | GraphDeleteReceipt],
        ] = {}
        self._lock = threading.RLock()

    def list(self, project: str, *, cursor: str | None = None, limit: int = 50) -> GraphPage:
        project = _validated_identifier(project, "project")
        limit = _validated_page_size(limit)
        after = _decode_cursor(project, cursor)
        with self._lock:
            graph_ids = sorted(
                graph for item_project, graph in self._records if item_project == project
            )
            start = bisect_right(graph_ids, after) if after is not None else 0
            selected = graph_ids[start : start + limit]
            items = tuple(self._records[(project, graph)].summary() for graph in selected)
            next_cursor = (
                _encode_cursor(project, selected[-1])
                if selected and start + len(selected) < len(graph_ids)
                else None
            )
            return GraphPage(items=items, next_cursor=next_cursor)

    def get(self, project: str, graph: str) -> GraphRecord:
        key = _validated_graph_key(project, graph)
        with self._lock:
            try:
                return _isolated_record(self._records[key])
            except KeyError as error:
                raise GraphStoreNotFoundError("The graph does not exist.") from error

    def create(
        self,
        project: str,
        graph: str,
        document: PipelineGraphDocument,
        *,
        idempotency_key: str,
    ) -> GraphRecord:
        key = _validated_graph_key(project, graph)
        idempotency_key = _validated_idempotency_key(idempotency_key)
        canonical = canonicalize_graph_document(document, max_bytes=self._max_graph_bytes)
        fingerprint = _create_fingerprint(project, graph, canonical.content_sha256)
        scope = (project, "create", idempotency_key)
        with self._lock:
            replay = self._idempotency.get(scope)
            if replay is not None:
                return _replay_record(replay, fingerprint)
            if key in self._records:
                raise GraphStoreAlreadyExistsError("The graph already exists.")
            now = _timestamp(self._clock())
            record = GraphRecord(
                project=project,
                graph=graph,
                document=canonical.document,
                revision=_validated_new_revision(self._revision_factory()),
                content_sha256=canonical.content_sha256,
                created_at=now,
                updated_at=now,
            )
            self._records[key] = record
            self._idempotency[scope] = (fingerprint, record)
            return _isolated_record(record)

    def put(
        self,
        project: str,
        graph: str,
        document: PipelineGraphDocument,
        *,
        expected_revision: str,
    ) -> GraphRecord:
        key = _validated_graph_key(project, graph)
        expected_revision = _validated_revision(expected_revision)
        canonical = canonicalize_graph_document(document, max_bytes=self._max_graph_bytes)
        with self._lock:
            current = self._records.get(key)
            if current is None:
                raise GraphStoreNotFoundError("The graph does not exist.")
            if current.revision != expected_revision:
                raise GraphStoreConflictError("The graph revision is stale.")
            record = GraphRecord(
                project=project,
                graph=graph,
                document=canonical.document,
                revision=_validated_new_revision(self._revision_factory()),
                content_sha256=canonical.content_sha256,
                created_at=current.created_at,
                updated_at=_timestamp(self._clock()),
            )
            self._records[key] = record
            return _isolated_record(record)

    def delete(
        self,
        project: str,
        graph: str,
        *,
        expected_revision: str,
        idempotency_key: str,
    ) -> GraphDeleteReceipt:
        key = _validated_graph_key(project, graph)
        expected_revision = _validated_revision(expected_revision)
        idempotency_key = _validated_idempotency_key(idempotency_key)
        fingerprint = _delete_fingerprint(project, graph, expected_revision)
        scope = (project, "delete", idempotency_key)
        with self._lock:
            replay = self._idempotency.get(scope)
            if replay is not None:
                return _replay_receipt(replay, fingerprint)
            current = self._records.get(key)
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
            del self._records[key]
            self._idempotency[scope] = (fingerprint, receipt)
            return receipt


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _reject_non_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


def _validated_max_bytes(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GraphStoreDocumentError("The graph document size bound is invalid.")
    return value


def _validated_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or _PORTABLE_IDENTIFIER.fullmatch(value) is None:
        raise GraphStoreIdentifierError(
            f"The {label} identifier must be 1-63 lowercase ASCII letters, digits, "
            "hyphens, or underscores."
        )
    return value


def _validated_graph_key(project: str, graph: str) -> tuple[str, str]:
    return (
        _validated_identifier(project, "project"),
        _validated_identifier(graph, "graph"),
    )


def _validated_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise GraphStoreIdentifierError(
            "The idempotency key must be 8-128 URL-safe ASCII characters."
        )
    return value


def _validated_revision(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_REVISION_LENGTH:
        raise GraphStoreIdentifierError("The expected graph revision is invalid.")
    return value


def _validated_new_revision(value: str) -> str:
    try:
        return _validated_revision(value)
    except GraphStoreIdentifierError as error:
        raise GraphStoreCorruptionError(
            "The revision provider returned an invalid token."
        ) from error


def _validated_page_size(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_GRAPH_PAGE_SIZE
    ):
        raise GraphStoreIdentifierError(
            f"The graph page size must be between 1 and {MAX_GRAPH_PAGE_SIZE}."
        )
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GraphStoreCorruptionError("The graph-store clock returned a timezone-less value.")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _opaque_revision() -> str:
    return uuid.uuid4().hex


def _request_fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _create_fingerprint(project: str, graph: str, content_sha256: str) -> str:
    return _request_fingerprint(
        {
            "content_sha256": content_sha256,
            "graph": graph,
            "operation": "create",
            "project": project,
        }
    )


def _delete_fingerprint(project: str, graph: str, expected_revision: str) -> str:
    return _request_fingerprint(
        {
            "expected_revision": expected_revision,
            "graph": graph,
            "operation": "delete",
            "project": project,
        }
    )


def _replay_record(
    replay: tuple[str, GraphRecord | GraphDeleteReceipt],
    fingerprint: str,
) -> GraphRecord:
    saved_fingerprint, result = replay
    if saved_fingerprint != fingerprint or not isinstance(result, GraphRecord):
        raise GraphStoreIdempotencyConflictError(
            "The idempotency key was already used for a different request."
        )
    return _isolated_record(result)


def _replay_receipt(
    replay: tuple[str, GraphRecord | GraphDeleteReceipt],
    fingerprint: str,
) -> GraphDeleteReceipt:
    saved_fingerprint, result = replay
    if saved_fingerprint != fingerprint or not isinstance(result, GraphDeleteReceipt):
        raise GraphStoreIdempotencyConflictError(
            "The idempotency key was already used for a different request."
        )
    return result


def _isolated_record(record: GraphRecord) -> GraphRecord:
    return GraphRecord(
        project=record.project,
        graph=record.graph,
        document=record.document.model_copy(deep=True),
        revision=record.revision,
        content_sha256=record.content_sha256,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _encode_cursor(project: str, after: str) -> str:
    payload = _canonical_json_bytes({"after": after, "project": project, "version": 1})
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(project: str, cursor: str | None) -> str | None:
    if cursor is None:
        return None
    if not isinstance(cursor, str) or not cursor or len(cursor) > _MAX_CURSOR_BYTES:
        raise GraphStoreIdentifierError("The graph page cursor is invalid.")
    try:
        encoded = cursor.encode("ascii")
        padding = b"=" * (-len(encoded) % 4)
        raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (UnicodeEncodeError, ValueError, json.JSONDecodeError) as error:
        raise GraphStoreIdentifierError("The graph page cursor is invalid.") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"after", "project", "version"}
        or payload.get("project") != project
        or payload.get("version") != 1
    ):
        raise GraphStoreIdentifierError("The graph page cursor is invalid.")
    after = payload.get("after")
    if not isinstance(after, str):
        raise GraphStoreIdentifierError("The graph page cursor is invalid.")
    try:
        return _validated_identifier(after, "graph")
    except GraphStoreIdentifierError as error:
        raise GraphStoreIdentifierError("The graph page cursor is invalid.") from error
