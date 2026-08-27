"""Startup composition for Control-owned hosted execution."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.control.application import ControlOperationError
from dander.control.cloud_run_execution_backend import CloudRunExecutionBackend
from dander.control.fargate_execution_backend import FargateExecutionBackend
from dander.control.orchestration import (
    ExecutionBackendError,
    ExecutionPlan,
    OrchestrationContractError,
    PlacementCandidate,
    RunStoreError,
)
from dander.control.orchestration_serialization import (
    OrchestrationSerializationError,
    deserialize_execution_plan,
    deserialize_trigger_spec,
)
from dander.control.run_lifecycle import (
    ControlRunLifecycle,
    ExecutionBackendRegistry,
    ExecutionPlanRegistry,
    PlanRunSubmissionResolver,
)
from dander.control.s3_run_store import S3RunStore
from dander.control.schedule_consumer import (
    ControlScheduleConsumer,
    ScheduledRunSubmissionResolver,
    ScheduleQueueError,
)
from dander.control.sqs_schedule_queue import SQSScheduleQueue
from dander.providers.cloud_run import CloudRunBinding, CloudRunOperationError
from dander.providers.fargate.operations import FargateBinding, FargateOperationError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from dander.control.graph_store import GraphStore
    from dander.control.orchestration import ExecutionBackend, RunStore, TriggerSpec

_MAX_PLAN_FILES = 100
_MAX_PLAN_BYTES = 1024 * 1024


class ControlRunCompositionError(ValueError):
    """The hosted run startup binding is incomplete or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ControlRunComposition:
    """The lifecycle and compatibility resolver installed into ControlApplication."""

    lifecycle: ControlRunLifecycle
    resolver: PlanRunSubmissionResolver


def load_execution_plans(paths: Sequence[Path]) -> tuple[ExecutionPlan, ...]:
    """Load a bounded set of canonical plan files with verified content revisions."""
    if not paths or len(paths) > _MAX_PLAN_FILES:
        raise ControlRunCompositionError("Control requires 1 to 100 execution-plan files.")
    plans: list[ExecutionPlan] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        try:
            size = resolved.stat().st_size
            if size < 1 or size > _MAX_PLAN_BYTES:
                raise ControlRunCompositionError(
                    "A Control execution-plan file exceeds its size bound."
                )
            plans.append(deserialize_execution_plan(resolved.read_bytes()))
        except ControlRunCompositionError:
            raise
        except (OSError, OrchestrationSerializationError) as error:
            raise ControlRunCompositionError(
                "A Control execution-plan file is unavailable or invalid."
            ) from error
    try:
        registry = ExecutionPlanRegistry(plans)
    except OrchestrationContractError as error:
        raise ControlRunCompositionError("The Control execution-plan set is invalid.") from error
    return registry.plans


def load_trigger_specs(paths: Sequence[Path]) -> tuple[TriggerSpec, ...]:
    """Load a bounded set of canonical scheduled-trigger files."""
    if not paths or len(paths) > _MAX_PLAN_FILES:
        raise ControlRunCompositionError("Control requires 1 to 100 trigger-spec files.")
    specs: list[TriggerSpec] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        try:
            size = resolved.stat().st_size
            if size < 1 or size > _MAX_PLAN_BYTES:
                raise ControlRunCompositionError(
                    "A Control trigger-spec file exceeds its size bound."
                )
            specs.append(deserialize_trigger_spec(resolved.read_bytes()))
        except ControlRunCompositionError:
            raise
        except (OSError, OrchestrationSerializationError) as error:
            raise ControlRunCompositionError(
                "A Control trigger-spec file is unavailable or invalid."
            ) from error
    return tuple(specs)


