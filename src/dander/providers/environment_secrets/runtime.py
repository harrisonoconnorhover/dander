"""Environment-only secret runtime selected through the provider registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from dander.providers.environment_secrets.config import EnvironmentSecretConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.security.runtime import SecretCapabilities, SecretRuntime
from dander.security.secret_manager import EnvironmentSecretStore

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel


def build_environment_secrets(
    config: BaseModel,
    context: Mapping[str, object],
) -> SecretRuntime:
    """Build direct environment resolution without loading a cloud SDK."""
    if not isinstance(config, EnvironmentSecretConfig):
        raise TypeError("Environment secret factory received the wrong configuration")
    environment_value = context.get("environment")
    store = EnvironmentSecretStore(
        cast("Mapping[str, str]", environment_value) if environment_value is not None else None
    )
    return SecretRuntime(
        provider_id="environment",
        store=store,
        capabilities=SecretCapabilities(
            provider_id="environment",
            reference_forms=frozenset({"environment_name"}),
            environment_indirection=False,
            audited_access=True,
        ),
    )


ENVIRONMENT_SECRET_FACTORY: ProviderFactory[SecretRuntime] = ProviderFactory(
    kind=ProviderKind.SECRETS,
    provider_id="environment",
    api_version=PROVIDER_API_VERSION,
    build=build_environment_secrets,
)

__all__ = ["ENVIRONMENT_SECRET_FACTORY"]
