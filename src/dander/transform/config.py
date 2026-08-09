"""Typed YAML boundary models for transform metadata."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dander.transform.model import Materialization, SqlDialect

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
type Scalar = str | int | float | bool

if TYPE_CHECKING:
    from pathlib import Path


class TransformConfigError(ValueError):
    """Raised when model metadata is missing, unsafe, or internally inconsistent."""


class ColumnMetadata(BaseModel):
    """Catalog and warehouse metadata for one model column."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=_IDENTIFIER.pattern)
    data_type: str = Field(alias="type", min_length=1)
    description: str = Field(min_length=1)


class RelationshipMetadata(BaseModel):
    """Target relation and field for a referential-integrity assertion."""

    model_config = ConfigDict(extra="forbid")

    to: str = Field(pattern=_IDENTIFIER.pattern)
    field: str = Field(pattern=_IDENTIFIER.pattern)


class GenericTestMetadata(BaseModel):
    """Declarative generic tests applied to one model column."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(pattern=_IDENTIFIER.pattern)
    not_null: bool = False
    unique: bool = False
    accepted_values: list[Scalar] | None = None
    relationships: RelationshipMetadata | None = None

    @model_validator(mode="after")
    def require_assertion(self) -> GenericTestMetadata:
        """Reject empty test declarations and empty accepted-value sets."""
        if self.accepted_values == []:
            raise ValueError("accepted_values must contain at least one value")
        if not (
            self.not_null
            or self.unique
            or self.accepted_values is not None
            or self.relationships is not None
        ):
            raise ValueError("a generic test must enable at least one assertion")
        return self


class MetricAggregation(StrEnum):
    """Supported governed metric calculations."""

    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    SUM = "sum"
    AVERAGE = "average"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


class MetricMetadata(BaseModel):
    """A business metric definition projected through the metadata spine."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=_IDENTIFIER.pattern)
    description: str = Field(min_length=1)
    aggregation: MetricAggregation
    field: str | None = Field(default=None, pattern=_IDENTIFIER.pattern)

    @model_validator(mode="after")
    def require_field_for_value_aggregation(self) -> MetricMetadata:
        if self.aggregation is not MetricAggregation.COUNT and self.field is None:
            raise ValueError("non-count metrics require a field")
        return self


class ModelMetadata(BaseModel):
    """Validated metadata spine for a SQL model."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(pattern=_IDENTIFIER.pattern)
    description: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    dialect: SqlDialect = SqlDialect.BIGQUERY
    materialization: Materialization = Materialization.VIEW
    dataset: str = Field(default="staging", pattern=_IDENTIFIER.pattern)
    source_system: str = Field(min_length=1)
    sensitivity: str = Field(min_length=1)
    columns: list[ColumnMetadata] = Field(min_length=1)
    tests: list[GenericTestMetadata] = Field(default_factory=list)
    metrics: list[MetricMetadata] = Field(default_factory=list)
    unique_key: list[str] = Field(default_factory=list)
    incremental_cursor: str | None = None

    @property
    def namespace(self) -> str:
        """Return the canonical namespace for the legacy model dataset field."""
        return self.dataset

    @model_validator(mode="after")
    def validate_columns_and_tests(self) -> ModelMetadata:
        """Require unique columns and test references that resolve within the model."""
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("model column names must be unique")
        declared = set(names)
        if unknown := sorted({test.column for test in self.tests} - declared):
            raise ValueError(f"tests reference undeclared columns: {', '.join(unknown)}")
        metric_names = [metric.name for metric in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("metric names must be unique within a model")
        if unknown_metric_fields := sorted(
            {metric.field for metric in self.metrics if metric.field is not None} - declared
        ):
            raise ValueError(
                "metrics reference undeclared columns: " + ", ".join(unknown_metric_fields)
            )
        if self.materialization is Materialization.INCREMENTAL:
            if not self.unique_key:
                raise ValueError("incremental models require unique_key")
            if self.incremental_cursor is None:
                raise ValueError("incremental models require incremental_cursor")
            if unknown_keys := sorted(set(self.unique_key) - declared):
                raise ValueError(
                    "incremental unique_key references undeclared columns: "
                    f"{', '.join(unknown_keys)}"
                )
            if self.incremental_cursor not in declared:
                raise ValueError("incremental_cursor references an undeclared column")
            if len(self.unique_key) != len(set(self.unique_key)):
                raise ValueError("incremental unique_key columns must be unique")
        elif self.unique_key or self.incremental_cursor is not None:
            raise ValueError("incremental settings require incremental materialization")
        return self


def load_model_metadata(path: Path) -> ModelMetadata:
    """Load one model sidecar without reflecting authored values into errors.

    Args:
        path: YAML sidecar to load.

    Returns:
        Validated model metadata.

    Raises:
        TransformConfigError: If the file cannot be read or fails schema validation.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise TransformConfigError(f"Cannot read model metadata: {path}") from error
    if not isinstance(raw, dict):
        raise TransformConfigError(f"Model metadata must be a mapping: {path}")
    try:
        return ModelMetadata.model_validate(raw)
    except ValidationError as error:
        locations = sorted(
            {".".join(str(part) for part in issue["loc"]) or "<root>" for issue in error.errors()}
        )
        raise TransformConfigError(
            f"Invalid model metadata at {path}; check: {', '.join(locations)}"
        ) from error