def compose_run_control(
    *,
    graph_store: GraphStore,
    store: RunStore,
    plans: Iterable[ExecutionPlan],
    backends: Mapping[str, ExecutionBackend],
    environment: str,
    placement_candidates: Iterable[PlacementCandidate] = (),
    preferred_locality: str | None = None,
    max_cost_microusd: int | None = None,
    reconcile_interval_seconds: float = 5.0,
    reconcile_page_size: int = 100,
    shutdown_grace_seconds: float = 35.0,
    start_reconciler: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> ControlRunComposition:
    """Compose provider-neutral lifecycle ports; later clouds use this same function."""
    backend_registry: ExecutionBackendRegistry | None = None
    lifecycle: ControlRunLifecycle | None = None
    try:
        plan_registry = ExecutionPlanRegistry(plans)
        backend_registry = ExecutionBackendRegistry(backends)
        if environment != "auto" and not any(
            plan.environment == environment for plan in plan_registry.plans
        ):
            raise ControlRunCompositionError(
                "The selected Control environment has no active execution plan."
            )
        lifecycle = ControlRunLifecycle(
            store,
            plan_registry,
            backend_registry,
            graph_store,
            clock=clock,
            reconcile_interval_seconds=reconcile_interval_seconds,
            reconcile_page_size=reconcile_page_size,
            shutdown_grace_seconds=shutdown_grace_seconds,
        )
        resolver = PlanRunSubmissionResolver(
            plan_registry,
            environment,
            tuple(placement_candidates),
            preferred_locality,
            max_cost_microusd,
        )
        if start_reconciler:
            lifecycle.start_reconciler()
        return ControlRunComposition(lifecycle=lifecycle, resolver=resolver)
    except (ControlRunCompositionError, OrchestrationContractError, ValueError) as error:
        try:
            if lifecycle is not None:
                lifecycle.close()
            else:
                if backend_registry is not None:
                    backend_registry.close()
                store.close()
        except Exception:  # noqa: BLE001 - preserve the primary construction failure
            pass
        if isinstance(error, ControlRunCompositionError):
            raise
        raise ControlRunCompositionError("The Control run composition is invalid.") from error


def build_multicloud_run_composition(
    *,
    graph_store: GraphStore,
    project_config: Path,
    platforms_config: Path | None = None,
    plan_paths: Sequence[Path],
    run_store_bucket: str,
    run_store_prefix: str,
    environment: str,
    placement_candidates: Iterable[PlacementCandidate] = (),
    preferred_locality: str | None = None,
    max_cost_microusd: int | None = None,
    deployment_name: str = "dander",
    gcp_project_id: str | None = None,
    gcp_deployment_name: str = "gcp_cloud_run",
    reconcile_interval_seconds: float = 5.0,
    shutdown_grace_seconds: float = 35.0,
    trigger_paths: Sequence[Path] = (),
    schedule_queue_url: str | None = None,
) -> ControlRunComposition:
    """Build one AWS-hosted Control composition with Fargate and optional Cloud Run."""
    plans = load_execution_plans(plan_paths)
    if bool(trigger_paths) != (schedule_queue_url is not None):
        raise ControlRunCompositionError(
            "Control trigger specs and schedule queue URL must be configured together."
        )
    triggers = load_trigger_specs(trigger_paths) if trigger_paths else ()
    fargate_bindings: dict[str, FargateBinding] = {}
    cloud_run_bindings: dict[str, CloudRunBinding] = {}
    account_ids: set[str] = set()
    regions: set[str] = set()
    composition: ControlRunComposition | None = None
    try:
        for plan in plans:
            if plan.backend_id == "cloud_run":
                if gcp_project_id is None:
                    raise ControlRunCompositionError(
                        "Cloud Run execution plans require an exact GCP project id."
                    )
                cloud_run_bindings[plan.revision] = CloudRunBinding.from_project(
                    config=project_config,
                    platforms_config=platforms_config,
                    deployment=gcp_deployment_name,
                    pipeline_id=plan.execution_template.pipeline_id,
                    project_id=gcp_project_id,
                )
                continue
            if plan.backend_id != "fargate":
                raise ControlRunCompositionError(
                    "The AWS Control composition accepts only Fargate or Cloud Run plans."
                )
            binding = FargateBinding.from_project(
                config=project_config,
                platforms_config=platforms_config,
                deployment=plan.profile_id,
                pipeline_id=plan.execution_template.pipeline_id,
                name=deployment_name,
            )
            if not binding.schedule_paused:
                raise ControlRunCompositionError(
                    "Direct Fargate schedules must remain paused when Control owns hosted runs."
                )
            fargate_bindings[plan.revision] = binding
            account_ids.add(binding.account_id)
            regions.add(binding.region)
        if len(account_ids) != 1:
            raise ControlRunCompositionError(
                "The AWS-hosted Control composition requires one exact Fargate AWS account."
            )
        owner = next(iter(account_ids))
        store = S3RunStore(
            run_store_bucket,
            prefix=run_store_prefix,
            expected_bucket_owner=owner,
        )
        backends: dict[str, ExecutionBackend] = {
            "fargate": FargateExecutionBackend(fargate_bindings)
        }
        if cloud_run_bindings:
            backends["cloud_run"] = CloudRunExecutionBackend(cloud_run_bindings)
        composition = compose_run_control(
            graph_store=graph_store,
            store=store,
            plans=plans,
            backends=backends,
            environment=environment,
            placement_candidates=placement_candidates,
            preferred_locality=preferred_locality,
            max_cost_microusd=max_cost_microusd,
            reconcile_interval_seconds=reconcile_interval_seconds,
            shutdown_grace_seconds=shutdown_grace_seconds,
            start_reconciler=not triggers,
        )
        if triggers:
            if len(regions) != 1 or schedule_queue_url is None:
                raise ControlRunCompositionError(
                    "Scheduled AWS Control plans must use one exact AWS region."
                )
            queue = SQSScheduleQueue(
                schedule_queue_url,
                expected_account_id=owner,
                expected_region=next(iter(regions)),
            )
            schedule_resolver = ScheduledRunSubmissionResolver(
                composition.resolver.plans,
                graph_store,
                triggers,
            )
            consumer = ControlScheduleConsumer(
                queue,
                schedule_resolver,
                composition.lifecycle,
                shutdown_grace_seconds=shutdown_grace_seconds,
            )
            composition.lifecycle.install_submission_source(consumer)
            composition.lifecycle.start_reconciler()
        return composition
    except (
        ControlOperationError,
        ControlRunCompositionError,
        CloudRunOperationError,
        ExecutionBackendError,
        FargateOperationError,
        OrchestrationContractError,
        RunStoreError,
        ScheduleQueueError,
        ValueError,
    ) as error:
        if composition is not None:
            with suppress(ControlOperationError):
                composition.lifecycle.close()
        if isinstance(error, ControlRunCompositionError):
            raise
        raise ControlRunCompositionError("The AWS Control run binding is invalid.") from error


build_fargate_run_composition = build_multicloud_run_composition


__all__ = [
    "ControlRunComposition",
    "ControlRunCompositionError",
    "build_fargate_run_composition",
    "build_multicloud_run_composition",
    "compose_run_control",
    "load_execution_plans",
    "load_trigger_specs",
]
