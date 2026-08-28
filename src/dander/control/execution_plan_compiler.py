"""Deployment-time compilation of canonical graphs into hosted execution plans."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from dander.control.orchestration import (
    ExecutionPlan,
    RetryPolicy,
    SizeClassCandidate,
    format_size_class_candidate_spec,
)
from dander.control.orchestration_serialization import serialize_execution_plan
from dander.control.physical_planner import (
    PhysicalPlanner,
    StaticPhysicalPlanner,
    bind_physical_plan,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dander.control.graph_store import GraphRecord
    from dander.deployment.projection import ExecutionTemplate
    from dander.physical_plan import PhysicalExecutionMode

_MAX_EXECUTION_PLANS = 100
_MAX_INPUT_BYTES = 2**63 - 1
_SIZE_CLASS = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_MANAGED_SPARK_CORES_MILLIS = frozenset({4_000, 8_000, 16_000})


class ExecutionPlanCompilationError(ValueError):
    """A deployment-time execution-plan set is empty, oversized, or ambiguous."""


@dataclass(frozen=True, slots=True)
class ExecutionPlanProfile:
    """Control-owned selection intent layered over an existing backend template."""

    plan_id: str
    environment: str
    execution_mode: PhysicalExecutionMode
    distributed_partitions: int | None = None


@dataclass(frozen=True, slots=True)
class ManagedSparkSizeClass:
    """One bounded static executor shape and caller-supplied input ceiling."""

    size_class: str
    max_input_bytes: int
    executor_count: int
    executor_cpu_millis: int
    executor_memory_mib: int

    def __post_init__(self) -> None:
        if _SIZE_CLASS.fullmatch(self.size_class) is None:
            raise ExecutionPlanCompilationError("Managed Spark size class is invalid.")
        if (
            isinstance(self.max_input_bytes, bool)
            or not isinstance(self.max_input_bytes, int)
            or not 0 <= self.max_input_bytes <= _MAX_INPUT_BYTES
        ):
            raise ExecutionPlanCompilationError(
                "Managed Spark size-class input ceiling is invalid."
            )
        if (
            isinstance(self.executor_count, bool)
            or not isinstance(self.executor_count, int)
            or not 2 <= self.executor_count <= 2_000
        ):
            raise ExecutionPlanCompilationError(
                "Managed Spark executor count is outside its static bound."
            )
        if self.executor_cpu_millis not in _MANAGED_SPARK_CORES_MILLIS:
            raise ExecutionPlanCompilationError(
                "Managed Spark executor CPU must be 4, 8, or 16 cores."
            )
        cores = self.executor_cpu_millis // 1_000
        if (
            isinstance(self.executor_memory_mib, bool)
            or not isinstance(self.executor_memory_mib, int)
            or not 1_024 <= self.executor_memory_mib // cores <= 7_424
        ):
            raise ExecutionPlanCompilationError(
                "Managed Spark executor memory per core is outside its static bound."
            )


@dataclass(frozen=True, slots=True)
class CompiledSizeClassPlans:
    """Canonical plans and existing Control size-candidate startup arguments."""

    plans: tuple[ExecutionPlan, ...]
    size_class_candidates: tuple[SizeClassCandidate, ...]

    @property
    def execution_plan_json(self) -> tuple[str, ...]:
        """Return canonical execution-plan JSON in revision order."""
        return tuple(serialize_execution_plan(plan).decode("utf-8") for plan in self.plans)

    @property
    def size_candidate_specs(self) -> tuple[str, ...]:
        """Return canonical startup arguments in matching revision order."""
        return tuple(
            format_size_class_candidate_spec(candidate) for candidate in self.size_class_candidates
        )


class ExecutionPlanCompiler:
    """Compile immutable plans without provider calls or runtime graph interpretation."""

    def __init__(self, planner: PhysicalPlanner | None = None) -> None:
        self._planner = planner if planner is not None else StaticPhysicalPlanner()

    def compile(
        self,
        graph: GraphRecord,
        profile: ExecutionPlanProfile,
        template: ExecutionTemplate,
    ) -> ExecutionPlan:
        """Bind one canonical graph and backend template into an immutable plan."""
        schedule = replace(template.schedule, expression=None, time_zone=None)
        unscheduled_template = replace(template, schedule=schedule)
        if unscheduled_template.launcher == "dataproc_serverless":
            if "--graph-content-sha256" in unscheduled_template.command:
                raise ExecutionPlanCompilationError(
                    "The managed Spark template already contains a graph content identity."
                )
            unscheduled_template = replace(
                unscheduled_template,
                command=(
                    *unscheduled_template.command,
                    "--graph-content-sha256",
                    graph.content_sha256,
                ),
            )
        physical_plan = self._planner.plan(
            graph.document,
            pipeline_id=unscheduled_template.pipeline_id,
            execution_mode=profile.execution_mode,
            distributed_partitions=profile.distributed_partitions,
        )
        bound_template = bind_physical_plan(unscheduled_template, physical_plan)
        return ExecutionPlan(
            plan_id=profile.plan_id,
            environment=profile.environment,
            project=graph.project,
            graph=graph.graph,
            graph_revision=graph.revision,
            graph_content_sha256=graph.content_sha256,
            backend_id=bound_template.launcher,
            profile_id=bound_template.profile_id,
            image=bound_template.image,
            execution_template=bound_template,
            deadline_seconds=bound_template.resources.deadline_seconds,
            retry_policy=RetryPolicy(
                max_attempts=bound_template.resources.launcher_retry_count + 1
            ),
            physical_plan=physical_plan,
        )

    def compile_managed_spark_size_classes(
        self,
        graph: GraphRecord,
        profile: ExecutionPlanProfile,
        template: ExecutionTemplate,
        size_classes: Iterable[ManagedSparkSizeClass],
    ) -> CompiledSizeClassPlans:
        """Materialize deterministic static Spark plans for Control's existing selector."""
        from dander.physical_plan import PhysicalExecutionMode

        if (
            profile.execution_mode is not PhysicalExecutionMode.DISTRIBUTED
            or profile.distributed_partitions is not None
            or template.launcher != "dataproc_serverless"
        ):
            raise ExecutionPlanCompilationError(
                "Managed Spark size classes require one unsized distributed profile."
            )
        ordered = tuple(
            sorted(size_classes, key=lambda item: (item.max_input_bytes, item.size_class))
        )
        if not ordered or len(ordered) > _MAX_EXECUTION_PLANS:
            raise ExecutionPlanCompilationError(
                "Managed Spark size-class compilation requires 1 to 100 classes."
            )
        if len({item.size_class for item in ordered}) != len(ordered) or len(
            {item.max_input_bytes for item in ordered}
        ) != len(ordered):
            raise ExecutionPlanCompilationError(
                "Managed Spark size classes require unique names and input ceilings."
            )
        compiled: list[tuple[ExecutionPlan, SizeClassCandidate]] = []
        for size in ordered:
            sized_template = replace(
                template,
                resources=replace(
                    template.resources,
                    cpu_millis=size.executor_cpu_millis,
                    memory_mib=size.executor_memory_mib,
                ),
                schedule=replace(
                    template.schedule,
                    task_count=size.executor_count,
                    maximum_parallelism=size.executor_count,
                ),
            )
            plan = self.compile(
                graph,
                replace(
                    profile,
                    plan_id=f"{profile.plan_id}-{size.size_class}",
                    distributed_partitions=size.executor_count,
                ),
                sized_template,
            )
            compiled.append(
                (
                    plan,
                    SizeClassCandidate(
                        plan_revision=plan.revision,
                        size_class=size.size_class,
                        max_input_bytes=size.max_input_bytes,
                    ),
                )
            )
        by_revision = {plan.revision: (plan, candidate) for plan, candidate in compiled}
        if len(by_revision) != len(compiled):
            raise ExecutionPlanCompilationError(
                "Managed Spark size-class compilation produced duplicate revisions."
            )
        canonical = tuple(by_revision[revision] for revision in sorted(by_revision))
        return CompiledSizeClassPlans(
            plans=tuple(plan for plan, _candidate in canonical),
            size_class_candidates=tuple(candidate for _plan, candidate in canonical),
        )


def compile_execution_plan_json(
    graph: GraphRecord,
    profiles: Iterable[tuple[ExecutionPlanProfile, ExecutionTemplate]],
    *,
    planner: PhysicalPlanner | None = None,
) -> tuple[str, ...]:
    """Return revision-sorted canonical JSON for deployment plan-file rendering."""
    compiler = ExecutionPlanCompiler(planner)
    plans = tuple(compiler.compile(graph, profile, template) for profile, template in profiles)
    if not plans or len(plans) > _MAX_EXECUTION_PLANS:
        raise ExecutionPlanCompilationError(
            "Execution-plan compilation requires 1 to 100 profiles."
        )
    by_revision = {plan.revision: plan for plan in plans}
    if len(by_revision) != len(plans):
        raise ExecutionPlanCompilationError(
            "Execution-plan compilation produced duplicate revisions."
        )
    return tuple(
        serialize_execution_plan(by_revision[revision]).decode("utf-8")
        for revision in sorted(by_revision)
    )


__all__ = [
    "ExecutionPlanCompilationError",
    "ExecutionPlanCompiler",
    "ExecutionPlanProfile",
    "CompiledSizeClassPlans",
    "ManagedSparkSizeClass",
    "compile_execution_plan_json",
]
