"""Typed discovery and invocation for optional read-only source capabilities.

``Source.discover`` and ``Source.extract`` remain the mandatory connector contract. Provider
APIs may also offer cheaper, targeted read operations. Those operations stay structural so an
independently installed connector can opt in without changing Dander's plugin API or adding stub
methods to every source.

The initial contract deliberately contains no deleted-record feed or provider mutation. Those
operations need separate cursor, destination, retry, and safety semantics before Dander can
claim to support them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from dander.ingestion.source import Source


class ConnectorOperation(StrEnum):
    """Read-only operations a concrete ``Source`` may optionally implement."""

    GET_SINGLE_OBJECT = "get_single_object"
    COUNT = "count"
    TEST_CONNECTION = "test_connection"


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


_CAPABILITY_PROTOCOLS: Final[dict[ConnectorOperation, type]] = {
    ConnectorOperation.GET_SINGLE_OBJECT: SupportsGetSingleObject,
    ConnectorOperation.COUNT: SupportsCount,
    ConnectorOperation.TEST_CONNECTION: SupportsTestConnection,
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
