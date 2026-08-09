"""Transform engine — our owned dbt-replacement (ref() DAG, materializations, tests)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dander.transform.config import (
    ColumnMetadata,
    GenericTestMetadata,
    MetricAggregation,
    MetricMetadata,
    ModelMetadata,
    RelationshipMetadata,
    TransformConfigError,
    load_model_metadata,
)
from dander.transform.dialects import PortableSqlError, parse_portable_query, render_portable_query
from dander.transform.model import SqlDialect
from dander.transform.project import (
    TransformModel,
    TransformProject,
    TransformProjectError,
)
from dander.transform.result import TransformRunError, TransformRunResult

if TYPE_CHECKING:
    from dander.transform.runner import BigQueryTransformRunner


def __getattr__(name: str) -> object:
    """Load the BigQuery runner only when that compatibility export is requested."""
    if name == "BigQueryTransformRunner":
        from dander.transform.runner import BigQueryTransformRunner

        return BigQueryTransformRunner
    raise AttributeError(name)


__all__ = [
    "BigQueryTransformRunner",
    "ColumnMetadata",
    "GenericTestMetadata",
    "MetricAggregation",
    "MetricMetadata",
    "ModelMetadata",
    "PortableSqlError",
    "RelationshipMetadata",
    "TransformConfigError",
    "TransformModel",
    "TransformProject",
    "TransformProjectError",
    "TransformRunError",
    "TransformRunResult",
    "SqlDialect",
    "load_model_metadata",
    "parse_portable_query",
    "render_portable_query",
]
