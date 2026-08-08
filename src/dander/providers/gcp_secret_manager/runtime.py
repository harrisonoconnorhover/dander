"""GCP Secret Manager runtime selected through the provider registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from dander.providers.gcp_secret_manager.config import GcpSecretManagerConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.security.runtime import SecretCapabilities, SecretRuntime
from dander.security.secret_manager import (
    DefaultSecretStore,
    EnvironmentSecretStore,
    GcpSecretStore,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from pydantic import BaseModel


def build_gcp_secret_manager(
    config: BaseModel,
    context: Mapping[str, object],
) -> SecretRuntime:
    """Build the compatibility resolver without eagerly constructing a Google client."""
    if not isinstance(config, GcpSecretManagerConfig):
        raise TypeError("GCP Secret Manager factory received the wrong configuration")
    environment_value = context.get("environment")
    client = context.get("client")
    environment = (
        EnvironmentSecretStore(cast("Mapping[str, str]", environment_value))
        if environment_value is not None
        else None
    )
    gcp = GcpSecretStore(cast("Any", client)) if client is not None else None
    return SecretRuntime(
        provider_id="gcp_secret_manager",
        store=DefaultSecretStore(environment=environment, gcp=gcp),
        capabilities=SecretCapabilities(
            provider_id="gcp_secret_manager",
            reference_forms=frozenset({"environment_name", "gcp_resource_name"}),
            environment_indirection=True,
            audited_access=True,
        ),
    )


GCP_SECRET_MANAGER_FACTORY: ProviderFactory[SecretRuntime] = ProviderFactory(
    kind=ProviderKind.SECRETS,
    provider_id="gcp_secret_manager",
    api_version=PROVIDER_API_VERSION,
    build=build_gcp_secret_manager,
)

__all__ = ["GCP_SECRET_MANAGER_FACTORY"]
