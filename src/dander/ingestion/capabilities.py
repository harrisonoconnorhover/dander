"""Typed discovery and invocation for optional source capabilities.

``Source.discover`` and ``Source.extract`` remain the mandatory connector contract. Provider
APIs may also offer cheaper, targeted read operations, a deleted-record feed, and write-back
(create/update/upsert/delete). Those operations stay structural so an independently installed
connector can opt in without changing Dander's plugin API or adding stub methods to every source.

Cursor, retry, authorization, and destination semantics for the deleted-record feed and
write-back operations are resolved in ``docs/decisions.md``, "2026-08-05 — Write-back and
deleted-record-feed semantics" (see also the earlier "Optional source capabilities remain
structural and read-only" entry this one supersedes for those two operations). Summary: ``since``
cursors mirror ``Source.extract``; ``create`` is non-idempotent and MUST NOT be blindly retried on
an ambiguous failure, while ``update``/``upsert``/``delete`` are naturally idempotent; every
implementation routes credentials through the source's existing audited ``AuthStrategy``
(``steering/01-security.md``) rather than a separate path; write-back targets the source system
itself, never BigQuery — consuming ``get_deleted`` to propagate hard deletes into BigQuery remains
separate, deferred write-pattern work.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dander.ingestion.source import Source


class ConnectorOperation(StrEnum):
    """Operations a concrete ``Source`` may optionally implement.

    ``GET_SINGLE_OBJECT``/``GET_DELETED``/``COUNT``/``TEST_CONNECTION`` are read-only.
    ``CREATE``/``UPDATE``/``UPSERT``/``DELETE`` are opt-in write-back, per the Decision Log entry
    2026-08-04 ("Write-back is now an optional, opt-in connector capability, not a hard
    non-goal") in ``steering/00-project-overview.md``. The core read → land-in-BigQuery path is
    unaffected by either group.
    """

    GET_SINGLE_OBJECT = "get_single_object"
    GET_DELETED = "get_deleted"
    COUNT = "count"
    TEST_CONNECTION = "test_connection"
    CREATE = "create"
    UPDATE = "update"
    UPSERT = "upsert"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class RecordNotFound:
    """Sentinel type for a targeted lookup that matched no record."""


RECORD_NOT_FOUND: Final[RecordNotFound] = RecordNotFound()


@runtime_checkable
class SupportsGetSingleObject(Protocol):
    """Fetch one record by its endpoint business key without a full extraction."""

    def get_single_object(
        self,
        endpoint: str,
        identity: Mapping[str, str],
    ) -> Mapping[str, Any] | RecordNotFound:
        """Return the record or ``RECORD_NOT_FOUND`` when the identity is absent."""
        ...


@runtime_checkable
class SupportsGetDeleted(Protocol):
    """Yield the business keys of records hard-deleted at the source.

    Mirrors ``Source.extract(endpoint, since=...)`` in keying and cursor semantics so a
    downstream consumer can reconcile the insert/update stream and the delete stream off one
    watermark. Consuming this feed to propagate deletions into BigQuery is separate, deferred
    write-pattern work — this Protocol only surfaces the feed as a typed, detectable capability.
    """

    def get_deleted(
        self, endpoint: str, *, since: str | None = None
    ) -> Iterator[Mapping[str, Any]]:
        """Yield one mapping per record deleted at ``endpoint``, each carrying its business key."""
        ...


class CountPrecision(StrEnum):
    """Whether a reported source count is exact or approximate."""

    EXACT = "exact"
    ESTIMATE = "estimate"


@dataclass(frozen=True, slots=True)
class CountResult:
    """A non-negative record count and its precision."""

    count: int
    precision: CountPrecision

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 0:
            raise ValueError("CountResult.count must be a non-negative integer")

    @classmethod
    def exact(cls, count: int) -> CountResult:
        """Construct an exact count."""
        return cls(count=count, precision=CountPrecision.EXACT)

    @classmethod
    def estimate(cls, count: int) -> CountResult:
        """Construct an estimated count."""
        return cls(count=count, precision=CountPrecision.ESTIMATE)


@runtime_checkable
class SupportsCount(Protocol):
    """Report a cheap count without materializing an endpoint's records."""

    def count(self, endpoint: str, *, since: str | None = None) -> CountResult:
        """Return an exact or estimated count, optionally bounded by a cursor."""
        ...


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    """Non-secret outcome of a source-level connectivity and credential probe."""

    ok: bool
    detail: str | None = None


@runtime_checkable
class SupportsTestConnection(Protocol):
    """Probe connectivity and credentials without returning business records."""

    def test_connection(self) -> ConnectionStatus:
        """Return a scalar connection result safe to display to an operator."""
        ...


@runtime_checkable
class SupportsCreate(Protocol):
    """Create one new record in the source system.

    Non-idempotent, unlike ``update``/``upsert``: calling ``create`` twice with the same
    ``record`` generally produces two distinct source-system records. Callers MUST NOT blindly
    retry after an ambiguous failure (e.g. a timeout where the write may or may not have landed).
    """

    def create(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        """Create ``record`` at ``endpoint``; return at least its business-key field(s)."""
        ...


@runtime_checkable
class SupportsUpdate(Protocol):
    """Update an existing record, addressed by its business key.

    Naturally idempotent (not guaranteed): re-applying the same ``changes`` to the same
    ``identity`` converges to the same source-system state.
    """

    def update(
        self,
        endpoint: str,
        identity: Mapping[str, str],
        changes: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Apply ``changes`` to the record at ``endpoint`` identified by ``identity``."""
        ...


@runtime_checkable
class SupportsUpsert(Protocol):
    """Create-or-update a record, keyed on the endpoint's own business-key field(s).

    Unlike ``update``, takes no separate identity argument — the implementation resolves the key
    from its own ``SourceConfig.endpoints`` (``Endpoint.primary_key``), the write-side twin of the
    BigQuery Writer's SCD1 ``MERGE`` on ``WriteTarget.business_key``.
    """

    def upsert(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        """Create ``record`` at ``endpoint`` if its business key is new, else update in place."""
        ...


class DeleteOutcome(StrEnum):
    """The closed result vocabulary for a ``delete`` write-back call.

    Both outcomes are normal and reportable as a value rather than by raising, so an
    implementation is never tempted to embed an identity value in an exception message
    (``steering/01-security.md``).
    """

    DELETED = "deleted"
    NOT_FOUND = "not_found"


@runtime_checkable
class SupportsDelete(Protocol):
    """Delete one existing record, addressed by its business key.

    Naturally idempotent: deleting an already-absent record returns ``DeleteOutcome.NOT_FOUND``
    rather than raising, so a repeat call for the same ``identity`` is safe to retry. Distinct
    from ``SupportsGetDeleted.get_deleted``, which *reports* deletions the source already made;
    this Protocol *performs* one.
    """

    def delete(self, endpoint: str, identity: Mapping[str, str]) -> DeleteOutcome:
        """Delete the record at ``endpoint`` identified by ``identity``, if it exists."""
        ...


_CAPABILITY_PROTOCOLS: Final[dict[ConnectorOperation, type]] = {
    ConnectorOperation.GET_SINGLE_OBJECT: SupportsGetSingleObject,
    ConnectorOperation.GET_DELETED: SupportsGetDeleted,
    ConnectorOperation.COUNT: SupportsCount,
    ConnectorOperation.TEST_CONNECTION: SupportsTestConnection,
    ConnectorOperation.CREATE: SupportsCreate,
    ConnectorOperation.UPDATE: SupportsUpdate,
    ConnectorOperation.UPSERT: SupportsUpsert,
    ConnectorOperation.DELETE: SupportsDelete,
}


class UnsupportedConnectorOperationError(ValueError):
    """Raised when a caller invokes an optional operation the source does not implement."""


class InvalidConnectorCapabilityResultError(TypeError):
    """Raised when a source returns a value outside an optional capability contract."""


class SourceCapabilities:
    """Typed facade over the optional read operations implemented by one ``Source``."""

    def __init__(self, source: Source) -> None:
        self._source = source
        self._supported = frozenset(
            operation
            for operation, protocol in _CAPABILITY_PROTOCOLS.items()
            if isinstance(source, protocol)
        )

    @property
    def supported_operations(self) -> frozenset[ConnectorOperation]:
        """Return the source's structurally implemented operations."""
        return self._supported

    def supports(self, operation: ConnectorOperation) -> bool:
        """Return whether the source implements ``operation``."""
        return operation in self._supported

    def require(self, operation: ConnectorOperation) -> None:
        """Fail clearly when the source does not implement ``operation``."""
        if not self.supports(operation):
            raise UnsupportedConnectorOperationError(
                f"source {self._source.config.name!r} does not support operation "
                f"{operation.value!r}"
            )

    def get_single_object(
        self,
        endpoint: str,
        identity: Mapping[str, str],
    ) -> Mapping[str, Any] | RecordNotFound:
        """Invoke a supported targeted record lookup."""
        self.require(ConnectorOperation.GET_SINGLE_OBJECT)
        implementation = cast("SupportsGetSingleObject", self._source)
        result = implementation.get_single_object(endpoint, identity)
        if result is not RECORD_NOT_FOUND and not isinstance(result, Mapping):
            raise InvalidConnectorCapabilityResultError(
                "get_single_object must return a record mapping or RECORD_NOT_FOUND"
            )
        return result

    def count(self, endpoint: str, *, since: str | None = None) -> CountResult:
        """Invoke a supported cheap endpoint count."""
        self.require(ConnectorOperation.COUNT)
        implementation = cast("SupportsCount", self._source)
        result = implementation.count(endpoint, since=since)
        if not isinstance(result, CountResult):
            raise InvalidConnectorCapabilityResultError("count must return CountResult")
        return result

    def test_connection(self) -> ConnectionStatus:
        """Invoke a supported source-level connection probe."""
        self.require(ConnectorOperation.TEST_CONNECTION)
        implementation = cast("SupportsTestConnection", self._source)
        result = implementation.test_connection()
        if not isinstance(result, ConnectionStatus):
            raise InvalidConnectorCapabilityResultError(
                "test_connection must return ConnectionStatus"
            )
        return result

    def get_deleted(
        self, endpoint: str, *, since: str | None = None
    ) -> Iterator[Mapping[str, Any]]:
        """Invoke a supported deleted-record feed."""
        self.require(ConnectorOperation.GET_DELETED)
        implementation = cast("SupportsGetDeleted", self._source)
        return implementation.get_deleted(endpoint, since=since)

    def create(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        """Invoke a supported create. Non-idempotent — do not blindly retry on ambiguous failure."""
        self.require(ConnectorOperation.CREATE)
        implementation = cast("SupportsCreate", self._source)
        result = implementation.create(endpoint, record)
        if not isinstance(result, Mapping):
            raise InvalidConnectorCapabilityResultError("create must return a record mapping")
        return result

    def update(
        self,
        endpoint: str,
        identity: Mapping[str, str],
        changes: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Invoke a supported update of the record identified by ``identity``."""
        self.require(ConnectorOperation.UPDATE)
        implementation = cast("SupportsUpdate", self._source)
        result = implementation.update(endpoint, identity, changes)
        if not isinstance(result, Mapping):
            raise InvalidConnectorCapabilityResultError("update must return a record mapping")
        return result

    def upsert(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        """Invoke a supported create-or-update keyed on ``record``'s own business key."""
        self.require(ConnectorOperation.UPSERT)
        implementation = cast("SupportsUpsert", self._source)
        result = implementation.upsert(endpoint, record)
        if not isinstance(result, Mapping):
            raise InvalidConnectorCapabilityResultError("upsert must return a record mapping")
        return result

    def delete(self, endpoint: str, identity: Mapping[str, str]) -> DeleteOutcome:
        """Invoke a supported delete of the record identified by ``identity``."""
        self.require(ConnectorOperation.DELETE)
        implementation = cast("SupportsDelete", self._source)
        result = implementation.delete(endpoint, identity)
        if not isinstance(result, DeleteOutcome):
            raise InvalidConnectorCapabilityResultError("delete must return DeleteOutcome")
        return result
