"""Dependency-light BigQuery warehouse configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BigQueryWarehouseConfig(BaseModel):
    """BigQuery location selected by one named platform profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["bigquery"]
    location: str = Field(default="US", min_length=1)


class BigQueryStateConfig(BaseModel):
    """BigQuery durable-state selection for one named platform profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["bigquery"]
