"""Dependency-light BigQuery warehouse configuration."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dander.warehouse.contracts import RelationRef

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")


class BigQueryWarehouseConfig(BaseModel):
    """BigQuery location selected by one named platform profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["bigquery"]
    location: str = Field(default="US", min_length=1)
    dataset: str | None = None

    @field_validator("dataset")
    @classmethod
    def validate_dataset(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("dataset must be a valid BigQuery dataset identifier")
        return value

    def raw_relation(
        self,
        name: str,
        *,
        compatibility_catalog: str | None,
        compatibility_namespace: str | None,
        default_namespace: str,
    ) -> RelationRef:
        """Translate legacy project/dataset inputs at the BigQuery boundary."""
        if not compatibility_catalog:
            raise ValueError("BigQuery warehouse requires a catalog")
        return RelationRef(
            catalog=compatibility_catalog,
            namespace=compatibility_namespace or self.dataset or default_namespace,
            name=name,
        )


class BigQueryStateConfig(BaseModel):
    """BigQuery durable-state selection for one named platform profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["bigquery"]
