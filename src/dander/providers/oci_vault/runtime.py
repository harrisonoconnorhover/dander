"""OCI Vault runtime selected through the provider registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from dander.providers.oci_vault.config import OciVaultConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.security.oci_vault import OciVaultSecretStore
from dander.security.runtime import SecretCapabilities, SecretRuntime

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from pydantic import BaseModel


def build_oci_vault(
    config: BaseModel,
    context: Mapping[str, object],
) -> SecretRuntime:
    """Build a lazy resource-principal Vault resolver after explicit selection."""
    if not isinstance(config, OciVaultConfig):
        raise TypeError("OCI Vault factory received the wrong configuration")
    return SecretRuntime(
        provider_id="oci_vault",
        store=OciVaultSecretStore(client=cast("Any", context.get("client"))),
        capabilities=SecretCapabilities(
            provider_id="oci_vault",
            reference_forms=frozenset(
                {
                    "oci_vault_secret_ocid",
                    "oci_vault_secret_name",
                }
            ),
            environment_indirection=False,
            audited_access=True,
        ),
    )


OCI_VAULT_FACTORY: ProviderFactory[SecretRuntime] = ProviderFactory(
    kind=ProviderKind.SECRETS,
    provider_id="oci_vault",
    api_version=PROVIDER_API_VERSION,
    build=build_oci_vault,
)

__all__ = ["OCI_VAULT_FACTORY"]
