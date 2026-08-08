"""Built-in provider registrations with lazy implementation loading."""

from dander.providers.bigquery import BigQueryStateConfig, BigQueryWarehouseConfig
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
    return registry
