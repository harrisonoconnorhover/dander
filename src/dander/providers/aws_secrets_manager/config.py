"""Validated AWS Secrets Manager provider configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AwsSecretsManagerConfig(BaseModel):
    """Select AWS Secrets Manager in one AWS region."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["aws_secret_manager"]
    region: str = Field(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
