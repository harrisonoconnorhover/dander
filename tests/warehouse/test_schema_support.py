"""Warehouse capability declarations reject unsupported schemas before provider I/O."""

from __future__ import annotations

import pytest

from dander.providers.bigquery.runtime import BIGQUERY_SCHEMA_SUPPORT
from dander.providers.postgresql.runtime import (
    POSTGRESQL_SCHEMA_SUPPORT,
    PostgreSQLSchemaMapper,
)
from dander.providers.redshift.runtime import REDSHIFT_SCHEMA_SUPPORT, RedshiftSchemaMapper
from dander.providers.snowflake.runtime import (
    SNOWFLAKE_SCHEMA_SUPPORT,
    SnowflakeSchemaMapper,
)
from dander.warehouse import (
    CanonicalField,
    CanonicalType,
    LogicalTypeKind,
    RelationSchema,
    WarehouseSchemaSupportError,
)


def _schema(name: str, data_type: CanonicalType) -> RelationSchema:
    return RelationSchema(fields=(CanonicalField(name=name, data_type=data_type),))


def test_redshift_mapper_rejects_semistructured_fields_before_provider_io() -> None:
    field = CanonicalField(
        name="payload",
        data_type=CanonicalType(kind=LogicalTypeKind.JSON),
    )

    with pytest.raises(
        WarehouseSchemaSupportError,
        match="require redshift/fallback=super",
    ):
        RedshiftSchemaMapper().canonical_schema([field])


def test_snowflake_mapper_requires_explicit_json_variant_before_provider_io() -> None:
    field = CanonicalField(
        name="payload",
        data_type=CanonicalType(kind=LogicalTypeKind.JSON),
    )

    with pytest.raises(
        WarehouseSchemaSupportError,
        match="require snowflake/fallback=variant",
    ):
        SnowflakeSchemaMapper().canonical_schema([field])


def test_decimal_and_temporal_limits_name_provider_field_and_limit() -> None:
    decimal = CanonicalType(kind=LogicalTypeKind.DECIMAL, precision=39, scale=2)
    timestamp = CanonicalType(
        kind=LogicalTypeKind.TIMESTAMP,
        with_timezone=True,
        fractional_second_precision=7,
    )

    with pytest.raises(
        WarehouseSchemaSupportError,
        match="redshift warehouse supports decimal precision up to 38; field 'amount' declares 39",
    ):
        REDSHIFT_SCHEMA_SUPPORT.require(_schema("amount", decimal))
    with pytest.raises(
        WarehouseSchemaSupportError,
        match=(
            "postgresql warehouse supports temporal precision up to 6; "
            "field 'observed_at' declares 7"
        ),
    ):
        POSTGRESQL_SCHEMA_SUPPORT.require(_schema("observed_at", timestamp))


def test_bigquery_rejects_nested_arrays_with_exact_field_path() -> None:
    nested = CanonicalType(
        kind=LogicalTypeKind.ARRAY,
        element=CanonicalType(
            kind=LogicalTypeKind.ARRAY,
            element=CanonicalType(kind=LogicalTypeKind.STRING),
        ),
    )

    with pytest.raises(
        WarehouseSchemaSupportError,
        match="bigquery warehouse does not support nested arrays at field 'matrix'",
    ):
        BIGQUERY_SCHEMA_SUPPORT.require(_schema("matrix", nested))


def test_postgresql_mapper_accepts_declared_nested_and_json_types() -> None:
    fields = [
        CanonicalField(
            name="payload",
            data_type=CanonicalType(kind=LogicalTypeKind.JSON),
        ),
        CanonicalField(
            name="labels",
            data_type=CanonicalType(
                kind=LogicalTypeKind.ARRAY,
                element=CanonicalType(kind=LogicalTypeKind.STRING),
            ),
        ),
    ]

    schema = PostgreSQLSchemaMapper().canonical_schema(fields)

    assert schema == RelationSchema(fields=tuple(fields))


def test_snowflake_declares_nine_digit_temporal_precision() -> None:
    timestamp = CanonicalType(
        kind=LogicalTypeKind.TIMESTAMP,
        with_timezone=False,
        fractional_second_precision=9,
    )

    assert SNOWFLAKE_SCHEMA_SUPPORT.require(_schema("observed_at", timestamp))
