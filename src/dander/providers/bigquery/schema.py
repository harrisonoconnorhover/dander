"""Project canonical fields to BigQuery's native writer schema at the provider boundary."""

from __future__ import annotations

from dander.warehouse import (
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    RelationSchema,
)
from dander.writer.base import WriteField

_SCALARS = {
    LogicalTypeKind.BOOLEAN: "BOOL",
    LogicalTypeKind.INTEGER: "INT64",
    LogicalTypeKind.FLOAT: "FLOAT64",
    LogicalTypeKind.STRING: "STRING",
    LogicalTypeKind.BINARY: "BYTES",
    LogicalTypeKind.DATE: "DATE",
    LogicalTypeKind.JSON: "JSON",
    LogicalTypeKind.RECORD: "RECORD",
}


def bigquery_write_fields(schema: RelationSchema) -> tuple[WriteField, ...]:
    """Convert representable canonical fields without importing a provider SDK."""
    return tuple(_write_field(field) for field in schema.fields)


def _write_field(field: CanonicalField) -> WriteField:
    data_type = field.data_type
    if data_type.kind is LogicalTypeKind.ARRAY:
        if field.cardinality is not FieldCardinality.REQUIRED:
            raise ValueError("BigQuery repeated fields cannot preserve nullable arrays")
        assert data_type.element is not None
        data_type = data_type.element
        mode = "REPEATED"
    else:
        mode = "REQUIRED" if field.cardinality is FieldCardinality.REQUIRED else "NULLABLE"
    native_type = _native_type(data_type)
    for extension in field.extensions:
        if extension.provider == "bigquery" and extension.name in {"type", "mode"}:
            expected = native_type if extension.name == "type" else mode
            if extension.value != expected:
                raise ValueError(
                    f"BigQuery {extension.name} annotation conflicts with "
                    f"canonical field {field.name!r}"
                )
    return WriteField(
        name=field.name,
        data_type=native_type,
        mode=mode,
        fields=tuple(_write_field(child) for child in data_type.fields),
        extensions=tuple(
            extension
            for extension in field.extensions
            if not (extension.provider == "bigquery" and extension.name in {"type", "mode"})
        ),
    )


def _native_type(data_type: CanonicalType) -> str:
    if native_type := _SCALARS.get(data_type.kind):
        return native_type
    if data_type.kind is LogicalTypeKind.DECIMAL:
        assert data_type.precision is not None and data_type.scale is not None
        if data_type.scale <= 9 and data_type.precision - data_type.scale <= 29:
            return "NUMERIC"
        raise ValueError("Canonical decimal exceeds BigQuery NUMERIC precision or scale")
    if data_type.kind in {LogicalTypeKind.TIME, LogicalTypeKind.TIMESTAMP}:
        assert data_type.fractional_second_precision is not None
        if data_type.fractional_second_precision > 6:
            raise ValueError("BigQuery supports temporal precision up to 6")
        if data_type.kind is LogicalTypeKind.TIME:
            return "TIME"
        return "TIMESTAMP" if data_type.with_timezone else "DATETIME"
    raise ValueError(f"BigQuery cannot represent canonical type {data_type.kind.value!r}")
