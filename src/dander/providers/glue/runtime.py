"""AWS Glue Data Catalog runtime composition."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from dander.catalog.glue import GlueCatalogPublisher
from dander.catalog.runtime import CatalogCapabilities, CatalogRuntime
from dander.providers.glue.config import GlueCatalogConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

    from dander.catalog.glue import GlueClient


def build_glue_catalog(
    config: BaseModel,
    context: Mapping[str, object],
) -> CatalogRuntime:
    """Build Glue only after its catalog provider is selected."""
    if not isinstance(config, GlueCatalogConfig):
        raise TypeError("Glue catalog factory received the wrong configuration")
    warehouse_provider = context.get("warehouse_provider")
    if not isinstance(warehouse_provider, str) or not warehouse_provider:
        raise ValueError("Glue catalog factory requires a warehouse provider context")
    client = context.get("client")
    return CatalogRuntime(
        provider_id="glue",
        publisher=GlueCatalogPublisher(
            region=config.region,
            catalog_id=config.catalog_id,
            database_prefix=config.database_prefix,
            warehouse_provider=warehouse_provider,
            connection_name=config.connection_name,
            client=cast("GlueClient", client) if client is not None else None,
        ),
        capabilities=CatalogCapabilities(
            provider_id="glue",
            readback=True,
            preserves_unrelated_fields=True,
            first_party_entries=False,
        ),
    )


GLUE_CATALOG_FACTORY: ProviderFactory[CatalogRuntime] = ProviderFactory(
    kind=ProviderKind.CATALOG,
    provider_id="glue",
    api_version=PROVIDER_API_VERSION,
    build=build_glue_catalog,
)

__all__ = ["GLUE_CATALOG_FACTORY"]
