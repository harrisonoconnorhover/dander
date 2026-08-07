"""Optional capability discovery and typed invocation, including write-back."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from dander.ingestion import (
    RECORD_NOT_FOUND,
    ConnectionStatus,
    ConnectorOperation,
    CountPrecision,
    CountResult,
    DeleteOutcome,
    InvalidConnectorCapabilityResultError,
    Source,
    SourceCapabilities,
    SourceConfig,
    SupportsCreate,
    SupportsDelete,
    SupportsUpdate,
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

    def get_deleted(
        self, endpoint: str, *, since: str | None = None
    ) -> Iterator[Mapping[str, Any]]:
        self.calls.append(("get_deleted", endpoint, since))
        return iter([{"id": "002"}])

    def create(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(("create", endpoint, record))
        return {**record, "id": "003"}

    def update(
        self,
        endpoint: str,
        identity: Mapping[str, str],
        changes: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.calls.append(("update", endpoint, identity, changes))
        return {**identity, **changes}

    def upsert(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(("upsert", endpoint, record))
        return record

    def delete(self, endpoint: str, identity: Mapping[str, str]) -> DeleteOutcome:
        self.calls.append(("delete", endpoint, identity))
        return DeleteOutcome.NOT_FOUND if identity.get("id") == "missing" else DeleteOutcome.DELETED


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
    assert list(capabilities.get_deleted("accounts", since="2026-08-01")) == [{"id": "002"}]
    assert capabilities.create("accounts", {"name": "New"}) == {"name": "New", "id": "003"}
    assert capabilities.update("accounts", {"id": "001"}, {"name": "Renamed"}) == {
        "id": "001",
        "name": "Renamed",
    }
    assert capabilities.upsert("accounts", {"id": "001", "name": "Upserted"}) == {
        "id": "001",
        "name": "Upserted",
    }
    assert capabilities.delete("accounts", {"id": "001"}) is DeleteOutcome.DELETED
    assert capabilities.delete("accounts", {"id": "missing"}) is DeleteOutcome.NOT_FOUND
    assert source.calls == [
        ("get_single_object", "accounts", {"id": "001"}),
        ("get_single_object", "accounts", {"id": "missing"}),
        ("count", "accounts", "2026-08-01"),
        "test_connection",
        ("get_deleted", "accounts", "2026-08-01"),
        ("create", "accounts", {"name": "New"}),
        ("update", "accounts", {"id": "001"}, {"name": "Renamed"}),
        ("upsert", "accounts", {"id": "001", "name": "Upserted"}),
        ("delete", "accounts", {"id": "001"}),
        ("delete", "accounts", {"id": "missing"}),
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
        ("create", "create must return a record mapping"),
        ("update", "update must return a record mapping"),
        ("upsert", "upsert must return a record mapping"),
        ("delete", "delete must return DeleteOutcome"),
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
        elif method == "test_connection":
            capabilities.test_connection()
        elif method == "create":
            capabilities.create("accounts", {"name": "New"})
        elif method == "update":
            capabilities.update("accounts", {"id": "001"}, {"name": "New"})
        elif method == "upsert":
            capabilities.upsert("accounts", {"id": "001"})
        else:
            capabilities.delete("accounts", {"id": "001"})


def test_get_deleted_does_not_validate_the_lazy_iterator_shape() -> None:
    source = _CapableSource(_config())
    capabilities = SourceCapabilities(source)

    assert capabilities.get_deleted("accounts") is not None


def test_create_is_documented_as_non_idempotent_and_others_as_idempotent() -> None:
    assert SupportsCreate.__doc__ is not None
    assert "non-idempotent" in SupportsCreate.__doc__.lower()
    assert SupportsUpdate.__doc__ is not None
    assert "idempotent" in SupportsUpdate.__doc__.lower()
    assert SupportsDelete.__doc__ is not None
    assert "idempotent" in SupportsDelete.__doc__.lower()


def test_full_operation_set_covers_read_and_write_back() -> None:
    assert {operation.value for operation in ConnectorOperation} == {
        "get_single_object",
        "get_deleted",
        "count",
        "test_connection",
        "create",
        "update",
        "upsert",
        "delete",
    }
