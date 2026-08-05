"""Typed, schema-preserving operations for executable transform nodes.

The operation vocabulary originates in Josh Wagner's generic pipeline-operation work at
``WagnerJ-Dev/dander@574d2f0``. Current Dander executes graph transforms after connector ingestion,
so this module carries the safe declarative subset into ``PipelineGraph`` rather than applying it
to the raw extraction stream. That keeps raw schemas, watermarks, and replay behavior unchanged.

Operations are ordered and operate on the owning transform node's declared output fields. The
compiler implements them as explicit BigQuery CTEs. Provider write-back, arbitrary SQL hooks,
deduplication, and operations that change the declared schema are intentionally absent.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, model_validator

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
type OperationScalar = str | int | float | bool


class OperationKind(StrEnum):
    """The executable, schema-preserving operation vocabulary."""

    TRUNCATE_STRING = "truncate_string"
    TRIM_WHITESPACE = "trim_whitespace"
    DEFAULT_VALUE = "default_value"
    FILTER_ROWS = "filter_rows"


class ComparisonOperator(StrEnum):
    """Closed comparison grammar for ``filter_rows``."""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class MatchLogic(StrEnum):
    """How a filter's flat condition list combines."""

    ALL = "all"
    ANY = "any"


class OperationParams(BaseModel):
    """Base for kind-specific operation parameters."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class TruncateStringParams(OperationParams):
    """Truncate one declared string field to ``max_length`` characters."""

    field: str = Field(pattern=_IDENTIFIER.pattern)
    max_length: int = Field(ge=0)


class TrimWhitespaceParams(OperationParams):
    """Strip leading and trailing whitespace from one declared string field."""

    field: str = Field(pattern=_IDENTIFIER.pattern)


class DefaultValueParams(OperationParams):
    """Replace a null field with a required, non-null scalar literal."""

    field: str = Field(pattern=_IDENTIFIER.pattern)
    default: OperationScalar


class FieldCondition(BaseModel):
    """One field comparison in the bounded ``filter_rows`` grammar."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    field: str = Field(pattern=_IDENTIFIER.pattern)
    op: ComparisonOperator
    value: OperationScalar | list[OperationScalar] | None = None

    @model_validator(mode="after")
    def _validate_value_arity(self) -> FieldCondition:
        if self.op in {ComparisonOperator.IS_NULL, ComparisonOperator.IS_NOT_NULL}:
            if self.value is not None:
                raise ValueError(f"Filter operator {self.op.value!r} must not set 'value'")
        elif self.op in {ComparisonOperator.IN, ComparisonOperator.NOT_IN}:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"Filter operator {self.op.value!r} requires a non-empty list")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError(f"Filter operator {self.op.value!r} requires one scalar value")
        return self


class FilterRowsParams(OperationParams):
    """Keep rows matching a flat, non-executable predicate."""

    conditions: list[FieldCondition] = Field(min_length=1)
    logic: MatchLogic = MatchLogic.ALL


type PipelineOperationParams = (
    TruncateStringParams | TrimWhitespaceParams | DefaultValueParams | FilterRowsParams
)

_PARAM_TYPES: dict[OperationKind, type[OperationParams]] = {
    OperationKind.TRUNCATE_STRING: TruncateStringParams,
    OperationKind.TRIM_WHITESPACE: TrimWhitespaceParams,
    OperationKind.DEFAULT_VALUE: DefaultValueParams,
    OperationKind.FILTER_ROWS: FilterRowsParams,
}


class OperationSpec(BaseModel):
    """One ordered operation declaration in ``TransformNodeConfig.operations``."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    kind: OperationKind
    params: SerializeAsAny[OperationParams]
    metadata: dict[str, Any] = Field(default_factory=dict)

    _param_types: ClassVar[dict[OperationKind, type[OperationParams]]] = _PARAM_TYPES

    @model_validator(mode="before")
    @classmethod
    def _route_params(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw = dict(value)
        raw_kind = raw.get("kind")
        if not isinstance(raw_kind, str):
            return value
        try:
            kind = OperationKind(raw_kind)
        except ValueError:
            return value
        raw["params"] = cls._param_types[kind].model_validate(raw.get("params", {}))
        return raw

    def referenced_fields(self) -> tuple[str, ...]:
        """Return declared output fields this operation reads or replaces."""
        params = self.params
        if isinstance(params, FilterRowsParams):
            return tuple(condition.field for condition in params.conditions)
        if isinstance(
            params,
            (TruncateStringParams, TrimWhitespaceParams, DefaultValueParams),
        ):
            return (params.field,)
        raise AssertionError(f"Unexpected operation params type: {type(params).__name__}")


def build_operation_catalog() -> dict[str, object]:
    """Return presentation-safe metadata for Druff's canonical operation editor."""
    field_parameter = {
        "name": "field",
        "display_name": "Field",
        "control": "field",
        "required": True,
    }
    return {
        "schema_version": 1,
        "operations": [
            {
                "kind": OperationKind.TRIM_WHITESPACE.value,
                "display_name": "Trim whitespace",
                "description": "Remove leading and trailing whitespace from a string field.",
                "parameters": [dict(field_parameter)],
            },
            {
                "kind": OperationKind.TRUNCATE_STRING.value,
                "display_name": "Truncate string",
                "description": "Limit a string field to a maximum character length.",
                "parameters": [
                    dict(field_parameter),
                    {
                        "name": "max_length",
                        "display_name": "Maximum length",
                        "control": "integer",
                        "required": True,
                        "minimum": 0,
                    },
                ],
            },
            {
                "kind": OperationKind.DEFAULT_VALUE.value,
                "display_name": "Default value",
                "description": "Replace a null field with a scalar literal.",
                "parameters": [
                    dict(field_parameter),
                    {
                        "name": "default",
                        "display_name": "Default",
                        "control": "scalar",
                        "required": True,
                    },
                ],
            },
            {
                "kind": OperationKind.FILTER_ROWS.value,
                "display_name": "Filter rows",
                "description": "Keep rows matching a bounded list of field comparisons.",
                "parameters": [
                    {
                        "name": "logic",
                        "display_name": "Match",
                        "control": "select",
                        "required": False,
                        "default": MatchLogic.ALL.value,
                        "options": [logic.value for logic in MatchLogic],
                    },
                    {
                        "name": "conditions",
                        "display_name": "Conditions",
                        "control": "conditions",
                        "required": True,
                        "operators": [operator.value for operator in ComparisonOperator],
                    },
                ],
            },
        ],
    }
