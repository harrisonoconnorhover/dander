"""Explicit no-op cloud-catalog runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dander.catalog.runtime import CatalogCapabilities, CatalogRuntime
from dander.providers.no_catalog.config import NoCatalogConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel


def build_no_catalog(
    config: BaseModel,
    context: Mapping[str, object],
) -> CatalogRuntime:
    """Build an explicit runtime that performs no external publication."""
    del context
    if not isinstance(config, NoCatalogConfig):
        raise TypeError("No-catalog factory received the wrong configuration")
    return CatalogRuntime(
        provider_id="none",
        publisher=None,
        capabilities=CatalogCapabilities(
            provider_id="none",
            readback=False,
            preserves_unrelated_fields=True,
            first_party_entries=False,
        ),
    )


NO_CATALOG_FACTORY: ProviderFactory[CatalogRuntime] = ProviderFactory(
    kind=ProviderKind.CATALOG,
    provider_id="none",
    api_version=PROVIDER_API_VERSION,
    build=build_no_catalog,
)

__all__ = ["NO_CATALOG_FACTORY"]
