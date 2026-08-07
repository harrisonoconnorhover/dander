"""Canonical warehouse relation and schema contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dander.ingestion import Endpoint, RawField
from dander.warehouse import (
    BigQuerySchemaCompatibilityError,
    CanonicalField,
    CanonicalType,
    FieldCardinality,
    LogicalTypeKind,
    ProviderExtension,
    RelationRef,
    RelationSchema,
    canonical_schema_from_bigquery,
)
from dander.writer import WriteField, WriteTarget


def test_relation_ref_keeps_coordinates_unrendered() -> None:
    relation = RelationRef(
        catalog="gcp-project",
        namespace="analytics",
        name="customer_orders",
    )

    assert relation.coordinates == ("gcp-project", "analytics", "customer_orders")
    assert "`" not in "".join(relation.coordinates)


@pytest.mark.parametrize(
    "payload",
    [
        {"catalog": "", "namespace": "analytics", "name": "orders"},
        {"catalog": "project", "namespace": "bad.name", "name": "orders"},
        {"catalog": "project", "namespace": "analytics", "name": "bad-name"},
    ],
)
def test_relation_ref_rejects_nonportable_coordinates(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        RelationRef.model_validate(payload)


def test_canonical_type_requires_explicit_decimal_timestamp_array_and_record_shape() -> None:
    decimal = CanonicalType(kind=LogicalTypeKind.DECIMAL, precision=38, scale=9)
    timestamp = CanonicalType(
        kind=LogicalTypeKind.TIMESTAMP,
        with_timezone=True,
        fractional_second_precision=6,
    )
    array = CanonicalType(kind=LogicalTypeKind.ARRAY, element=decimal)
    record = CanonicalType(
        kind=LogicalTypeKind.RECORD,
        fields=(CanonicalField(name="observed_at", data_type=timestamp),),
    )

    assert array.element == decimal
    assert record.fields[0].data_type.with_timezone is True
    for payload in (
        {"kind": "decimal", "precision": 10},
        {"kind": "timestamp"},
        {"kind": "integer"},
        {"kind": "float", "bit_width": 16},
        {"kind": "array"},
        {"kind": "record", "fields": []},
        {"kind": "string", "precision": 5},
    ):
        with pytest.raises(ValidationError):
            CanonicalType.model_validate(payload)


def test_schema_rejects_duplicate_fields_and_extensions() -> None:
    field = CanonicalField(name="id", data_type=CanonicalType(kind=LogicalTypeKind.STRING))
    with pytest.raises(ValidationError, match="relation field names must be unique"):
        RelationSchema(fields=(field, field))

    extension = ProviderExtension(provider="bigquery", name="type", value="STRING")
    with pytest.raises(ValidationError, match="duplicate keys"):
        CanonicalField(
            name="id",
            data_type=CanonicalType(kind=LogicalTypeKind.STRING),
            extensions=(extension, extension),
        )


def test_bigquery_compatibility_maps_nested_repeated_and_precise_types() -> None:
    fields = (
        WriteField(name="id", data_type="INTEGER", mode="REQUIRED"),
        WriteField(name="amount", data_type="NUMERIC"),
        WriteField(name="observed_at", data_type="TIMESTAMP"),
        WriteField(name="local_at", data_type="DATETIME"),
        WriteField(
            name="events",
            data_type="RECORD",
            mode="REPEATED",
            fields=(WriteField(name="active", data_type="BOOLEAN"),),
        ),
    )

    schema = canonical_schema_from_bigquery(fields)

    assert schema.fields[0].data_type.kind is LogicalTypeKind.INTEGER
    assert schema.fields[0].cardinality is FieldCardinality.REQUIRED
    assert schema.fields[1].data_type == CanonicalType(
        kind=LogicalTypeKind.DECIMAL,
        precision=38,
        scale=9,
    )
    assert schema.fields[2].data_type.with_timezone is True
    assert schema.fields[2].data_type.fractional_second_precision == 6
    assert schema.fields[3].data_type.with_timezone is False
    repeated = schema.fields[4]
    assert repeated.cardinality is FieldCardinality.REQUIRED
    assert repeated.data_type.kind is LogicalTypeKind.ARRAY
    assert repeated.data_type.element is not None
    assert repeated.data_type.element.kind is LogicalTypeKind.RECORD
    assert repeated.data_type.element.fields[0].data_type.kind is LogicalTypeKind.BOOLEAN
    assert [(item.name, item.value) for item in repeated.extensions] == [
        ("mode", "REPEATED"),
        ("type", "RECORD"),
    ]


@pytest.mark.parametrize(
    ("bigquery_type", "kind"),
    [
        ("BOOL", LogicalTypeKind.BOOLEAN),
        ("BYTES", LogicalTypeKind.BINARY),
        ("DATE", LogicalTypeKind.DATE),
        ("FLOAT64", LogicalTypeKind.FLOAT),
        ("INT64", LogicalTypeKind.INTEGER),
        ("JSON", LogicalTypeKind.JSON),
        ("STRING", LogicalTypeKind.STRING),
        ("TIME", LogicalTypeKind.TIME),
    ],
)
def test_bigquery_scalar_compatibility_is_explicit(
    bigquery_type: str,
    kind: LogicalTypeKind,
) -> None:
    field = WriteField(name="value", data_type=bigquery_type)

    assert canonical_schema_from_bigquery((field,)).fields[0].data_type.kind is kind


def test_bigquery_compatibility_requires_a_declared_schema() -> None:
    with pytest.raises(BigQuerySchemaCompatibilityError, match="at least one declared"):
        canonical_schema_from_bigquery(())


def test_bigquery_compatibility_rejects_lossy_type_without_explicit_fallback() -> None:
    geography = WriteField(name="location", data_type="GEOGRAPHY")

    with pytest.raises(BigQuerySchemaCompatibilityError, match="explicit canonical fallback"):
        canonical_schema_from_bigquery((geography,))

    schema = canonical_schema_from_bigquery(
        (geography,),
        unsupported_fallbacks={
            "GEOGRAPHY": CanonicalType(kind=LogicalTypeKind.STRING),
        },
    )
    assert schema.fields[0].data_type.kind is LogicalTypeKind.STRING
    assert schema.fields[0].extensions[1].value == "GEOGRAPHY"

    big_numeric = WriteField(name="amount", data_type="BIGNUMERIC")
    with pytest.raises(BigQuerySchemaCompatibilityError, match="explicit canonical fallback"):
        canonical_schema_from_bigquery((big_numeric,))


def test_existing_connector_and_writer_models_expose_canonical_views() -> None:
    endpoint = Endpoint(
        name="records",
        path="/records",
        primary_key=["id"],
        raw_schema=[RawField(name="id", data_type="STRING", mode="REQUIRED")],
    )
    target = WriteTarget(
        project="gcp-project",
        dataset="raw",
        table="records",
        schema=(WriteField(name="id", data_type="STRING", mode="REQUIRED"),),
    )

    assert endpoint.raw_schema[0].to_canonical() == endpoint.canonical_raw_schema().fields[0]
    assert target.relation_ref.coordinates == ("gcp-project", "raw", "records")
    assert target.canonical_schema.fields[0].cardinality is FieldCardinality.REQUIRED
