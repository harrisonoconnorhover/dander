"""Optional read-only capability discovery and typed invocation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from dander.ingestion import (
    RECORD_NOT_FOUND,
    ConnectionStatus,
    ConnectorOperation,
    CountPrecision,
    CountResult,
    InvalidConnectorCapabilityResultError,
    Source,
    SourceCapabilities,
    SourceConfig,
    UnsupportedConnectorOperationError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


class _PlainSource(Source):
    def discover(self) -> Mapping[str, Any]:
        return {}

    def extract(
        self,
        endpoint: str,
        *,
        since: str | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        del endpoint, since
        return iter(())


class _CapableSource(_PlainSource):
    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        self.calls: list[object] = []

    def get_single_object(
        self,
        endpoint: str,
        identity: Mapping[str, str],
    ) -> Mapping[str, Any] | object:
        self.calls.append(("get_single_object", endpoint, identity))
        if identity.get("id") == "missing":
            return RECORD_NOT_FOUND
        return {"id": identity["id"], "name": "Example"}

    def count(self, endpoint: str, *, since: str | None = None) -> CountResult:
        self.calls.append(("count", endpoint, since))
        return CountResult.exact(7)

    def test_connection(self) -> ConnectionStatus:
        self.calls.append("test_connection")
        return ConnectionStatus(ok=True)


def _config() -> SourceConfig:
    return SourceConfig(
        name="example",
        base_url="https://example.test",
        engine="dlt",
        auth_strategy="none",
    )


def test_plain_source_has_no_optional_operations_and_fails_clearly() -> None:
    capabilities = SourceCapabilities(_PlainSource(_config()))

    assert capabilities.supported_operations == frozenset()
    assert not capabilities.supports(ConnectorOperation.COUNT)
    with pytest.raises(
        UnsupportedConnectorOperationError,
        match="source 'example' does not support operation 'count'",
    ):
        capabilities.count("accounts")


def test_capable_source_is_discovered_and_invoked_through_typed_facade() -> None:
    source = _CapableSource(_config())
    capabilities = SourceCapabilities(source)

    assert capabilities.supported_operations == frozenset(ConnectorOperation)
    assert capabilities.get_single_object("accounts", {"id": "001"}) == {
        "id": "001",
        "name": "Example",
    }
    assert capabilities.get_single_object("accounts", {"id": "missing"}) is RECORD_NOT_FOUND
    assert capabilities.count("accounts", since="2026-08-01") == CountResult.exact(7)
    assert capabilities.test_connection() == ConnectionStatus(ok=True)
    assert source.calls == [
        ("get_single_object", "accounts", {"id": "001"}),
        ("get_single_object", "accounts", {"id": "missing"}),
        ("count", "accounts", "2026-08-01"),
        "test_connection",
    ]


@pytest.mark.parametrize("count", [-1, True, 1.5])
def test_count_result_rejects_invalid_counts(count: object) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        CountResult.exact(count)  # type: ignore[arg-type]


def test_count_result_preserves_precision() -> None:
    assert CountResult.exact(2).precision is CountPrecision.EXACT
    assert CountResult.estimate(3).precision is CountPrecision.ESTIMATE


@pytest.mark.parametrize(
    ("method", "match"),
    [
        ("get_single_object", "must return a record mapping"),
        ("count", "count must return CountResult"),
        ("test_connection", "must return ConnectionStatus"),
    ],
)
def test_facade_rejects_invalid_plugin_results(method: str, match: str) -> None:
    source = _CapableSource(_config())
    setattr(source, method, lambda *args, **kwargs: object())
    capabilities = SourceCapabilities(source)

    with pytest.raises(InvalidConnectorCapabilityResultError, match=match):
        if method == "get_single_object":
            capabilities.get_single_object("accounts", {"id": "001"})
        elif method == "count":
            capabilities.count("accounts")
        else:
            capabilities.test_connection()


def test_initial_operation_set_is_read_only_and_intentionally_small() -> None:
    assert {operation.value for operation in ConnectorOperation} == {
        "count",
        "get_single_object",
        "test_connection",
    }
