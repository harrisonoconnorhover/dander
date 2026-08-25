"""Control orchestration contracts, transition truth, and restart adoption."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest

from dander.control.graph_store import GraphRecord, InMemoryGraphStore
from dander.control.models import PipelineGraphDocument
from dander.control.orchestration import (
    AttemptRecord,
    BackendExecutionState,
    BackendHandle,
    BackendLogPage,
    BackendObservation,
    CleanupState,
    ExecutionPlan,
    HostedRunState,
    OrchestrationContractError,
    ResultsState,
    RetryPolicy,
    RunClaim,
    RunOutcome,
    RunRecord,
    RunStoreConflictError,
    RunStoreIdempotencyConflictError,
    RunSubmission,
    RunTransitionError,
    RunTrigger,
    StoredRun,
    StoredRunPage,
    TriggerKind,
    TriggerSpec,
    attempt_identity,
    create_run_record,
    dispatch_run_attempt,
    transition_run,
    validate_submission_plan,
)
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
)
from dander.runtime_contract import RUNTIME_CONTRACT

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
IMAGE = "registry.example.invalid/dander/runtime@sha256:" + "b" * 64
GRAPH_DOCUMENT = PipelineGraphDocument.model_validate(
    {"name": "hosted_graph", "nodes": [], "edges": []}
)


def _graph() -> GraphRecord:
    return InMemoryGraphStore(clock=lambda: NOW, revision_factory=lambda: "graph-r1").create(
        "demo",
        "hosted-graph",
        GRAPH_DOCUMENT,
        idempotency_key="graph-key-0001",
    )


def _template(*, scheduled: bool = False) -> ExecutionTemplate:
    return ExecutionTemplate(
        schema=EXECUTION_PROJECTION_SCHEMA,
        contract=RUNTIME_CONTRACT,
        pipeline_id="hosted_graph",
        profile_id="aws",
        launcher="fargate",
        image=IMAGE,
        command=(
            "runtime",
            "execute",
            "--contract",
            RUNTIME_CONTRACT,
            "--pipeline",
            "hosted_graph",
            "--platform",
            "aws",
        ),
        configuration_reference="s3://dander-control/config.json",
        environment=(),
        secret_bindings=(),
        workload_identity="arn:aws:iam::123456789012:role/dander-runtime",
        resources=ResourceProjection(
            cpu_millis=1_000,
            memory_mib=2_048,
            ephemeral_storage_mib=21_504,
            deadline_seconds=300,
            runtime_retry_count=0,
            launcher_retry_count=1,
        ),
        schedule=ScheduleProjection(
            task_count=1,
            maximum_parallelism=1,
            expression="0 * * * *" if scheduled else None,
            time_zone="UTC" if scheduled else None,
            paused=False,
        ),
        network=NetworkPlacement(),
        labels=(),
        observability=ObservabilityProjection(
            log_destination="cloudwatch",
            metric_namespace="dander",
            alert_target=None,
            retention_days=30,
        ),
    )


def _plan(graph: GraphRecord | None = None) -> ExecutionPlan:
    selected = graph or _graph()
    return ExecutionPlan(
        plan_id="aws-redshift",
        environment="production",
        project=selected.project,
        graph=selected.graph,
        graph_revision=selected.revision,
        graph_content_sha256=selected.content_sha256,
        backend_id="fargate",
        profile_id="aws",
        image=IMAGE,
        execution_template=_template(),
        deadline_seconds=300,
        retry_policy=RetryPolicy(max_attempts=2),
    )


def _submission(
    graph: GraphRecord | None = None,
    *,
    key: str = "start-key-0001",
    requested_at: datetime = NOW,
    plan_revision: str | None = None,
    trigger: RunTrigger | None = None,
) -> RunSubmission:
    selected = graph or _graph()
    return RunSubmission(
        environment="production",
        project=selected.project,
        graph=selected,
        plan_id="aws-redshift",
        plan_revision=plan_revision or _plan(selected).revision,
        trigger=trigger or RunTrigger(kind=TriggerKind.API, trigger_id="control-api"),
        idempotency_key=key,
        requested_at=requested_at,
        requested_deadline_seconds=240,
    )


@dataclass
class _FakeRunStore:
    runs: dict[str, StoredRun] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str], str] = field(default_factory=dict)
    attempts: dict[str, AttemptRecord] = field(default_factory=dict)
    fail_next_save: bool = False
    _revision: int = 0

    def claim(self, record: RunRecord) -> RunClaim:
        scope = (record.environment, record.project, record.idempotency_key_sha256)
        existing_run_id = self.idempotency.get(scope)
        if existing_run_id is not None:
            return RunClaim(stored=self.runs[existing_run_id], created=False)
        self._revision += 1
        stored = StoredRun(record=record, revision=f"revision-{self._revision}")
        self.runs[record.run_id] = stored
        self.idempotency[scope] = record.run_id
        return RunClaim(stored=stored, created=True)

    def get(self, run_id: str) -> StoredRun | None:
        return self.runs.get(run_id)

    def find_idempotency(
        self,
        *,
        environment: str,
        project: str,
        idempotency_key_sha256: str,
    ) -> StoredRun | None:
        run_id = self.idempotency.get((environment, project, idempotency_key_sha256))
        return self.runs.get(run_id) if run_id is not None else None

    def save(self, stored: StoredRun, record: RunRecord) -> StoredRun:
        if self.fail_next_save:
            self.fail_next_save = False
            raise RunStoreConflictError("simulated crash before handle persistence")
        if self.runs.get(record.run_id) != stored:
            raise RunStoreConflictError("stale run revision")
        self._revision += 1
        updated = StoredRun(record=record, revision=f"revision-{self._revision}")
        self.runs[record.run_id] = updated
        return updated

    def append_attempt(self, attempt: AttemptRecord) -> None:
        existing = self.attempts.setdefault(attempt.attempt_id, attempt)
        if existing != attempt:
            raise RunStoreConflictError("attempt identity was reused with different input")

    def list(self, *, cursor: str | None, limit: int) -> StoredRunPage:
        assert cursor is None
        return StoredRunPage(items=tuple(self.runs.values())[:limit])

    def close(self) -> None:
        return None


@dataclass
class _FakeBackend:
    effects: dict[str, BackendHandle] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    def submit_or_adopt(
        self,
        plan: ExecutionPlan,
        *,
        run_id: str,
        attempt_id: str,
        trigger: RunTrigger,
    ) -> BackendHandle:
        assert plan.backend_id == "fargate"
        assert run_id.startswith("run-")
        assert trigger.kind is TriggerKind.API
        self.requests.append(attempt_id)
        handle = BackendHandle(
            backend_id=plan.backend_id,
            execution_id=f"provider/{attempt_id}",
        )
        return self.effects.setdefault(attempt_id, handle)

    def observe(self, plan: ExecutionPlan, handle: BackendHandle) -> BackendObservation:
        return BackendObservation(
            execution_state=BackendExecutionState.RUNNING,
            outcome=RunOutcome.UNKNOWN,
            results_state=ResultsState.PENDING,
            cleanup_state=CleanupState.PENDING,
            observed_at=NOW,
        )

    def logs(
        self,
        plan: ExecutionPlan,
        handle: BackendHandle,
        *,
        cursor: str | None,
        limit: int,
    ) -> BackendLogPage:
        return BackendLogPage(records=())

    def cancel(self, plan: ExecutionPlan, handle: BackendHandle) -> None:
        return None

    def close(self) -> None:
        return None


def test_execution_plan_and_trigger_spec_keep_schedule_identity_separate() -> None:
    graph = _graph()
    plan = _plan(graph)
    trigger = TriggerSpec(
        trigger_id="hourly",
        kind=TriggerKind.SCHEDULE,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        enabled=True,
        schedule="0 * * * *",
        time_zone="America/New_York",
    )

    changed_schedule = replace(trigger, schedule="15 * * * *")

    assert changed_schedule.plan_revision == plan.revision
    assert plan.execution_template.schedule.expression is None
    validate_submission_plan(_submission(graph), plan)
    with pytest.raises(OrchestrationContractError, match="must not embed schedule"):
        replace(plan, execution_template=_template(scheduled=True))


def test_submission_retry_identity_ignores_request_time_but_replay_is_a_new_run() -> None:
    graph = _graph()
    first = _submission(graph)
    retry = _submission(graph, requested_at=NOW + timedelta(minutes=1))
    replay = _submission(
        graph,
        key="replay-key-0001",
        trigger=RunTrigger(
            kind=TriggerKind.API,
            trigger_id="control-api",
            replay_of_run_id=create_run_record(first).run_id,
        ),
    )

    assert retry.fingerprint == first.fingerprint
    assert create_run_record(retry).run_id == create_run_record(first).run_id
    assert create_run_record(replay).run_id != create_run_record(first).run_id


def test_transition_preserves_independent_outcome_results_and_cleanup_truth() -> None:
    queued = create_run_record(_submission())
    attempt_id = attempt_identity(queued.run_id, 1)
    running = transition_run(
        queued,
        HostedRunState.RUNNING,
        now=NOW + timedelta(seconds=1),
        attempt_count=1,
        current_attempt_id=attempt_id,
        backend_handle=BackendHandle("fargate", "provider/execution-one"),
    )
    terminal = transition_run(
        running,
        HostedRunState.TERMINAL,
        now=NOW + timedelta(seconds=2),
        outcome=RunOutcome.SUCCEEDED,
    )
    partially_reconciled = transition_run(
        terminal,
        HostedRunState.TERMINAL,
        now=NOW + timedelta(seconds=3),
        results_state=ResultsState.AVAILABLE,
        cleanup_state=CleanupState.UNCERTAIN,
    )

    assert partially_reconciled.outcome is RunOutcome.SUCCEEDED
    assert partially_reconciled.results_state is ResultsState.AVAILABLE
    assert partially_reconciled.cleanup_state is CleanupState.UNCERTAIN
    confirmed = transition_run(
        partially_reconciled,
        HostedRunState.TERMINAL,
        now=NOW + timedelta(seconds=4),
        cleanup_state=CleanupState.CONFIRMED,
    )
    assert confirmed.cleanup_state is CleanupState.CONFIRMED
    with pytest.raises(RunTransitionError, match="cannot move"):
        transition_run(confirmed, HostedRunState.RUNNING, now=NOW + timedelta(seconds=5))


def test_dispatch_restart_adopts_provider_effect_after_crash_before_handle_save() -> None:
    graph = _graph()
    submission = _submission(graph)
    plan = _plan(graph)
    store = _FakeRunStore(fail_next_save=True)
    backend = _FakeBackend()

    with pytest.raises(RunStoreConflictError, match="simulated crash"):
        dispatch_run_attempt(store, backend, submission, plan, now=NOW)

    queued = store.get(create_run_record(submission).run_id)
    assert queued is not None
    assert queued.record.run_state is HostedRunState.QUEUED
    recovered = dispatch_run_attempt(
        store,
        backend,
        _submission(graph, requested_at=NOW + timedelta(minutes=1)),
        plan,
        now=NOW + timedelta(minutes=1),
    )

    assert backend.requests[0] == backend.requests[1]
    assert len(backend.effects) == 1
    assert len(store.attempts) == 1
    assert recovered.record.run_state is HostedRunState.RUNNING
    assert recovered.record.backend_handle == next(iter(backend.effects.values()))


def test_idempotency_conflict_and_stale_plan_fail_before_second_provider_effect() -> None:
    graph = _graph()
    store = _FakeRunStore()
    backend = _FakeBackend()
    plan = _plan(graph)
    submission = _submission(graph)
    dispatch_run_attempt(store, backend, submission, plan, now=NOW)

    conflicting = _submission(graph, plan_revision="c" * 64)
    with pytest.raises(OrchestrationContractError, match="exact execution plan"):
        dispatch_run_attempt(store, backend, conflicting, plan, now=NOW)

    changed_graph = replace(graph, content_sha256="d" * 64)
    conflicting_plan = _plan(changed_graph)
    conflicting = _submission(changed_graph, plan_revision=conflicting_plan.revision)
    with pytest.raises(RunStoreIdempotencyConflictError):
        dispatch_run_attempt(store, backend, conflicting, conflicting_plan, now=NOW)
    assert len(backend.effects) == 1
