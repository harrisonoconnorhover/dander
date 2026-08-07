"""Small curated connector catalog backed by published package metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from dander import __version__

if TYPE_CHECKING:
    from dander.plugins.registry import InstalledConnectorPlugin

CATALOG_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CatalogConnector:
    """Public, non-secret metadata for one curated connector package."""

    connector_id: str
    display_name: str
    description: str
    distribution: str
    version: str
    dander_specifier: str
    support_status: str
    validation_status: str
    documentation_url: str
    pypi_url: str
    repository_url: str

    def matches(self, query: str) -> bool:
        """Return whether a case-insensitive query matches user-facing metadata."""
        normalized = query.strip().casefold()
        if not normalized:
            return True
        searchable = (
            self.connector_id,
            self.display_name,
            self.description,
            self.distribution,
        )
        return any(normalized in value.casefold() for value in searchable)

    def as_dict(
        self,
        *,
        dander_version: str,
        installed_version: str | None,
    ) -> dict[str, object]:
        """Project the catalog entry into the stable HTTP/CLI data shape."""
        try:
            compatible = Version(dander_version) in SpecifierSet(self.dander_specifier)
        except InvalidVersion:
            compatible = False
        return {
            "id": self.connector_id,
            "display_name": self.display_name,
            "description": self.description,
            "distribution": self.distribution,
            "version": self.version,
            "dander_specifier": self.dander_specifier,
            "compatible": compatible,
            "support_status": self.support_status,
            "validation_status": self.validation_status,
            "documentation_url": self.documentation_url,
            "pypi_url": self.pypi_url,
            "repository_url": self.repository_url,
            "installed": installed_version is not None,
            "installed_version": installed_version,
        }


CURATED_CONNECTORS = (
    CatalogConnector(
        connector_id="salesforce",
        display_name="Salesforce",
        description=(
            "Bounded Salesforce Bulk API 2.0 Accounts, Contacts, Opportunities, and Users "
            "ingestion with replay cursors."
        ),
        distribution="dander-connector-salesforce",
        version="0.3.1rc1",
        dander_specifier=">=0.6.0,<0.8",
        support_status="first-party-beta",
        validation_status="provider-validated",
        documentation_url=(
            "https://github.com/harrisonoconnorhover/"
            "dander-connector-salesforce#dander-salesforce-connector"
        ),
        pypi_url="https://pypi.org/project/dander-connector-salesforce/0.3.1rc1/",
        repository_url=("https://github.com/harrisonoconnorhover/dander-connector-salesforce"),
    ),
    CatalogConnector(
        connector_id="servicenow",
        display_name="ServiceNow",
        description="Read-only ServiceNow Table API incident ingestion with stable paging.",
        distribution="dander-connector-servicenow",
        version="0.2.2rc1",
        dander_specifier=">=0.6.0,<0.8",
        support_status="first-party-beta",
        validation_status="provider-validated",
        documentation_url=(
            "https://github.com/harrisonoconnorhover/"
            "dander-connector-servicenow#dander-servicenow-connector"
        ),
        pypi_url="https://pypi.org/project/dander-connector-servicenow/0.2.2rc1/",
        repository_url=("https://github.com/harrisonoconnorhover/dander-connector-servicenow"),
    ),
)


def search_connector_catalog(query: str = "") -> tuple[CatalogConnector, ...]:
    """Search the curated catalog without contacting a package index."""
    return tuple(connector for connector in CURATED_CONNECTORS if connector.matches(query))


def build_plugin_catalog(
    installed_plugins: tuple[InstalledConnectorPlugin, ...] = (),
    *,
    dander_version: str = __version__,
    query: str = "",
) -> dict[str, object]:
    """Build the presentation-safe catalog and manifest-scoped installation status."""
    installed_versions = {
        canonicalize_name(plugin.distribution): plugin.version for plugin in installed_plugins
    }
    connectors = [
        connector.as_dict(
            dander_version=dander_version,
            installed_version=installed_versions.get(canonicalize_name(connector.distribution)),
        )
        for connector in search_connector_catalog(query)
    ]
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "dander_version": dander_version,
        "connectors": connectors,
    }
