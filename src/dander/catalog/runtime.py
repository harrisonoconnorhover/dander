"""Provider-neutral cloud-catalog runtime composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dander.catalog.publisher import CatalogPublisher


@dataclass(frozen=True, slots=True)
class CatalogCapabilities:
    """Publication guarantees exposed by one cloud-catalog provider."""

    provider_id: str
    readback: bool
    preserves_unrelated_fields: bool
    first_party_entries: bool

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("catalog capabilities require a provider id")


@dataclass(frozen=True, slots=True)
class CatalogRuntime:
    """A selected cloud catalog and its optional asset publisher."""

    provider_id: str
    publisher: CatalogPublisher | None
    capabilities: CatalogCapabilities

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("catalog runtime requires a provider id")
        if self.capabilities.provider_id != self.provider_id:
            raise ValueError("catalog runtime and capabilities provider ids must match")
