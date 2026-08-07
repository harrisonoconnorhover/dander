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

__all__ = [
    "BigQuerySchemaCompatibilityError",
    "CanonicalField",
    "CanonicalType",
    "FieldCardinality",
    "LogicalTypeKind",
    "ProviderExtension",
    "RelationCodec",
    "RelationRef",
    "RelationSchema",
    "canonical_field_from_bigquery",
    "canonical_schema_from_bigquery",
]
