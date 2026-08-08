"""Built-in provider registrations with lazy implementation loading."""

from dander.providers.bigquery import BigQueryStateConfig, BigQueryWarehouseConfig
from dander.providers.dataplex import DataplexCatalogConfig
from dander.providers.no_catalog import NoCatalogConfig
from dander.providers.registry import ProviderKind, ProviderRegistry, lazy_provider_factory


def default_provider_registry() -> ProviderRegistry:
    """Return fresh built-in registrations without importing provider SDK modules."""
    registry = ProviderRegistry()
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="bigquery",
        config_model=BigQueryWarehouseConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.bigquery.runtime:BIGQUERY_WAREHOUSE_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.STATE,
        provider_id="bigquery",
        config_model=BigQueryStateConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.bigquery.state:BIGQUERY_STATE_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.CATALOG,
        provider_id="dataplex",
        config_model=DataplexCatalogConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.dataplex.runtime:DATAPLEX_CATALOG_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.CATALOG,
        provider_id="none",
        config_model=NoCatalogConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.no_catalog.runtime:NO_CATALOG_FACTORY"
        ),
    )
    return registry
