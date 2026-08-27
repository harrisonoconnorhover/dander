"""Canonical provider-neutral physical plans for hosted and direct execution."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

PHYSICAL_PLAN_SCHEMA = "io.dander.physical-plan/v1"

_PORTABLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_PIPELINE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_PLAN_BYTES = 64 * 1024
_MAX_STAGES = 100
_MAX_EXCHANGES = 200
_MAX_PARTITIONS = 10_000
_MAX_OPERATORS = 10_000


class PhysicalPlanError(ValueError):
    """A physical plan is malformed, non-canonical, or internally inconsistent."""


class PhysicalExecutionMode(StrEnum):
    """How a backend executes the statically planned stage graph."""

    FUSED_CONTAINER = "fused_container"
    DISTRIBUTED = "distributed"


class PartitioningStrategy(StrEnum):
    """Fixed partition routing selected before a run is submitted."""

    SINGLE = "single"
    HASH = "hash"
    ROUND_ROBIN = "round_robin"


class ExchangeTransport(StrEnum):
    """Provider-neutral handoff between two physical stages."""

    MEMORY = "memory"
    OBJECT_STORE = "object_store"


@dataclass(frozen=True, slots=True)
class PhysicalStage:
    """One statically bounded group of pipeline operators."""

    stage_id: str
    operators: tuple[str, ...]
    partition_count: int
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_portable_id(self.stage_id, label="stage")
        if (
            not self.operators
            or len(self.operators) > _MAX_OPERATORS
            or tuple(sorted(set(self.operators))) != self.operators
            or any(_PIPELINE_ID.fullmatch(item) is None for item in self.operators)
        ):
            raise PhysicalPlanError("stage operators must be unique canonical identifiers")
        if (
            isinstance(self.partition_count, bool)
            or not isinstance(self.partition_count, int)
            or not 1 <= self.partition_count <= _MAX_PARTITIONS
        ):
            raise PhysicalPlanError("stage partition count is invalid")
        if (
            tuple(sorted(set(self.depends_on))) != self.depends_on
            or any(_PORTABLE_ID.fullmatch(item) is None for item in self.depends_on)
            or self.stage_id in self.depends_on
        ):
            raise PhysicalPlanError("stage dependencies must be unique canonical stage ids")


@dataclass(frozen=True, slots=True)
class PhysicalExchange:
    """One explicit partitioned handoff between producer and consumer stages."""

    exchange_id: str
    producer_stage_id: str
    consumer_stage_id: str
    transport: ExchangeTransport
    partitioning: PartitioningStrategy
    partition_count: int
    partition_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.transport, ExchangeTransport) or not isinstance(
            self.partitioning, PartitioningStrategy
        ):
            raise PhysicalPlanError("exchange transport or partitioning is invalid")
        for label, value in (
            ("exchange", self.exchange_id),
            ("producer stage", self.producer_stage_id),
            ("consumer stage", self.consumer_stage_id),
        ):
            _require_portable_id(value, label=label)
        if self.producer_stage_id == self.consumer_stage_id:
            raise PhysicalPlanError("an exchange cannot connect a stage to itself")
        if (
            isinstance(self.partition_count, bool)
            or not isinstance(self.partition_count, int)
            or not 1 <= self.partition_count <= _MAX_PARTITIONS
        ):
            raise PhysicalPlanError("exchange partition count is invalid")
        if tuple(sorted(set(self.partition_keys))) != self.partition_keys or any(
            _PIPELINE_ID.fullmatch(item) is None for item in self.partition_keys
        ):
            raise PhysicalPlanError("exchange partition keys must be unique canonical identifiers")
        if self.partitioning is PartitioningStrategy.SINGLE:
            if self.partition_count != 1 or self.partition_keys:
                raise PhysicalPlanError("single partitioning requires one partition and no keys")
        elif self.partitioning is PartitioningStrategy.HASH:
            if not self.partition_keys:
                raise PhysicalPlanError("hash partitioning requires at least one key")
        elif self.partition_keys:
            raise PhysicalPlanError("round-robin partitioning cannot declare keys")


@dataclass(frozen=True, slots=True)
class PhysicalPlan:
    """One immutable static stage DAG selected without provider-specific resources."""

    pipeline_id: str
    execution_mode: PhysicalExecutionMode
    stages: tuple[PhysicalStage, ...]
    exchanges: tuple[PhysicalExchange, ...]
    maximum_parallelism: int
    schema: str = PHYSICAL_PLAN_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PHYSICAL_PLAN_SCHEMA:
            raise PhysicalPlanError("unsupported physical-plan contract")
        if not isinstance(self.execution_mode, PhysicalExecutionMode):
            raise PhysicalPlanError("physical-plan execution mode is invalid")
        if _PIPELINE_ID.fullmatch(self.pipeline_id) is None:
            raise PhysicalPlanError("physical-plan pipeline id is invalid")
        if (
            not self.stages
            or len(self.stages) > _MAX_STAGES
            or tuple(sorted(self.stages, key=lambda item: item.stage_id)) != self.stages
            or len({item.stage_id for item in self.stages}) != len(self.stages)
        ):
            raise PhysicalPlanError("physical-plan stages must be unique and canonically ordered")
        if (
            len(self.exchanges) > _MAX_EXCHANGES
            or tuple(sorted(self.exchanges, key=lambda item: item.exchange_id)) != self.exchanges
            or len({item.exchange_id for item in self.exchanges}) != len(self.exchanges)
        ):
            raise PhysicalPlanError(
                "physical-plan exchanges must be unique and canonically ordered"
            )
        total_partitions = sum(stage.partition_count for stage in self.stages)
        if total_partitions > _MAX_PARTITIONS:
            raise PhysicalPlanError("physical-plan stage partitions exceed the static bound")
        if (
            isinstance(self.maximum_parallelism, bool)
            or not isinstance(self.maximum_parallelism, int)
            or not 1 <= self.maximum_parallelism <= total_partitions
        ):
            raise PhysicalPlanError("physical-plan maximum parallelism is invalid")

        by_id = {stage.stage_id: stage for stage in self.stages}
        operators = [operator for stage in self.stages for operator in stage.operators]
        if len(operators) != len(set(operators)) or len(operators) > _MAX_OPERATORS:
            raise PhysicalPlanError("physical-plan operators must belong to exactly one stage")
        dependency_edges = {
            (dependency, stage.stage_id) for stage in self.stages for dependency in stage.depends_on
        }
        if any(dependency not in by_id for dependency, _consumer in dependency_edges):
            raise PhysicalPlanError("physical-plan stage dependency is unknown")
        exchange_edges = {
            (exchange.producer_stage_id, exchange.consumer_stage_id) for exchange in self.exchanges
        }
        if len(exchange_edges) != len(self.exchanges) or exchange_edges != dependency_edges:
            raise PhysicalPlanError("each stage dependency requires exactly one exchange")
        for exchange in self.exchanges:
            consumer = by_id.get(exchange.consumer_stage_id)
            if consumer is None or exchange.producer_stage_id not in by_id:
                raise PhysicalPlanError("physical-plan exchange references an unknown stage")
            if exchange.partition_count != consumer.partition_count:
                raise PhysicalPlanError("exchange partition count must match its consumer stage")
        _require_acyclic(by_id)
        if self.execution_mode is PhysicalExecutionMode.FUSED_CONTAINER:
            if self.maximum_parallelism != 1 or any(
                stage.partition_count != 1 for stage in self.stages
            ):
                raise PhysicalPlanError(
                    "fused-container plans require one partition per stage and parallelism one"
                )
            if any(
                exchange.transport is not ExchangeTransport.MEMORY
                or exchange.partitioning is not PartitioningStrategy.SINGLE
                for exchange in self.exchanges
            ):
                raise PhysicalPlanError(
                    "fused-container exchanges must use single in-memory handoffs"
                )

    @property
    def revision(self) -> str:
        """Return the SHA-256 identity of the canonical versioned contents."""
        return hashlib.sha256(canonical_physical_plan_contents(self)).hexdigest()

    @property
    def partition_count(self) -> int:
        """Return the statically planned number of stage partitions."""
        return sum(stage.partition_count for stage in self.stages)


def fused_container_physical_plan(
    pipeline_id: str,
    *,
    stages: tuple[PhysicalStage, ...] | None = None,
    exchanges: tuple[PhysicalExchange, ...] = (),
) -> PhysicalPlan:
    """Build the compatibility plan executed by one existing Dander container."""
    return PhysicalPlan(
        pipeline_id=pipeline_id,
        execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
        stages=stages
        or (
            PhysicalStage(
                stage_id="pipeline",
                operators=(pipeline_id,),
                partition_count=1,
            ),
        ),
        exchanges=exchanges,
        maximum_parallelism=1,
    )


def canonical_physical_plan_contents(plan: PhysicalPlan) -> bytes:
    """Return canonical contents excluding the derived revision."""
    return _canonical_json(
        {
            "schema": PHYSICAL_PLAN_SCHEMA,
            "plan": _physical_plan_payload(plan),
        }
    )


def serialize_physical_plan(plan: PhysicalPlan) -> bytes:
    """Return one canonical, self-verifying physical-plan envelope."""
    return _canonical_json(
        {
            "schema": PHYSICAL_PLAN_SCHEMA,
            "revision": plan.revision,
            "plan": _physical_plan_payload(plan),
        }
    )


def deserialize_physical_plan(data: bytes) -> PhysicalPlan:
    """Load canonical bytes and reject content or revision tampering."""
    if not isinstance(data, bytes) or not data or len(data) > _MAX_PLAN_BYTES:
        raise PhysicalPlanError("physical-plan size is invalid")
    try:
        raw = json.loads(data)
        envelope = _mapping(raw, "physical-plan envelope")
        if envelope.get("schema") != PHYSICAL_PLAN_SCHEMA:
            raise PhysicalPlanError("unsupported physical-plan schema")
        values = _mapping(envelope["plan"], "physical plan")
        stages = tuple(_physical_stage(item) for item in _sequence(values["stages"], "stages"))
        exchanges = tuple(
            _physical_exchange(item) for item in _sequence(values["exchanges"], "exchanges")
        )
        plan = PhysicalPlan(
            pipeline_id=_string(values["pipeline_id"], "pipeline_id"),
            execution_mode=_enum(
                PhysicalExecutionMode,
                values["execution_mode"],
                "execution mode",
            ),
            stages=stages,
            exchanges=exchanges,
            maximum_parallelism=_integer(values["maximum_parallelism"], "maximum_parallelism"),
        )
        revision = _string(envelope["revision"], "revision")
        if revision != plan.revision:
            raise PhysicalPlanError("physical-plan revision does not match its contents")
        if data != serialize_physical_plan(plan):
            raise PhysicalPlanError("physical-plan record is not canonical")
        return plan
    except PhysicalPlanError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise PhysicalPlanError("physical-plan record is invalid") from error


def _physical_plan_payload(plan: PhysicalPlan) -> dict[str, object]:
    return {
        "pipeline_id": plan.pipeline_id,
        "execution_mode": plan.execution_mode.value,
        "stages": [
            {
                "stage_id": stage.stage_id,
                "operators": list(stage.operators),
                "partition_count": stage.partition_count,
                "depends_on": list(stage.depends_on),
            }
            for stage in plan.stages
        ],
        "exchanges": [
            {
                "exchange_id": exchange.exchange_id,
                "producer_stage_id": exchange.producer_stage_id,
                "consumer_stage_id": exchange.consumer_stage_id,
                "transport": exchange.transport.value,
                "partitioning": exchange.partitioning.value,
                "partition_count": exchange.partition_count,
                "partition_keys": list(exchange.partition_keys),
            }
            for exchange in plan.exchanges
        ],
        "maximum_parallelism": plan.maximum_parallelism,
    }


def _physical_stage(value: object) -> PhysicalStage:
    values = _mapping(value, "physical stage")
    return PhysicalStage(
        stage_id=_string(values["stage_id"], "stage_id"),
        operators=_string_tuple(values["operators"], "operators"),
        partition_count=_integer(values["partition_count"], "partition_count"),
        depends_on=_string_tuple(values["depends_on"], "depends_on"),
    )


def _physical_exchange(value: object) -> PhysicalExchange:
    values = _mapping(value, "physical exchange")
    return PhysicalExchange(
        exchange_id=_string(values["exchange_id"], "exchange_id"),
        producer_stage_id=_string(values["producer_stage_id"], "producer_stage_id"),
        consumer_stage_id=_string(values["consumer_stage_id"], "consumer_stage_id"),
        transport=_enum(ExchangeTransport, values["transport"], "exchange transport"),
        partitioning=_enum(
            PartitioningStrategy,
            values["partitioning"],
            "partitioning strategy",
        ),
        partition_count=_integer(values["partition_count"], "partition_count"),
        partition_keys=_string_tuple(values["partition_keys"], "partition_keys"),
    )


def _require_acyclic(stages: Mapping[str, PhysicalStage]) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(stage_id: str) -> None:
        if stage_id in active:
            raise PhysicalPlanError("physical-plan stage dependencies contain a cycle")
        if stage_id in visited:
            return
        active.add(stage_id)
        for dependency in stages[stage_id].depends_on:
            visit(dependency)
        active.remove(stage_id)
        visited.add(stage_id)

    for stage_id in stages:
        visit(stage_id)


def _require_portable_id(value: str, *, label: str) -> None:
    if _PORTABLE_ID.fullmatch(value) is None:
        raise PhysicalPlanError(f"{label} id is invalid")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise PhysicalPlanError(f"{label} must be an object")
    return cast("Mapping[str, object]", value)


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise PhysicalPlanError(f"{label} must be a list")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PhysicalPlanError(f"{label} must be a string")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    items = _sequence(value, label)
    if any(not isinstance(item, str) for item in items):
        raise PhysicalPlanError(f"{label} must contain strings")
    return tuple(cast("list[str]", items))


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhysicalPlanError(f"{label} must be an integer")
    return value


def _enum[T: StrEnum](enum_type: type[T], value: object, label: str) -> T:
    try:
        return enum_type(_string(value, label))
    except ValueError as error:
        raise PhysicalPlanError(f"{label} is invalid") from error


__all__ = [
    "PHYSICAL_PLAN_SCHEMA",
    "ExchangeTransport",
    "PartitioningStrategy",
    "PhysicalExchange",
    "PhysicalExecutionMode",
    "PhysicalPlan",
    "PhysicalPlanError",
    "PhysicalStage",
    "canonical_physical_plan_contents",
    "deserialize_physical_plan",
    "fused_container_physical_plan",
    "serialize_physical_plan",
]
