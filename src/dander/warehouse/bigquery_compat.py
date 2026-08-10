"""One-way mapping from existing BigQuery field declarations to canonical schema v1."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from dander.schema import normalize_bigquery_type
from dander.warehouse.contracts import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    ProviderExtension,
    RelationSchema,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class BigQuerySchemaCompatibilityError(ValueError):
    """A BigQuery schema cannot be represented canonically without an explicit fallback."""


class BigQueryFieldLike(Protocol):
    """Structural view shared by connector RawField and writer WriteField."""

    @property
    def name(self) -> str: ...

    @property
    def data_type(self) -> str: ...

    @property
    def mode(self) -> str: ...

    @property
    def fields(self) -> Sequence[object]: ...

    @property
    def extensions(self) -> Sequence[ProviderExtension]: ...


_SCALARS = {
    "BOOL": CanonicalType(kind=LogicalTypeKind.BOOLEAN),
    "BYTES": CanonicalType(kind=LogicalTypeKind.BINARY),
    "DATE": CanonicalType(kind=LogicalTypeKind.DATE),
    "FLOAT64": CanonicalType(kind=LogicalTypeKind.FLOAT, bit_width=64),
    "INT64": CanonicalType(kind=LogicalTypeKind.INTEGER, bit_width=64),
    "JSON": CanonicalType(kind=LogicalTypeKind.JSON),
    "STRING": CanonicalType(kind=LogicalTypeKind.STRING),
    "TIME": CanonicalType(kind=LogicalTypeKind.TIME, fractional_second_precision=6),
    "TIMESTAMP": CanonicalType(
        kind=LogicalTypeKind.TIMESTAMP,
        with_timezone=True,
        fractional_second_precision=6,
    ),
    "DATETIME": CanonicalType(
        kind=LogicalTypeKind.TIMESTAMP,
        with_timezone=False,
        fractional_second_precision=6,
    ),
    "NUMERIC": CanonicalType(kind=LogicalTypeKind.DECIMAL, precision=38, scale=9),
}


def canonical_schema_from_bigquery(
    fields: Sequence[BigQueryFieldLike],
    *,
    unsupported_fallbacks: Mapping[str, CanonicalType] | None = None,
) -> RelationSchema:
    """Map a complete BigQuery schema without mutating or weakening the source declaration."""
    if not fields:
        raise BigQuerySchemaCompatibilityError(
            "Canonical schema compatibility requires at least one declared BigQuery field"
        )
    return RelationSchema(
        fields=tuple(
            canonical_field_from_bigquery(
                field,
                unsupported_fallbacks=unsupported_fallbacks,
            )
            for field in fields
        )
    )


def canonical_field_from_bigquery(
    field: BigQueryFieldLike,
    *,
    unsupported_fallbacks: Mapping[str, CanonicalType] | None = None,
) -> CanonicalField:
    """Map one recursive BigQuery field, rejecting undeclared lossy conversions."""
    data_type = normalize_bigquery_type(field.data_type)
    mode = field.mode.upper()
    base_type = _canonical_base_type(
        data_type,
        field.fields,
        unsupported_fallbacks=unsupported_fallbacks,
    )
    canonical_type = (
        CanonicalType(kind=LogicalTypeKind.ARRAY, element=base_type)
        if mode == "REPEATED"
        else base_type
    )
    if mode not in {"NULLABLE", "REQUIRED", "REPEATED"}:
        raise BigQuerySchemaCompatibilityError(f"Unsupported BigQuery field mode: {mode}")
    cardinality = FieldCardinality.NULLABLE if mode == "NULLABLE" else FieldCardinality.REQUIRED
    return CanonicalField(
        name=field.name,
        data_type=canonical_type,
        cardinality=cardinality,
        extensions=tuple(
            sorted(
                (
                    *getattr(field, "extensions", ()),
                    ProviderExtension(provider="bigquery", name="mode", value=mode),
                    ProviderExtension(provider="bigquery", name="type", value=data_type),
                ),
                key=lambda extension: (extension.provider, extension.name),
            )
        ),
    )


def _canonical_base_type(
    data_type: str,
    nested_fields: Sequence[object],
    *,
    unsupported_fallbacks: Mapping[str, CanonicalType] | None,
) -> CanonicalType:
    if data_type == "RECORD":
        if not nested_fields:
            raise BigQuerySchemaCompatibilityError("BigQuery RECORD must declare nested fields")
        return CanonicalType(
            kind=LogicalTypeKind.RECORD,
            fields=tuple(
                canonical_field_from_bigquery(
                    _field_like(field),
                    unsupported_fallbacks=unsupported_fallbacks,
                )
                for field in nested_fields
            ),
        )
    if nested_fields:
        raise BigQuerySchemaCompatibilityError(
            f"BigQuery {data_type} field cannot declare nested fields"
        )
    if mapped := _SCALARS.get(data_type):
        return mapped
    fallback = (unsupported_fallbacks or {}).get(data_type)
    if fallback is None:
        raise BigQuerySchemaCompatibilityError(
            f"BigQuery type {data_type!r} requires an explicit canonical fallback"
        )
    return fallback


def _field_like(value: object) -> BigQueryFieldLike:
    if not all(hasattr(value, attribute) for attribute in ("name", "data_type", "mode", "fields")):
        raise BigQuerySchemaCompatibilityError("BigQuery schema contains an invalid field")
    return cast("BigQueryFieldLike", value)
