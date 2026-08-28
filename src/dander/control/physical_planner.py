"""Deterministic graph-to-physical planning for Control's first bounded execution modes."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from dander.physical_plan import (
    ExchangeTransport,
    PartitioningStrategy,
    PhysicalExchange,
    PhysicalExecutionMode,
    PhysicalPlan,
    PhysicalPlanError,
    PhysicalStage,
    serialize_physical_plan,
)
from dander.pipeline.errors import GraphValidationError
from dander.pipeline.node_config import TransformNodeConfig

if TYPE_CHECKING:
    from dander.control.models import PipelineGraphDocument
    from dander.deployment.projection import ExecutionTemplate
    from dander.pipeline.graph import PipelineGraph

_DEFAULT_DISTRIBUTED_PARTITIONS = 2
_MAX_DISTRIBUTED_PARTITIONS = 2_000
_KNOWN_NODE_TYPES = frozenset({"source", "transform", "target"})


class PhysicalPlanningError(ValueError):
    """A canonical graph cannot be represented by the requested bounded physical mode."""


class PhysicalPlanner(Protocol):
    """Provider-neutral extension point for compiling one canonical graph."""

    def plan(
        self,
        document: PipelineGraphDocument,
        *,
        pipeline_id: str,
        execution_mode: PhysicalExecutionMode,
        distributed_partitions: int | None = None,
    ) -> PhysicalPlan:
        """Compile the graph into one immutable physical plan."""


class StaticPhysicalPlanner:
    """Compile fused graphs or the first fixed two-stage distributed graph shape."""

    def plan(
        self,
        document: PipelineGraphDocument,
        *,
        pipeline_id: str,
        execution_mode: PhysicalExecutionMode,
        distributed_partitions: int | None = None,
    ) -> PhysicalPlan:
        """Return a deterministic plan without provider calls or runtime sizing."""
        try:
            graph = document.to_domain()
        except GraphValidationError as error:
            raise PhysicalPlanningError(
                "The pipeline graph is not valid for physical planning."
            ) from error
        if not graph.nodes:
            raise PhysicalPlanningError("Physical planning requires at least one graph node.")
        if any(node.type not in _KNOWN_NODE_TYPES for node in graph.nodes):
            raise PhysicalPlanningError("Physical planning does not support this graph node type.")
        if not isinstance(execution_mode, PhysicalExecutionMode):
            raise PhysicalPlanningError("The requested physical execution mode is invalid.")
        if distributed_partitions is not None and (
            isinstance(distributed_partitions, bool)
            or not isinstance(distributed_partitions, int)
            or not 2 <= distributed_partitions <= _MAX_DISTRIBUTED_PARTITIONS
        ):
            raise PhysicalPlanningError("Distributed partition count is outside its static bound.")

        try:
            if execution_mode is PhysicalExecutionMode.FUSED_CONTAINER:
                if distributed_partitions is not None:
                    raise PhysicalPlanningError(
                        "Fused planning does not accept a distributed partition count."
                    )
                return self._fused_plan(graph, pipeline_id=pipeline_id)
            if execution_mode is PhysicalExecutionMode.DISTRIBUTED:
                return self._distributed_plan(
                    graph,
                    pipeline_id=pipeline_id,
                    partition_count=(distributed_partitions or _DEFAULT_DISTRIBUTED_PARTITIONS),
                )
            raise PhysicalPlanningError("The requested physical execution mode is unsupported.")
        except PhysicalPlanError as error:
            raise PhysicalPlanningError(
                "The pipeline identifiers cannot be represented in a physical plan."
            ) from error

    @staticmethod
    def _fused_plan(graph: PipelineGraph, *, pipeline_id: str) -> PhysicalPlan:
        return PhysicalPlan(
            pipeline_id=pipeline_id,
            execution_mode=PhysicalExecutionMode.FUSED_CONTAINER,
            stages=(
                PhysicalStage(
                    stage_id="pipeline",
                    operators=tuple(sorted(node.id for node in graph.nodes)),
                    partition_count=1,
                ),
            ),
            exchanges=(),
            maximum_parallelism=1,
        )

    @staticmethod
    def _distributed_plan(
        graph: PipelineGraph,
        *,
        pipeline_id: str,
        partition_count: int,
    ) -> PhysicalPlan:
        by_type = {
            node_type: tuple(
                sorted(
                    (node for node in graph.nodes if node.type == node_type),
                    key=lambda item: item.id,
                )
            )
            for node_type in _KNOWN_NODE_TYPES
        }
        if any(len(by_type[node_type]) != 1 for node_type in _KNOWN_NODE_TYPES):
            raise PhysicalPlanningError(
                "Distributed planning supports exactly one source, transform, and target node."
            )
        source = by_type["source"][0]
        transform = by_type["transform"][0]
        target = by_type["target"][0]
        expected_edges = ((source.id, transform.id), (transform.id, target.id))
        actual_edges = tuple(sorted((edge.source, edge.target) for edge in graph.edges))
        if actual_edges != tuple(sorted(expected_edges)):
            raise PhysicalPlanningError(
                "Distributed planning supports only a source-to-transform-to-target chain."
            )
        if any(edge.join is not None for edge in graph.edges) or not isinstance(
            transform.config, TransformNodeConfig
        ):
            raise PhysicalPlanningError("Distributed planning does not support joins yet.")
        if transform.config.join is not None:
            raise PhysicalPlanningError("Distributed planning does not support joins yet.")

        return PhysicalPlan(
            pipeline_id=pipeline_id,
            execution_mode=PhysicalExecutionMode.DISTRIBUTED,
            stages=(
                PhysicalStage(
                    stage_id="extract",
                    operators=(source.id,),
                    partition_count=partition_count,
                ),
                PhysicalStage(
                    stage_id="transform",
                    operators=tuple(sorted((transform.id, target.id))),
                    partition_count=partition_count,
                    depends_on=("extract",),
                ),
            ),
            exchanges=(
                PhysicalExchange(
                    exchange_id="extract-transform",
                    producer_stage_id="extract",
                    consumer_stage_id="transform",
                    transport=ExchangeTransport.OBJECT_STORE,
                    partitioning=PartitioningStrategy.ROUND_ROBIN,
                    partition_count=partition_count,
                ),
            ),
            maximum_parallelism=partition_count,
        )


def bind_physical_plan(
    template: ExecutionTemplate,
    physical_plan: PhysicalPlan,
) -> ExecutionTemplate:
    """Append the exact canonical plan to an otherwise unchanged execution command."""
    if template.pipeline_id != physical_plan.pipeline_id:
        raise PhysicalPlanningError(
            "The physical plan pipeline does not match the execution template."
        )
    if "--physical-plan" in template.command:
        raise PhysicalPlanningError("The execution template already contains a physical plan.")
    if template.schedule.maximum_parallelism < physical_plan.maximum_parallelism:
        raise PhysicalPlanningError(
            "The execution template cannot satisfy the planned maximum parallelism."
        )
    return replace(
        template,
        command=(
            *template.command,
            "--physical-plan",
            serialize_physical_plan(physical_plan).decode("utf-8"),
        ),
    )


__all__ = [
    "PhysicalPlanner",
    "PhysicalPlanningError",
    "StaticPhysicalPlanner",
    "bind_physical_plan",
]
