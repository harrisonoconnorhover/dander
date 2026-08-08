"""Dependency-light Cloud Run launcher configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CloudRunLauncherConfig(BaseModel):
    """Select Cloud Run Jobs in one GCP region."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["cloud_run"]
    region: str = Field(default="us-central1", min_length=1)
