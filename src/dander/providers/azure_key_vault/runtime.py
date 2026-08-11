"""Azure Key Vault runtime selected through the provider registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from dander.providers.azure_key_vault.config import AzureKeyVaultConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.security.azure_key_vault import AzureKeyVaultSecretStore
from dander.security.runtime import SecretCapabilities, SecretRuntime

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from pydantic import BaseModel


def build_azure_key_vault(
    config: BaseModel,
    context: Mapping[str, object],
) -> SecretRuntime:
    """Build a lazy Key Vault resolver without constructing Azure SDK clients eagerly."""
    if not isinstance(config, AzureKeyVaultConfig):
        raise TypeError("Azure Key Vault factory received the wrong configuration")
    return SecretRuntime(
        provider_id="azure_key_vault",
        store=AzureKeyVaultSecretStore(client=cast("Any", context.get("client"))),
        capabilities=SecretCapabilities(
            provider_id="azure_key_vault",
            reference_forms=frozenset({"azure_key_vault_secret_uri"}),
            environment_indirection=False,
            audited_access=True,
        ),
    )


AZURE_KEY_VAULT_FACTORY: ProviderFactory[SecretRuntime] = ProviderFactory(
    kind=ProviderKind.SECRETS,
    provider_id="azure_key_vault",
    api_version=PROVIDER_API_VERSION,
    build=build_azure_key_vault,
)

__all__ = ["AZURE_KEY_VAULT_FACTORY"]
