"""Dependency-light OCI Vault configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class OciVaultConfig(BaseModel):
    """Select launcher-native OCI Vault secret resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["oci_vault"]
