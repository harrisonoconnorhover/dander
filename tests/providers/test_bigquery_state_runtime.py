"""BigQuery state provider composition and migration coverage."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import pytest

from dander.catalog import BigQueryMetadataStore
from dander.providers import ProviderKind, default_provider_registry
from dander.state import (
    BigQueryLeaseStore,
    BigQueryRunHistoryStore,
    BigQueryWatermarkStore,
    StateRuntime,
)

if TYPE_CHECKING:
    from pytest import MonkeyPatch


class _Job:
    num_dml_affected_rows = 1

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def result(self) -> list[dict[str, Any]]:
        return self._rows


class _Client:
    def __init__(self, *, version: int = 0) -> None:
        self.version = version
        self.queries: list[str] = []

    def query(self, query: str, *, job_config: object | None = None) -> _Job:
        del job_config
        self.queries.append(query)
        if "SELECT COALESCE(MAX(version), 0)" in query:
            return _Job([{"version": self.version}])
        if query.startswith("MERGE `") and "._dander_state_schema`" in query:
            self.version = 1
        return _Job()


def _build(client: _Client, *, metadata_enabled: bool = True) -> StateRuntime:
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.STATE, {"provider": "bigquery"})
    runtime = registry.build(
        ProviderKind.STATE,
        config,
        context={
            "project": "unit-project",
            "raw_dataset": "raw",
            "metadata_dataset": "dander_meta",
            "project_pipeline": True,
            "metadata_enabled": metadata_enabled,
            "client": client,
        },
    )
    assert isinstance(runtime, StateRuntime)
    return runtime


def test_default_registry_loads_bigquery_state_only_after_selection() -> None:
    module_name = "dander.providers.bigquery.state"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()

    config = registry.parse(ProviderKind.STATE, {"provider": "bigquery"})

    assert module_name not in sys.modules
    runtime = registry.build(
        ProviderKind.STATE,
        config,
        context={
            "project": "unit-project",
            "raw_dataset": "raw",
            "metadata_dataset": "dander_meta",
            "project_pipeline": True,
            "metadata_enabled": True,
            "client": _Client(),
        },
    )
    assert isinstance(runtime, StateRuntime)
    assert module_name in sys.modules


def test_bigquery_state_runtime_preserves_existing_table_locations() -> None:
    runtime = _build(_Client())

    assert isinstance(runtime.leases, BigQueryLeaseStore)
    assert isinstance(runtime.watermarks, BigQueryWatermarkStore)
    assert isinstance(runtime.history, BigQueryRunHistoryStore)
    assert isinstance(runtime.metadata, BigQueryMetadataStore)
    assert runtime.leases._table_prefix == "unit-project.dander_meta._dander_lease_"
    assert runtime.watermarks._table_id == "unit-project.raw._dander_watermarks"
    assert runtime.history._table == "unit-project.dander_meta._dander_runs"
    assert runtime.metadata._table == "unit-project.dander_meta._dander_catalog"
    assert runtime.capabilities.server_time is True
    assert runtime.capabilities.atomic_watermark_cas is True
    assert runtime.capabilities.interrupted_run_reconciliation is True


def test_bigquery_state_migration_is_versioned_and_idempotent() -> None:
    client = _Client()
    runtime = _build(client)

    assert runtime.migrator.migrate() == 1
    first_queries = tuple(client.queries)

    assert client.version == 1
    assert "._dander_state_schema`" in first_queries[0]
    assert any("._dander_watermarks`" in query for query in first_queries)
    assert any("._dander_runs`" in query for query in first_queries)
    assert any("._dander_catalog`" in query for query in first_queries)
    assert first_queries[-1].startswith("MERGE `")

    before_second = len(client.queries)
    assert runtime.migrator.migrate() == 1
    second_queries = client.queries[before_second:]
    assert len(second_queries) == 1
    assert "SELECT COALESCE(MAX(version), 0)" in second_queries[0]


def test_failed_state_migration_is_not_recorded(
    monkeypatch: MonkeyPatch,
) -> None:
    client = _Client()
    runtime = _build(client)

    def fail() -> None:
        raise RuntimeError("history migration failed")

    monkeypatch.setattr(runtime.history, "migrate", fail)

    with pytest.raises(RuntimeError, match="history migration failed"):
        runtime.migrator.migrate()

    assert client.version == 0
    assert not any(
        query.startswith("MERGE `") and "._dander_state_schema`" in query
        for query in client.queries
    )
