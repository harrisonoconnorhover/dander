"""Canonical schema support advertised and enforced by PostgreSQL runtimes."""

from dander.warehouse import LogicalTypeKind
from dander.warehouse.runtime import WarehouseSchemaSupport

POSTGRESQL_SCHEMA_SUPPORT = WarehouseSchemaSupport(
    provider_id="postgresql",
    logical_types=frozenset(LogicalTypeKind),
    max_decimal_precision=1_000,
    max_temporal_precision=6,
    supports_nested_arrays=True,
)

__all__ = ["POSTGRESQL_SCHEMA_SUPPORT"]
