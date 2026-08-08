"""Dependency-light GCP Secret Manager configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class GcpSecretManagerConfig(BaseModel):
    """Select GCP Secret Manager with legacy environment indirection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["gcp_secret_manager"]
