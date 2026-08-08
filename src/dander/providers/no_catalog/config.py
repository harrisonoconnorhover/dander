"""Explicit disabled-catalog configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class NoCatalogConfig(BaseModel):
    """Select no external cloud-catalog publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["none"]
