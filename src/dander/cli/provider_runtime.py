"""Small CLI composition helpers for selected provider runtimes."""

from click import ClickException

from dander.catalog import CatalogPublisher, CatalogRuntime
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry


def build_catalog_publisher(
    *,
    provider_id: str,
    project: str,
    location: str,
) -> CatalogPublisher:
    """Build the selected external catalog publisher or fail when it is disabled."""
    registry = default_provider_registry()
    try:
        config = registry.parse(ProviderKind.CATALOG, {"provider": provider_id})
        runtime = registry.build(
            ProviderKind.CATALOG,
            config,
            context={"project": project, "location": location},
        )
    except ProviderFactoryError as error:
        raise ClickException(str(error)) from error
    if not isinstance(runtime, CatalogRuntime):
        raise ClickException("Selected catalog provider returned an invalid runtime")
    if runtime.publisher is None:
        raise ClickException(f"Catalog provider {provider_id!r} does not publish assets")
    return runtime.publisher
