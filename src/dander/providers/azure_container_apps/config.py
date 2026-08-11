"""Validated Azure Container Apps Jobs launcher configuration without SDK imports."""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_RESOURCE_NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
_ACR_NAME = re.compile(r"^[a-z][a-z0-9]{4,49}$")
_KEY_VAULT_NAME = re.compile(r"^[a-z][a-z0-9-]{1,22}[a-z0-9]$")
_LOCATION = re.compile(r"^[a-z][a-z0-9]{1,31}$")


class AzureContainerAppsLauncherConfig(BaseModel):
    """Select one pre-authorized Azure identity and Container Apps environment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["azure_container_apps"]
    region: str = Field(pattern=_LOCATION.pattern)
    subscription_id: UUID
    resource_group_name: str = Field(pattern=_RESOURCE_NAME.pattern)
    container_app_environment_name: str = Field(pattern=_RESOURCE_NAME.pattern)
    acr_name: str = Field(pattern=_ACR_NAME.pattern)
    key_vault_name: str = Field(pattern=_KEY_VAULT_NAME.pattern)
    managed_identity_name: str = Field(pattern=_RESOURCE_NAME.pattern)
    managed_identity_client_id: UUID
    google_workload_identity_audience: str | None = Field(
        default=None,
        pattern=(
            r"^//iam\.googleapis\.com/projects/[0-9]{6,20}/locations/global/"
            r"workloadIdentityPools/[a-z][a-z0-9-]{3,31}/providers/"
            r"[a-z][a-z0-9-]{3,31}$"
        ),
    )
    google_application_id_uri: str | None = Field(
        default=None,
        pattern=(
            r"^api://[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
            r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
        ),
    )

    @field_validator("subscription_id", "managed_identity_client_id")
    @classmethod
    def require_rfc4122_uuid(cls, value: UUID) -> UUID:
        if value.variant != "specified in RFC 4122":
            raise ValueError("Azure identifiers must be RFC 4122 UUIDs")
        return value

    @field_validator("key_vault_name")
    @classmethod
    def reject_ambiguous_key_vault_name(cls, value: str) -> str:
        if "--" in value:
            raise ValueError("Azure Key Vault names must not contain consecutive hyphens")
        return value

    @model_validator(mode="after")
    def require_complete_google_federation(self) -> AzureContainerAppsLauncherConfig:
        """Keep the optional cross-cloud identity boundary all-or-nothing."""
        configured = (
            self.google_workload_identity_audience,
            self.google_application_id_uri,
        )
        if any(configured) and not all(configured):
            raise ValueError(
                "Azure Google federation requires both audience and application ID URI"
            )
        return self

    @property
    def acr_login_server(self) -> str:
        return f"{self.acr_name}.azurecr.io"

    @property
    def key_vault_uri(self) -> str:
        return f"https://{self.key_vault_name}.vault.azure.net"

    @property
    def managed_identity_resource_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}"
            "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
            f"{self.managed_identity_name}"
        )

    @property
    def container_app_environment_resource_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}"
            "/providers/Microsoft.App/managedEnvironments/"
            f"{self.container_app_environment_name}"
        )
