"""Validated ECS/Fargate launcher configuration without AWS SDK imports."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SUBNET_ID = re.compile(r"^subnet-(?:[0-9a-f]{8}|[0-9a-f]{17})$")
_SECURITY_GROUP_ID = re.compile(r"^sg-(?:[0-9a-f]{8}|[0-9a-f]{17})$")


class FargateLauncherConfig(BaseModel):
    """Select one existing AWS network for Fargate tasks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["fargate"]
    region: str = Field(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
    aws_account_id: str = Field(pattern=r"^[0-9]{12}$")
    google_workload_identity_audience: str | None = Field(
        default=None,
        pattern=(
            r"^//iam\.googleapis\.com/projects/[0-9]{6,20}/locations/global/"
            r"workloadIdentityPools/[a-z][a-z0-9-]{3,31}/providers/[a-z][a-z0-9-]{3,31}$"
        ),
    )
    subnet_ids: tuple[str, ...] = Field(min_length=1)
    security_group_ids: tuple[str, ...] = Field(min_length=1)
    architecture: Literal["ARM64", "X86_64"] = "ARM64"
    assign_public_ip: bool = False
    ephemeral_storage_mib: int = Field(default=20_480, ge=20_480, le=204_800)
    stop_timeout_seconds: int = Field(default=120, ge=2, le=120)

    @field_validator("subnet_ids")
    @classmethod
    def validate_subnets(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Require deterministic, unique subnet identifiers."""
        if values != tuple(sorted(set(values))) or any(
            _SUBNET_ID.fullmatch(value) is None for value in values
        ):
            raise ValueError("subnet_ids must be unique, sorted AWS subnet IDs")
        return values

    @field_validator("security_group_ids")
    @classmethod
    def validate_security_groups(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Require deterministic, unique security-group identifiers."""
        if values != tuple(sorted(set(values))) or any(
            _SECURITY_GROUP_ID.fullmatch(value) is None for value in values
        ):
            raise ValueError("security_group_ids must be unique, sorted AWS security-group IDs")
        return values
