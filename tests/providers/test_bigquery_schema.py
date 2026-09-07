"""BigQuery's canonical-to-native schema boundary and legacy compatibility."""

from __future__ import annotations

import pytest

from dander.providers.bigquery.schema import bigquery_write_fields
from dander.warehouse import (
    CanonicalField,
    CanonicalType,
    LogicalTypeKind,
    ProviderExtension,
    RelationRef,
    RelationSchema,
    canonical_schema_from_bigquery,
)
from dander.writer import SchemaEvolution, WriteField, WriteTarget
from dander.writer.bigquery import BigQueryWriteError, _validate_declared_schema


def test_native_projection_round_trips_supported_types_nested_fields_and_extensions() -> None:
    fields = tuple(
        WriteField(name=f"field_{index}", data_type=data_type)
        for index, data_type in enumerate(
            (
                "BOOL",
                "BYTES",
                "DATE",
                "FLOAT64",
                "INT64",
                "JSON",
                "STRING",
                "TIME",
                "TIMESTAMP",
                "DATETIME",
                "NUMERIC",
            )
        )
    ) + (
        WriteField(
            name="events",
            data_type="RECORD",
            mode="REPEATED",
            fields=(WriteField(name="id", data_type="INT64", mode="REQUIRED"),),
        ),
        WriteField(
            name="annotated",
            data_type="STRING",
            extensions=(ProviderExtension(provider="snowflake", name="fallback", value="variant"),),
        ),
    )
    schema = canonical_schema_from_bigquery(fields)

    assert canonical_schema_from_bigquery(bigquery_write_fields(schema)) == schema


@pytest.mark.parametrize(
    "data_type, message",
    [
        (CanonicalType(kind=LogicalTypeKind.DECIMAL, precision=38, scale=0), "precision or scale"),
        (
            CanonicalType(
                kind=LogicalTypeKind.TIMESTAMP, with_timezone=True, fractional_second_precision=7
            ),
            "temporal precision",
        ),
        (
            CanonicalType(
                kind=LogicalTypeKind.ARRAY, element=CanonicalType(kind=LogicalTypeKind.STRING)
            ),
            "nullable arrays",
        ),
    ],
)
def test_projection_rejects_types_that_native_schema_cannot_preserve(
    data_type: CanonicalType, message: str
) -> None:
    schema = RelationSchema(fields=(CanonicalField(name="value", data_type=data_type),))
    with pytest.raises(ValueError, match=message):
        bigquery_write_fields(schema)


def test_writer_accepts_canonical_input_and_preserves_explicit_legacy_validation() -> None:
    relation = RelationRef(catalog="unit-project", namespace="raw", name="records")
    schema = canonical_schema_from_bigquery((WriteField(name="id", data_type="INT64"),))
    target = WriteTarget(relation=relation, declared_schema=schema)
    assert _validate_declared_schema(target, SchemaEvolution.ADDITIVE) == (
        WriteField(name="id", data_type="INT64"),
    )
    native = WriteTarget(
        relation=relation,
        schema=(WriteField(name="location", data_type="GEOGRAPHY"),),
        declared_schema=schema,
    )
    assert _validate_declared_schema(native, None) == native.schema
    invalid = WriteTarget(relation=relation, schema=(WriteField(name="id", data_type="INVALID"),))
    with pytest.raises(BigQueryWriteError, match="Unsupported declared schema type"):
        _validate_declared_schema(invalid, None)
