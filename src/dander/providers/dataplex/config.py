"""Dependency-light Dataplex catalog configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DataplexCatalogConfig(BaseModel):
    """Select Dataplex catalog publication for one platform profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["dataplex"]
