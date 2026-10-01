"""Retained-raw date repair contracts and credential-free eligibility checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from dander.pipeline.graph_ops import validate_field_wiring
from dander.pipeline.node_config import TargetNodeConfig
from dander.writer import WriteMode

if TYPE_CHECKING:
    from dander.pipeline.graph import PipelineGraph

_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_KEY_TYPES = frozenset(
    {
        "STRING",
        "INT64",
        "INTEGER",
        "BOOL",
        "BOOLEAN",
        "DATE",
        "DATETIME",
        "TIMESTAMP",
        "BYTES",
        "NUMERIC",
        "BIGNUMERIC",
    }
)


class GraphRepairError(ValueError):
    """The graph cannot safely repair a date range from retained raw data."""


class GraphRepairWindow(BaseModel):
    """UTC calendar dates: start is inclusive and end is exclusive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_date: date
    end_date: date

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def strict_date(cls, value: object) -> date:
        if type(value) is date:
            return value
        if not isinstance(value, str) or not _DATE.fullmatch(value):
            raise ValueError("Repair dates must use YYYY-MM-DD")
        return date.fromisoformat(value)

    @model_validator(mode="after")
    def ordered_window(self) -> Self:
        if self.end_date <= self.start_date:
            raise ValueError("Repair end_date must be after start_date (exclusive)")
        return self


@dataclass(frozen=True, slots=True)
class GraphTargetRepair:
    node_id: str
    partition_field: str
    partition_type: Literal["DATE", "TIMESTAMP"]
    business_key: tuple[str, ...]


def plan_graph_repair(
    graph: PipelineGraph,
    window: GraphRepairWindow,
) -> tuple[GraphTargetRepair, ...]:
    """Validate every declared output before any source or warehouse client is constructed.

    This checks the authored write contract. Ordinary executable graph compilation still runs
    later, and publication checks actual partition values and key collisions transactionally.
    """
    GraphRepairWindow.model_validate(window.model_dump())
    validate_field_wiring(graph)
    targets: list[GraphTargetRepair] = []
    for node in graph.nodes:
        if node.type != "target":
            continue
        config = node.config
        if not isinstance(config, TargetNodeConfig) or config.writer is None:
            raise GraphRepairError(f"Repair target {node.id!r} needs a declared writer")
        writer = config.writer
        if writer.write_mode is not WriteMode.REPLACE:
            raise GraphRepairError(f"Repair target {node.id!r} must use replace write mode")
        partition = writer.partitioning
        if partition is None or partition.field is None:
            raise GraphRepairError(
                f"Repair target {node.id!r} needs a DATE/TIMESTAMP partition field"
            )
        fields = {field.name: (field.cast_to or field.type).upper() for field in node.fields}
        partition_type = fields.get(partition.field)
        if partition_type not in {"DATE", "TIMESTAMP"}:
            raise GraphRepairError(
                f"Repair target {node.id!r} partition field must be DATE or TIMESTAMP"
            )
        keys = tuple(writer.destination.business_key)
        if (
            not keys
            or len(keys) != len(set(keys))
            or any(fields.get(key) not in _KEY_TYPES for key in keys)
        ):
            raise GraphRepairError(
                f"Repair target {node.id!r} needs distinct, declared scalar business-key fields"
            )
        targets.append(
            GraphTargetRepair(
                node_id=node.id,
                partition_field=partition.field,
                partition_type="DATE" if partition_type == "DATE" else "TIMESTAMP",
                business_key=keys,
            )
        )
    if not targets:
        raise GraphRepairError("Date repair requires at least one configured output")
    return tuple(targets)
