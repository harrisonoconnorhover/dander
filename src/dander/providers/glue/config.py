"""Validated AWS Glue Data Catalog provider configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GlueCatalogConfig(BaseModel):
    """Select one account-scoped Glue Data Catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["glue"]
    region: str = Field(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
    catalog_id: str = Field(pattern=r"^[0-9]{12}$")
    database_prefix: str = Field(
        default="dander",
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    connection_name: str | None = Field(default=None, min_length=1, max_length=255)
