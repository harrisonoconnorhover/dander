"""Cloud-provider registration and construction contracts."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from dander.providers.dependencies import (
    FULL_RUNTIME_DISTRIBUTIONS,
    PROVIDER_DEPENDENCY_SETS,
    ProviderDependencySet,
    RuntimeDependencyError,
    installed_runtime_dependencies,
    require_full_runtime,
)
from dander.providers.registry import (
    PROVIDER_API_VERSION,
    ProviderFactory,
    ProviderFactoryError,
    ProviderKind,
    ProviderRegistry,
    lazy_provider_factory,
)

if TYPE_CHECKING:
    from dander.providers.defaults import default_provider_registry


def __getattr__(name: str) -> object:
    """Avoid importing every provider config when one provider submodule is selected."""
    if name == "default_provider_registry":
        return getattr(import_module("dander.providers.defaults"), name)
    raise AttributeError(name)


__all__ = [
    "FULL_RUNTIME_DISTRIBUTIONS",
    "PROVIDER_API_VERSION",
    "PROVIDER_DEPENDENCY_SETS",
    "ProviderDependencySet",
    "ProviderFactory",
    "ProviderFactoryError",
    "ProviderKind",
    "ProviderRegistry",
    "RuntimeDependencyError",
    "installed_runtime_dependencies",
    "default_provider_registry",
    "lazy_provider_factory",
    "require_full_runtime",
]
