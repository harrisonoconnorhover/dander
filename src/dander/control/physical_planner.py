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
from dander.pipeline.node_config import ExecutableJoinType, TransformNodeConfig

if TYPE_CHECKING:
    from dander.control.models import PipelineGraphDocument
    from dander.deployment.projection import ExecutionTemplate
    from dander.pipeline.graph import Node, PipelineGraph

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
    """Compile fused graphs or the two explicitly supported distributed graph shapes."""

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
        if (
            len(by_type["source"]) == 2
            and len(by_type["transform"]) == 1
            and len(by_type["target"]) == 1
        ):
            return StaticPhysicalPlanner._keyed_join_plan(
                graph,
                pipeline_id=pipeline_id,
                partition_count=partition_count,
                sources=by_type["source"],
                transform=by_type["transform"][0],
                target=by_type["target"][0],
            )
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

    @staticmethod
    def _keyed_join_plan(
        graph: PipelineGraph,
        *,
        pipeline_id: str,
        partition_count: int,
        sources: tuple[Node, ...],
        transform: Node,
        target: Node,
    ) -> PhysicalPlan:
        config = transform.config
        if not isinstance(config, TransformNodeConfig) or config.join is None:
            raise PhysicalPlanningError(
                "Distributed join planning requires one explicit transform join."
            )
        join = config.join
        if (
            join.type is not ExecutableJoinType.INNER
            or len(join.keys) != 1
            or config.operations
            or any(edge.join is not None for edge in graph.edges)
        ):
            raise PhysicalPlanningError(
                "Distributed join planning supports one inner equality key without operations."
            )
        by_id = {source.id: source for source in sources}
        if set(by_id) != {join.left_input, join.right_input}:
            raise PhysicalPlanningError(
                "Distributed join inputs must match the graph's two source nodes."
            )
        expected_edges = {
            (join.left_input, transform.id),
            (join.right_input, transform.id),
            (transform.id, target.id),
        }
        if {(edge.source, edge.target) for edge in graph.edges} != expected_edges:
            raise PhysicalPlanningError(
                "Distributed join planning supports only two sources feeding one join target."
            )
        key = join.keys[0]
        left_types = {field.name: field.type.upper() for field in by_id[join.left_input].fields}
        right_types = {field.name: field.type.upper() for field in by_id[join.right_input].fields}
        if (
            key.left not in left_types
            or key.right not in right_types
            or left_types[key.left] != right_types[key.right]
        ):
            raise PhysicalPlanningError(
                "Distributed join keys must exist and have the same declared type."
            )

        return PhysicalPlan(
            pipeline_id=pipeline_id,
            execution_mode=PhysicalExecutionMode.DISTRIBUTED,
            stages=(
                PhysicalStage(
                    stage_id="extract-left",
                    operators=(join.left_input,),
                    partition_count=partition_count,
                ),
                PhysicalStage(
                    stage_id="extract-right",
                    operators=(join.right_input,),
                    partition_count=partition_count,
                ),
                PhysicalStage(
                    stage_id="join",
                    operators=tuple(sorted((transform.id, target.id))),
                    partition_count=partition_count,
                    depends_on=("extract-left", "extract-right"),
                ),
            ),
            exchanges=(
                PhysicalExchange(
                    exchange_id="left-join",
                    producer_stage_id="extract-left",
                    consumer_stage_id="join",
                    transport=ExchangeTransport.OBJECT_STORE,
                    partitioning=PartitioningStrategy.HASH,
                    partition_count=partition_count,
                    partition_keys=(key.left,),
                ),
                PhysicalExchange(
                    exchange_id="right-join",
                    producer_stage_id="extract-right",
                    consumer_stage_id="join",
                    transport=ExchangeTransport.OBJECT_STORE,
                    partitioning=PartitioningStrategy.HASH,
                    partition_count=partition_count,
                    partition_keys=(key.right,),
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
