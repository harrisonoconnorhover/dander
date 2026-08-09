"""Dataplex cloud-catalog runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dander.catalog.dataplex import DataplexCatalogPublisher
from dander.catalog.runtime import CatalogCapabilities, CatalogRuntime
from dander.providers.dataplex.config import DataplexCatalogConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from pydantic import BaseModel


def build_dataplex_catalog(
    config: BaseModel,
    context: Mapping[str, object],
) -> CatalogRuntime:
    """Build Dataplex only after its catalog provider is selected."""
    if not isinstance(config, DataplexCatalogConfig):
        raise TypeError("Dataplex catalog factory received the wrong configuration")
    project = context.get("catalog", context.get("project"))
    location = context.get("location")
    if not isinstance(project, str) or not project:
        raise ValueError("Dataplex catalog factory requires a catalog context")
    if not isinstance(location, str) or not location:
        raise ValueError("Dataplex catalog factory requires a location context")
    client: Any = context.get("client")
    publisher = DataplexCatalogPublisher(
        project=project,
        location=location,
        client=client,
    )
    return CatalogRuntime(
        provider_id="dataplex",
        publisher=publisher,
        capabilities=CatalogCapabilities(
            provider_id="dataplex",
            readback=True,
            preserves_unrelated_fields=True,
            first_party_entries=True,
        ),
    )


DATAPLEX_CATALOG_FACTORY: ProviderFactory[CatalogRuntime] = ProviderFactory(
    kind=ProviderKind.CATALOG,
    provider_id="dataplex",
    api_version=PROVIDER_API_VERSION,
    build=build_dataplex_catalog,
)

__all__ = ["DATAPLEX_CATALOG_FACTORY"]
