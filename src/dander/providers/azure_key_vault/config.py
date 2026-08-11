"""Dependency-light Azure Key Vault configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class AzureKeyVaultConfig(BaseModel):
    """Select launcher-native Azure Key Vault secret projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["azure_key_vault"]
