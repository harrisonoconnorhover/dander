"""Dependency-light environment secret-provider configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class EnvironmentSecretConfig(BaseModel):
    """Select direct environment-variable secret resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["environment"]
