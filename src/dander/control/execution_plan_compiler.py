"""Deployment-time compilation of canonical graphs into hosted execution plans."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from dander.control.orchestration import ExecutionPlan, RetryPolicy
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


class ExecutionPlanCompilationError(ValueError):
    """A deployment-time execution-plan set is empty, oversized, or ambiguous."""


@dataclass(frozen=True, slots=True)
class ExecutionPlanProfile:
    """Control-owned selection intent layered over an existing backend template."""

    plan_id: str
    environment: str
    execution_mode: PhysicalExecutionMode


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
    "compile_execution_plan_json",
]
