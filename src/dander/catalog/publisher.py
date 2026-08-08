"""Metadata spine — 'define once, project everywhere'.

One model/source YAML projects to executable SQL **and** data-catalog aspects (Dataplex) **and** a
semantic/agent registry. This is a core differentiator (see ``steering/00-project-overview.md``):
the same metadata that compiles the SQL also documents the asset for the catalog and the agents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from dander.catalog.spine import CatalogAsset


class CatalogPublishError(RuntimeError):
    """Raised when a catalog request is invalid or cannot be completed."""


class CatalogPublisher(Protocol):
    """Publishes generated metadata for a catalog asset."""

    def publish(self, asset: CatalogAsset) -> str:
        """Publish one asset and return the provider resource name."""
        ...
