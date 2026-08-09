"""Canonical relation and schema contracts shared by warehouse adapters."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CATALOG = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,254}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_EXTENSION_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,126}$")

ExtensionScalar = str | int | float | bool


class LogicalTypeKind(StrEnum):
    """Closed set of provider-neutral data types in schema contract v1."""

    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    STRING = "string"
    BINARY = "binary"
    DATE = "date"
    TIME = "time"
    TIMESTAMP = "timestamp"
    JSON = "json"
    ARRAY = "array"
    RECORD = "record"


class FieldCardinality(StrEnum):
    """Whether one relation row must contain a value for a field."""

    NULLABLE = "nullable"
    REQUIRED = "required"


class ProviderExtension(BaseModel):
    """One deterministic provider-specific schema annotation, never a credential."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    name: str
    value: ExtensionScalar

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if not _PROVIDER.fullmatch(value):
            raise ValueError("extension provider must be a valid provider id")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _EXTENSION_NAME.fullmatch(value):
            raise ValueError("extension name must use lowercase portable syntax")
        return value


class CanonicalType(BaseModel):
    """Recursive logical type with explicit precision and timestamp semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: LogicalTypeKind
    bit_width: Literal[8, 16, 32, 64] | None = None
    precision: int | None = None
    scale: int | None = None
    with_timezone: bool | None = None
    fractional_second_precision: int | None = None
    element: CanonicalType | None = None
    fields: tuple[CanonicalField, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind is LogicalTypeKind.INTEGER:
            if self.bit_width is None:
                raise ValueError("integer must declare bit_width")
        elif self.kind is LogicalTypeKind.FLOAT:
            if self.bit_width not in {32, 64}:
                raise ValueError("float bit_width must be 32 or 64")
        elif self.bit_width is not None:
            raise ValueError("bit_width applies only to integer and float")

        if self.kind is LogicalTypeKind.DECIMAL:
            if (
                self.precision is None
                or self.scale is None
                or isinstance(self.precision, bool)
                or isinstance(self.scale, bool)
                or not 1 <= self.precision <= 1_000
                or not 0 <= self.scale <= self.precision
            ):
                raise ValueError(
                    "decimal requires precision and scale with 0 <= scale <= precision"
                )
        elif self.precision is not None or self.scale is not None:
            raise ValueError("precision and scale apply only to decimal")

        if self.kind is LogicalTypeKind.TIMESTAMP:
            if self.with_timezone is None:
                raise ValueError("timestamp must declare with_timezone")
        elif self.with_timezone is not None:
            raise ValueError("with_timezone applies only to timestamp")

        if self.kind in {LogicalTypeKind.TIME, LogicalTypeKind.TIMESTAMP}:
            if (
                self.fractional_second_precision is None
                or isinstance(self.fractional_second_precision, bool)
                or not 0 <= self.fractional_second_precision <= 9
            ):
                raise ValueError(
                    "time and timestamp must declare fractional_second_precision from 0 to 9"
                )
        elif self.fractional_second_precision is not None:
            raise ValueError("fractional_second_precision applies only to time and timestamp")

        if self.kind is LogicalTypeKind.ARRAY:
            if self.element is None:
                raise ValueError("array must declare an element type")
        elif self.element is not None:
            raise ValueError("element applies only to array")

        if self.kind is LogicalTypeKind.RECORD:
            if not self.fields:
                raise ValueError("record must declare fields")
            names = [field.name for field in self.fields]
            if len(names) != len(set(names)):
                raise ValueError("record field names must be unique")
        elif self.fields:
            raise ValueError("fields apply only to record")
        return self


class CanonicalField(BaseModel):
    """One named relation field with provider-neutral type and cardinality."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )

    name: str
    data_type: CanonicalType = Field(alias="type")
    cardinality: FieldCardinality = FieldCardinality.NULLABLE
    description: str = ""
    extensions: tuple[ProviderExtension, ...] = Field(default_factory=tuple)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("canonical field name must be a portable identifier")
        return value

    @field_validator("extensions")
    @classmethod
    def validate_extensions(
        cls,
        values: tuple[ProviderExtension, ...],
    ) -> tuple[ProviderExtension, ...]:
        keys = [(extension.provider, extension.name) for extension in values]
        if len(keys) != len(set(keys)):
            raise ValueError("provider extensions must not contain duplicate keys")
        if tuple(sorted(keys)) != tuple(keys):
            raise ValueError("provider extensions must use deterministic provider/name order")
        return values


class RelationSchema(BaseModel):
    """Versioned canonical schema for one physical relation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    fields: tuple[CanonicalField, ...] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, values: tuple[CanonicalField, ...]) -> tuple[CanonicalField, ...]:
        names = [field.name for field in values]
        if len(names) != len(set(names)):
            raise ValueError("relation field names must be unique")
        return values


class RelationRef(BaseModel):
    """Unrendered catalog/namespace/relation coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog: str
    namespace: str
    name: str

    @field_validator("catalog")
    @classmethod
    def validate_catalog(cls, value: str) -> str:
        if not _CATALOG.fullmatch(value):
            raise ValueError("relation catalog must be a portable catalog identifier")
        return value

    @field_validator("namespace", "name")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("relation namespace and name must be portable identifiers")
        return value

    @property
    def coordinates(self) -> tuple[str, str, str]:
        """Return stable coordinates without choosing provider quoting or separators."""
        return (self.catalog, self.namespace, self.name)


@runtime_checkable
class WarehouseCoordinateConfig(Protocol):
    """Resolve compatibility CLI inputs into canonical warehouse coordinates.

    Provider configuration owns the translation from its native vocabulary. Shared
    orchestration only receives the resulting ``RelationRef`` values.
    """

    def raw_relation(
        self,
        name: str,
        *,
        compatibility_catalog: str | None,
        compatibility_namespace: str | None,
    ) -> RelationRef:
        """Return one raw relation for a configured endpoint."""
        ...


@runtime_checkable
class RelationCodec(Protocol):
    """Validate and render canonical coordinates for one warehouse provider."""

    @property
    def provider_id(self) -> str:
        """Return the registered warehouse provider identifier."""
        ...

    def render(self, relation: RelationRef) -> str:
        """Return one safely quoted provider relation identifier."""
        ...
