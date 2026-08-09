"""Cloud-catalog provider selection and composition coverage."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest
from click import ClickException

from dander.catalog import CatalogRuntime
from dander.cli.provider_runtime import build_catalog_publisher
from dander.providers import ProviderKind, default_provider_registry

if TYPE_CHECKING:
    from google.cloud import dataplex_v1


class _Client:
    def modify_entry(self, request: dataplex_v1.ModifyEntryRequest) -> object:
        del request
        return object()

    def get_entry(self, request: dataplex_v1.GetEntryRequest) -> dataplex_v1.Entry:
        del request
        raise AssertionError("readback is not used by this composition test")


def test_default_registry_loads_dataplex_only_after_selection() -> None:
    module_name = "dander.providers.dataplex.runtime"
    implementation_module = "dander.catalog.dataplex"
    sys.modules.pop(module_name, None)
    sys.modules.pop(implementation_module, None)
    registry = default_provider_registry()

    config = registry.parse(ProviderKind.CATALOG, {"provider": "dataplex"})

    assert module_name not in sys.modules
    assert implementation_module not in sys.modules
    runtime = registry.build(
        ProviderKind.CATALOG,
        config,
        context={"project": "unit-project", "location": "us", "client": _Client()},
    )
    assert isinstance(runtime, CatalogRuntime)
    assert module_name in sys.modules
    assert implementation_module in sys.modules
    assert runtime.publisher is not None
    assert type(runtime.publisher).__name__ == "DataplexCatalogPublisher"
    assert runtime.capabilities.readback is True
    assert runtime.capabilities.preserves_unrelated_fields is True
    assert runtime.capabilities.first_party_entries is True


def test_no_catalog_runtime_has_no_publisher_or_dataplex_import() -> None:
    dataplex_module = "dander.providers.dataplex.runtime"
    implementation_module = "dander.catalog.dataplex"
    no_catalog_module = "dander.providers.no_catalog.runtime"
    sys.modules.pop(dataplex_module, None)
    sys.modules.pop(implementation_module, None)
    sys.modules.pop(no_catalog_module, None)
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.CATALOG, {"provider": "none"})

    runtime = registry.build(ProviderKind.CATALOG, config)

    assert isinstance(runtime, CatalogRuntime)
    assert runtime.provider_id == "none"
    assert runtime.publisher is None
    assert runtime.capabilities.readback is False
    assert no_catalog_module in sys.modules
    assert dataplex_module not in sys.modules
    assert implementation_module not in sys.modules


def test_default_registry_loads_glue_only_after_selection() -> None:
    module_name = "dander.providers.glue.runtime"
    implementation_module = "dander.catalog.glue"
    sys.modules.pop(module_name, None)
    sys.modules.pop(implementation_module, None)
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.CATALOG,
        {
            "provider": "glue",
            "region": "us-east-1",
            "catalog_id": "123456789012",
        },
    )

    assert module_name not in sys.modules
    assert implementation_module not in sys.modules
    runtime = registry.build(
        ProviderKind.CATALOG,
        config,
        context={"warehouse_provider": "redshift", "client": object()},
    )

    assert isinstance(runtime, CatalogRuntime)
    assert runtime.provider_id == "glue"
    assert module_name in sys.modules
    assert implementation_module in sys.modules
    assert runtime.publisher is not None
    assert type(runtime.publisher).__name__ == "GlueCatalogPublisher"
    assert runtime.capabilities.readback is True
    assert runtime.capabilities.preserves_unrelated_fields is True
    assert runtime.capabilities.first_party_entries is False


def test_cli_rejects_publication_when_catalog_is_disabled() -> None:
    with pytest.raises(ClickException, match="does not publish assets"):
        build_catalog_publisher(
            provider_id="none",
            project="unit-project",
            location="us",
        )


def test_cli_rejects_mismatched_catalog_provider_configuration() -> None:
    with pytest.raises(ClickException, match="id and configuration must match"):
        build_catalog_publisher(
            provider_id="dataplex",
            provider_config={"provider": "none"},
            project="unit-project",
            location="us",
        )
