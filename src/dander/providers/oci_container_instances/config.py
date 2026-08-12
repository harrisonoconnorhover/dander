"""Validated OCI Container Instances configuration without SDK imports."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_REGION = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-[1-9][0-9]*$")
_TENANCY_OCID = re.compile(r"^ocid1\.tenancy\.oc[0-9]+\.\.[A-Za-z0-9]+$")
_COMPARTMENT_OCID = re.compile(r"^ocid1\.compartment\.oc[0-9]+\.\.[A-Za-z0-9]+$")
_SUBNET_OCID = re.compile(r"^ocid1\.subnet\.oc[0-9]+\.[a-z0-9-]+\.[A-Za-z0-9]+$")
_VAULT_OCID = re.compile(r"^ocid1\.vault\.oc[0-9]+\.[a-z0-9-]+\.[A-Za-z0-9]+$")
_AVAILABILITY_DOMAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:-]{0,254}$")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,254}$")
_DYNAMIC_GROUP = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,99}$")


class OciContainerInstancesLauncherConfig(BaseModel):
    """Select one reviewed OCI compartment, network, registry, and identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["oci_container_instances"]
    region: str = Field(pattern=_REGION.pattern)
    tenancy_id: str = Field(pattern=_TENANCY_OCID.pattern)
    compartment_id: str = Field(pattern=_COMPARTMENT_OCID.pattern)
    availability_domain: str = Field(pattern=_AVAILABILITY_DOMAIN.pattern)
    subnet_id: str = Field(pattern=_SUBNET_OCID.pattern)
    registry_namespace: str = Field(pattern=_NAMESPACE.pattern)
    repository_name: str = Field(pattern=_REPOSITORY.pattern)
    vault_id: str = Field(pattern=_VAULT_OCID.pattern)
    dynamic_group_name: str = Field(pattern=_DYNAMIC_GROUP.pattern)
    shape: Literal[
        "CI.Standard.E4.Flex",
        "CI.Standard.E5.Flex",
        "CI.Standard.A1.Flex",
    ] = "CI.Standard.E4.Flex"
    assign_public_ip: bool = False
    graceful_shutdown_seconds: int = Field(default=120, ge=0)

    @field_validator("repository_name")
    @classmethod
    def reject_ambiguous_repository_path(cls, value: str) -> str:
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("OCI repository_name must contain unambiguous path segments")
        return value

    @property
    def registry_endpoint(self) -> str:
        return f"ocir.{self.region}.oci.oraclecloud.com"

    @property
    def repository(self) -> str:
        return f"{self.registry_endpoint}/{self.registry_namespace}/{self.repository_name}"

    @property
    def resource_principal_identity(self) -> str:
        return f"oci-resource-principal://dynamic-group/{self.dynamic_group_name}"
