"""BigQuery implementation of the provider-neutral warehouse runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from dander.pipeline.runtime import GraphExecutionPlan
from dander.providers.bigquery.config import BigQueryWarehouseConfig
from dander.providers.bigquery.fence import BigQueryTargetFence
from dander.providers.bigquery.graph import BigQueryGraphRunner
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.transform import BigQueryTransformRunner
from dander.warehouse.bigquery_compat import canonical_schema_from_bigquery
from dander.warehouse.contracts import LogicalTypeKind
from dander.warehouse.runtime import (
    WarehouseCapabilities,
    WarehouseRuntime,
    WarehouseSchemaSupport,
    WarehouseTransformRunner,
)
from dander.writer import (
    BigQueryReplaceWriter,
    BigQueryScd1Writer,
    SchemaEvolution,
    WriteMode,
    WritePattern,
    WriteTransport,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import BaseModel

    from dander.warehouse.bigquery_compat import BigQueryFieldLike
    from dander.warehouse.contracts import RelationRef, RelationSchema


BIGQUERY_SCHEMA_SUPPORT = WarehouseSchemaSupport(
    provider_id="bigquery",
    logical_types=frozenset(LogicalTypeKind),
    max_decimal_precision=38,
    max_temporal_precision=6,
    supports_nested_arrays=False,
)


@dataclass(frozen=True, slots=True)
class BigQueryRelationCodec:
    """Render validated canonical coordinates as quoted BigQuery identifiers."""

    provider_id: str = "bigquery"

    def render(self, relation: RelationRef) -> str:
        """Render ``project.dataset.table`` without accepting raw SQL fragments."""
        return ".".join(f"`{coordinate}`" for coordinate in relation.coordinates)


@dataclass(frozen=True, slots=True)
class BigQuerySchemaMapper:
    """Expose the existing strict BigQuery compatibility mapper as a capability."""

    def canonical_schema(self, fields: Sequence[object]) -> RelationSchema:
        """Map a complete legacy BigQuery declaration to canonical schema v1."""
        schema = canonical_schema_from_bigquery(cast("Sequence[BigQueryFieldLike]", fields))
        return BIGQUERY_SCHEMA_SUPPORT.require(schema)


@dataclass(frozen=True, slots=True)
class BigQueryWriterFactory:
    """Construct current BigQuery ingestion writers without changing behavior."""

    project: str

    def build_ingestion_writer(
        self,
        *,
        sandbox: bool,
        batch_rows: int,
        schema_evolution: SchemaEvolution,
    ) -> WritePattern:
        """Use replace for local sandbox execution and SCD1 for hosted execution."""
        if sandbox:
            return BigQueryReplaceWriter(project=self.project, max_batch_rows=batch_rows)
        return BigQueryScd1Writer(
            project=self.project,
            max_batch_rows=batch_rows,
            schema_evolution=schema_evolution,
        )


@dataclass(frozen=True, slots=True)
class BigQueryTransformFactory:
    """Construct the existing BigQuery model or graph runner."""

    project: str

    def build_transform_runner(
        self,
        *,
        graph_plan: object | None,
        build_models: bool,
        raw_namespace: str = "raw",
    ) -> WarehouseTransformRunner | None:
        """Preserve graph precedence and model-build behavior."""
        if graph_plan is not None:
            if not isinstance(graph_plan, GraphExecutionPlan):
                raise TypeError("BigQuery graph plan has the wrong type")
            return BigQueryGraphRunner(plan=graph_plan, project=self.project)
        if build_models:
            return BigQueryTransformRunner(
                project=self.project,
                raw_namespace=raw_namespace,
            )
        return None


@dataclass(frozen=True, slots=True)
class BigQueryTelemetry:
    """Normalize stable BigQuery job counters and identifiers."""

    def operation(
        self,
        job: object,
        *,
        operation: TelemetryOperation,
        duration_ms: int = 0,
        retry_count: int = 0,
    ) -> OperationTelemetry:
        """Read only approved scalar attributes from a completed BigQuery job."""
        return OperationTelemetry(
            provider="bigquery",
            operation=operation,
            duration_ms=duration_ms,
            retry_count=retry_count,
            rows_written=_nonnegative_attribute(job, "output_rows"),
            rows_affected=_nonnegative_attribute(job, "num_dml_affected_rows"),
            bytes_processed=_nonnegative_attribute(job, "total_bytes_processed"),
            bytes_billed=_nonnegative_attribute(job, "total_bytes_billed"),
            job_id=_optional_identifier(job, "job_id"),
        )


BIGQUERY_CAPABILITIES = WarehouseCapabilities(
    provider_id="bigquery",
    schema_contract_version=1,
    write_modes=frozenset(WriteMode),
    transports=frozenset({WriteTransport.LOAD_JOB, WriteTransport.STORAGE_WRITE}),
    supports_transforms=True,
    supports_graphs=True,
    supports_target_fencing=True,
    schema_support=BIGQUERY_SCHEMA_SUPPORT,
)


def build_bigquery_warehouse(
    config: BaseModel,
    context: Mapping[str, object],
) -> WarehouseRuntime:
    """Build the selected BigQuery runtime after provider validation."""
    if not isinstance(config, BigQueryWarehouseConfig):
        raise TypeError("BigQuery warehouse factory received the wrong configuration")
    project = context.get("catalog", context.get("project"))
    if not isinstance(project, str) or not project:
        raise ValueError("BigQuery warehouse factory requires a catalog context")
    return WarehouseRuntime(
        provider_id="bigquery",
        relation_codec=BigQueryRelationCodec(),
        schema_mapper=BigQuerySchemaMapper(),
        writers=BigQueryWriterFactory(project),
        transforms=BigQueryTransformFactory(project),
        target_fence=BigQueryTargetFence(project),
        telemetry=BigQueryTelemetry(),
        capabilities=BIGQUERY_CAPABILITIES,
    )


BIGQUERY_WAREHOUSE_FACTORY: ProviderFactory[WarehouseRuntime] = ProviderFactory(
    kind=ProviderKind.WAREHOUSE,
    provider_id="bigquery",
    api_version=PROVIDER_API_VERSION,
    build=build_bigquery_warehouse,
)


def _nonnegative_attribute(job: object, name: str) -> int:
    value = getattr(job, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _optional_identifier(job: object, name: str) -> str | None:
    value = getattr(job, name, None)
    return value if isinstance(value, str) and value else None
