"""Catalog module: the metadata spine (cloud catalog + semantic/agent registry)."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from dander.catalog.publisher import CatalogPublisher, CatalogPublishError
from dander.catalog.registry import SemanticRegistryError, SemanticRegistryPublisher
from dander.catalog.runtime import CatalogCapabilities, CatalogRuntime
from dander.catalog.spine import (
    CatalogAsset,
    CatalogColumn,
    MetadataSpine,
    MetricDefinition,
    TestContract,
)
from dander.catalog.store import (
    BigQueryMetadataStore,
    MetadataSnapshot,
    MetadataStore,
    SqliteMetadataStore,
)

if TYPE_CHECKING:
    from dander.catalog.dataplex import (
        DataplexAspectGenerator,
        DataplexCatalogPublisher,
        GeneratedAspect,
    )

_DATAPLEX_EXPORTS = frozenset(
    {"DataplexAspectGenerator", "DataplexCatalogPublisher", "GeneratedAspect"}
)


def __getattr__(name: str) -> object:
    """Load legacy Dataplex exports only when a caller explicitly requests them."""
    if name in _DATAPLEX_EXPORTS:
        return getattr(import_module("dander.catalog.dataplex"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CatalogAsset",
    "CatalogColumn",
    "CatalogPublishError",
    "CatalogPublisher",
    "CatalogCapabilities",
    "CatalogRuntime",
    "BigQueryMetadataStore",
    "DataplexAspectGenerator",
    "DataplexCatalogPublisher",
    "GeneratedAspect",
    "MetadataSpine",
    "MetadataSnapshot",
    "MetadataStore",
    "MetricDefinition",
    "SemanticRegistryError",
    "SemanticRegistryPublisher",
    "SqliteMetadataStore",
    "TestContract",
]
