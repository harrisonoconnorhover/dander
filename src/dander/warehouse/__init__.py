"""Provider-neutral warehouse contracts and compatibility mappings."""

from dander.warehouse.bigquery_compat import (
    BigQuerySchemaCompatibilityError,
    canonical_field_from_bigquery,
    canonical_schema_from_bigquery,
)
from dander.warehouse.contracts import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    ProviderExtension,
    RelationCodec,
    RelationRef,
    RelationSchema,
)
from dander.warehouse.runtime import (
    PreparedWarehouseStatement,
    WarehouseCapabilities,
    WarehouseRuntime,
    WarehouseSchemaMapper,
    WarehouseTargetFence,
    WarehouseTelemetry,
    WarehouseTransformFactory,
    WarehouseTransformRunner,
    WarehouseWriterFactory,
)
from dander.warehouse.staging import (
    ParquetStagingSession,
    StagedArtifact,
    StagingArtifactError,
    StagingManifest,
)

__all__ = [
    "BigQuerySchemaCompatibilityError",
    "CanonicalField",
    "CanonicalType",
    "FieldCardinality",
    "LogicalTypeKind",
    "ParquetStagingSession",
    "ProviderExtension",
    "PreparedWarehouseStatement",
    "RelationCodec",
    "RelationRef",
    "RelationSchema",
    "StagedArtifact",
    "StagingArtifactError",
    "StagingManifest",
    "WarehouseCapabilities",
    "WarehouseRuntime",
    "WarehouseSchemaMapper",
    "WarehouseTargetFence",
    "WarehouseTelemetry",
    "WarehouseTransformFactory",
    "WarehouseTransformRunner",
    "WarehouseWriterFactory",
    "canonical_field_from_bigquery",
    "canonical_schema_from_bigquery",
]
