"""Control lifecycle composition, reconciliation, and restart recovery."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from fastapi.testclient import TestClient

from dander.control.application import (
    ControlApplication,
    ControlOperationConflictError,
    ControlOperationIdempotencyConflictError,
    RunAddress,
)
from dander.control.graph_store import GraphRecord, InMemoryGraphStore
from dander.control.http import create_control_app, encode_revision_etag
from dander.control.models import MutationResult, PipelineGraphDocument, RunState
from dander.control.orchestration import (
    AttemptRecord,
    BackendExecutionState,
    BackendHandle,
    BackendLogPage,
    BackendLogRecord,
    BackendObservation,
    CleanupState,
    ExecutionBackend,
    ExecutionBackendError,
    ExecutionPlan,
    ExecutionResultSummary,
    OrchestrationContractError,
    ResultsState,
    RetryPolicy,
    RunClaim,
    RunOutcome,
    RunRecord,
    RunStore,
    RunStoreConflictError,
    RunStoreIdempotencyConflictError,
    RunSubmission,
    RunTrigger,
    StoredRun,
    StoredRunPage,
    TriggerKind,
    TriggerSpec,
    create_run_record,
)
from dander.control.orchestration_serialization import (
    serialize_execution_plan,
    serialize_trigger_spec,
)
from dander.control.run_composition import (
    ControlRunCompositionError,
    build_fargate_run_composition,
    compose_run_control,
    load_execution_plans,
)
from dander.control.run_lifecycle import (
    ControlRunLifecycle,
    ExecutionBackendRegistry,
    ExecutionPlanRegistry,
    PlanRunSubmissionResolver,
)
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
)
from dander.providers.cloud_run import CloudRunBinding
from dander.providers.fargate import FargateBinding
from dander.runtime_contract import RUNTIME_CONTRACT

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

NOW = datetime(2026, 8, 25, 18, tzinfo=UTC)
IMAGE = "registry.example.invalid/dander/runtime@sha256:" + "b" * 64
DOCUMENT = PipelineGraphDocument.model_validate({"name": "hosted_graph", "nodes": [], "edges": []})


def _graph_store() -> tuple[InMemoryGraphStore, GraphRecord]:
    store = InMemoryGraphStore(clock=lambda: NOW, revision_factory=lambda: "graph-r1")
    record = store.create(
        "demo",
        "hosted-graph",
        DOCUMENT,
        idempotency_key="graph-key-0001",
    )
    return store, record


def _plan(graph: GraphRecord, *, backend_id: str = "fargate") -> ExecutionPlan:
    template = ExecutionTemplate(
        schema=EXECUTION_PROJECTION_SCHEMA,
        contract=RUNTIME_CONTRACT,
        pipeline_id="hosted_graph",
        profile_id="aws",
        launcher=backend_id,
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
        configuration_reference="/app/dander.yaml",
        environment=(),
        secret_bindings=(),
        workload_identity="workload-identity",
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
            expression=None,
            time_zone=None,
            paused=True,
        ),
        network=NetworkPlacement(),
        labels=(),
        observability=ObservabilityProjection(
            log_destination="provider_logs",
            metric_namespace="dander",
            alert_target=None,
            retention_days=30,
        ),
    )
    return ExecutionPlan(
        plan_id="aws-redshift",
        environment="production",
        project=graph.project,
        graph=graph.graph,
        graph_revision=graph.revision,
        graph_content_sha256=graph.content_sha256,
        backend_id=backend_id,
        profile_id="aws",
        image=IMAGE,
        execution_template=template,
        deadline_seconds=300,
        retry_policy=RetryPolicy(max_attempts=2),
    )


def _gcp_plan(graph: GraphRecord) -> ExecutionPlan:
    image = "us-central1-docker.pkg.dev/dander-unit-project/dander/runtime@sha256:" + "d" * 64
    aws = _plan(graph)
    template = replace(
        aws.execution_template,
        profile_id="gcp",
        launcher="cloud_run",
        image=image,
        command=(*aws.execution_template.command[:-1], "gcp"),
        workload_identity=("dander-runtime@dander-unit-project.iam.gserviceaccount.com"),
        resources=replace(
            aws.execution_template.resources,
            memory_mib=512,
            ephemeral_storage_mib=None,
        ),
        observability=replace(
            aws.execution_template.observability,
            log_destination="cloud_logging",
            metric_namespace="run.googleapis.com",
            retention_days=None,
        ),
    )
    return replace(
        aws,
        plan_id="gcp-bigquery",
        environment="gcp",
        backend_id="cloud_run",
        profile_id="gcp",
        image=image,
        execution_template=template,
    )


def _submission(
    graph: GraphRecord,
    plan: ExecutionPlan,
    *,
    key: str = "start-key-0001",
    requested_at: datetime = NOW,
) -> RunSubmission:
    return RunSubmission(
        environment="production",
        project=graph.project,
        graph=graph,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        trigger=RunTrigger(kind=TriggerKind.API, trigger_id="control-api"),
        idempotency_key=key,
        requested_at=requested_at,
    )


@dataclass
class _Store:
    runs: dict[str, StoredRun] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str], str] = field(default_factory=dict)
    attempts: dict[str, AttemptRecord] = field(default_factory=dict)
    mutations: dict[str, tuple[str, str, bytes]] = field(default_factory=dict)
    fail_next_save: bool = False
    close_count: int = 0
    revision: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def claim(self, record: RunRecord) -> RunClaim:
        with self.lock:
            scope = (record.environment, record.project, record.idempotency_key_sha256)
            run_id = self.idempotency.get(scope)
            if run_id is not None:
                stored = self.runs[run_id]
                if stored.record.submission_sha256 != record.submission_sha256:
                    raise RunStoreIdempotencyConflictError("conflicting submission")
                return RunClaim(stored=stored, created=False)
            self.revision += 1
            stored = StoredRun(record=record, revision=f"r{self.revision}")
            self.runs[record.run_id] = stored
            self.idempotency[scope] = record.run_id
            return RunClaim(stored=stored, created=True)

    def get(self, run_id: str) -> StoredRun | None:
        with self.lock:
            return self.runs.get(run_id)

    def find_idempotency(
        self,
        *,
        environment: str,
        project: str,
        idempotency_key_sha256: str,
    ) -> StoredRun | None:
        with self.lock:
            run_id = self.idempotency.get((environment, project, idempotency_key_sha256))
            return self.runs.get(run_id) if run_id is not None else None

    def save(self, stored: StoredRun, record: RunRecord) -> StoredRun:
        with self.lock:
            if self.fail_next_save:
                self.fail_next_save = False
                raise RunStoreConflictError("simulated save loss")
            if self.runs.get(record.run_id) != stored:
                raise RunStoreConflictError("stale snapshot")
            self.revision += 1
            updated = StoredRun(record=record, revision=f"r{self.revision}")
            self.runs[record.run_id] = updated
            return updated

    def append_attempt(self, attempt: AttemptRecord) -> None:
        with self.lock:
            existing = self.attempts.setdefault(attempt.attempt_id, attempt)
            if existing != attempt:
                raise RunStoreConflictError("attempt conflict")

    def claim_mutation(
        self,
        *,
        key_sha256: str,
        operation: str,
        run_id: str,
        result: bytes,
    ) -> bytes:
        with self.lock:
            existing = self.mutations.setdefault(key_sha256, (operation, run_id, result))
            if existing[:2] != (operation, run_id):
                raise RunStoreIdempotencyConflictError("mutation conflict")
            return existing[2]

    def list(self, *, cursor: str | None, limit: int) -> StoredRunPage:
        with self.lock:
            run_ids = sorted(self.runs)
            start = run_ids.index(cursor) + 1 if cursor is not None else 0
            selected = run_ids[start : start + limit]
            has_more = start + len(selected) < len(run_ids)
            return StoredRunPage(
                items=tuple(self.runs[run_id] for run_id in selected),
                next_cursor=selected[-1] if selected and has_more else None,
            )

    def close(self) -> None:
        self.close_count += 1


@dataclass
class _Backend:
    effects: dict[str, BackendHandle] = field(default_factory=dict)
    submissions: list[str] = field(default_factory=list)
    observations: dict[str, BackendObservation] = field(default_factory=dict)
    cancel_calls: list[str] = field(default_factory=list)
    close_count: int = 0
    log_error: bool = False
    submitted: threading.Event = field(default_factory=threading.Event)
    submit_entered: threading.Event = field(default_factory=threading.Event)
    submit_release: threading.Event | None = None

    def submit_or_adopt(
        self,
        plan: ExecutionPlan,
        *,
        run_id: str,
        attempt_id: str,
        trigger: RunTrigger,
    ) -> BackendHandle:
        del trigger
        self.submissions.append(attempt_id)
        self.submit_entered.set()
        if self.submit_release is not None:
            assert self.submit_release.wait(timeout=1)
        handle = self.effects.setdefault(
            attempt_id,
            BackendHandle(plan.backend_id, f"provider:{attempt_id}"),
        )
        self.observations.setdefault(handle.execution_id, _running())
        self.submitted.set()
        return handle

    def observe(self, plan: ExecutionPlan, handle: BackendHandle) -> BackendObservation:
        assert plan.backend_id == handle.backend_id
        return self.observations[handle.execution_id]

    def logs(
        self,
        plan: ExecutionPlan,
        handle: BackendHandle,
        *,
        cursor: str | None,
        limit: int,
    ) -> BackendLogPage:
        assert plan.backend_id == handle.backend_id
        assert cursor is None
        assert limit == 25
        if self.log_error:
            raise ExecutionBackendError("provider-secret-must-not-escape")
        return BackendLogPage(
            records=(BackendLogRecord(NOW, "worker started", level="info"),),
            next_cursor="next-page",
        )

    def cancel(self, plan: ExecutionPlan, handle: BackendHandle) -> None:
        assert plan.backend_id == handle.backend_id
        self.cancel_calls.append(handle.execution_id)
        self.observations[handle.execution_id] = _terminal(RunOutcome.CANCELED)

    def close(self) -> None:
        self.close_count += 1


def _running(*, stage: str = "running") -> BackendObservation:
    return BackendObservation(
        execution_state=BackendExecutionState.RUNNING,
        outcome=RunOutcome.UNKNOWN,
        results_state=ResultsState.PENDING,
        cleanup_state=CleanupState.PENDING,
        observed_at=NOW + timedelta(seconds=10),
        stage=stage,
    )


def _result_summary() -> ExecutionResultSummary:
    return ExecutionResultSummary(
        endpoints=1,
        extracted_rows=3,
        affected_rows=3,
        models=1,
        assertions=3,
        assets=1,
        duration_ms=1_000,
        operation_count=0,
        retry_count=0,
        rows_read=0,
        rows_written=0,
        rows_affected=0,
        bytes_read=0,
        bytes_written=0,
        bytes_processed=0,
        bytes_billed=0,
        queue_duration_ms=0,
        execution_duration_ms=0,
        spill_bytes=0,
    )


def _terminal(
    outcome: RunOutcome,
    *,
    cleanup: CleanupState = CleanupState.CONFIRMED,
) -> BackendObservation:
    return BackendObservation(
        execution_state=BackendExecutionState.TERMINAL,
        outcome=outcome,
        results_state=(
            ResultsState.AVAILABLE if outcome is RunOutcome.SUCCEEDED else ResultsState.UNAVAILABLE
        ),
        cleanup_state=cleanup,
        observed_at=NOW + timedelta(seconds=20),
        stage=outcome.value,
        failure_code="provider_failed" if outcome is RunOutcome.FAILED else None,
        result_summary=_result_summary() if outcome is RunOutcome.SUCCEEDED else None,
    )


def _lifecycle(
    graph_store: InMemoryGraphStore,
    plan: ExecutionPlan,
    store: _Store,
    backend: _Backend,
    *,
    clock: Callable[[], datetime] | None = None,
) -> ControlRunLifecycle:
    return ControlRunLifecycle(
        cast("RunStore", store),
        ExecutionPlanRegistry((plan,)),
        ExecutionBackendRegistry({plan.backend_id: cast("ExecutionBackend", backend)}),
        graph_store,
        clock=clock or (lambda: NOW + timedelta(seconds=1)),
        reconcile_interval_seconds=0.01,
        shutdown_grace_seconds=1,
    )


def test_start_list_logs_and_terminal_reconciliation_are_provider_neutral() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph)
    store = _Store()
    backend = _Backend()
    lifecycle = _lifecycle(graph_store, plan, store, backend)

    started = lifecycle.start(_submission(graph, plan))
    repeated = lifecycle.start(_submission(graph, plan, requested_at=NOW + timedelta(minutes=1)))
    assert started == repeated
    assert started.state is RunState.RUNNING
    assert started.can_cancel is True
    assert len(backend.effects) == 1
    assert len(store.attempts) == 1

    logs = lifecycle.logs(RunAddress(started.run_id), cursor=None, limit=25)
    assert logs.records[0].message == "worker started"
    assert logs.records[0].correlation_id == started.run_id
    assert logs.next_cursor == "next-page"

    handle = next(iter(backend.effects.values()))
    backend.observations[handle.execution_id] = _terminal(
        RunOutcome.SUCCEEDED,
        cleanup=CleanupState.UNCERTAIN,
    )
    assert lifecycle.reconcile_once() == 1
    terminal = lifecycle.get(RunAddress(started.run_id))
    assert terminal.state is RunState.SUCCEEDED
    assert terminal.can_replay is True
    assert terminal.endpoints == 1
    assert terminal.extracted == 3
    assert terminal.telemetry is not None
    assert terminal.telemetry.duration_ms == 1_000
    durable = store.get(started.run_id)
    assert durable is not None
    assert durable.record.results_state is ResultsState.AVAILABLE
    assert durable.record.result_summary == _result_summary()
    assert durable.record.cleanup_state is CleanupState.UNCERTAIN

    restarted = _lifecycle(graph_store, plan, store, backend)
    recovered_status = restarted.get(RunAddress(started.run_id))
    assert recovered_status.extracted == 3
    assert recovered_status.telemetry == terminal.telemetry

    backend.observations[handle.execution_id] = _terminal(RunOutcome.SUCCEEDED)
    lifecycle.reconcile_once()
    confirmed = store.get(started.run_id)
    assert confirmed is not None
    assert confirmed.record.cleanup_state is CleanupState.CONFIRMED


def test_restart_recovers_queued_save_after_submit_gap_by_adopting_same_effect() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph)
    store = _Store(fail_next_save=True)
    backend = _Backend()
    first = _lifecycle(graph_store, plan, store, backend)

    queued = first.start(_submission(graph, plan))
    assert queued.state is RunState.QUEUED
    assert len(backend.effects) == 1

    restarted = _lifecycle(graph_store, plan, store, backend)
    assert restarted.reconcile_once() == 1
    recovered = restarted.get(RunAddress(queued.run_id))
    assert recovered.state is RunState.RUNNING
    assert backend.submissions[0] == backend.submissions[1]
    assert len(backend.effects) == 1
    assert len(store.attempts) == 1


def test_background_recovery_controls_readiness_and_graceful_shutdown() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph)
    store = _Store()
    backend = _Backend()
    store.claim(create_run_record(_submission(graph, plan)))
    composition = compose_run_control(
        graph_store=graph_store,
        store=cast("RunStore", store),
        plans=(plan,),
        backends={"fargate": cast("ExecutionBackend", backend)},
        environment="production",
        reconcile_interval_seconds=0.01,
        shutdown_grace_seconds=1,
        clock=lambda: NOW + timedelta(seconds=1),
    )

    assert backend.submitted.wait(timeout=1)
    for _ in range(100):
        if composition.lifecycle.ready():
            break
        threading.Event().wait(0.005)
    assert composition.lifecycle.ready() is True

    composition.lifecycle.close()
    assert composition.lifecycle.ready() is False
    assert backend.close_count == 1
    assert store.close_count == 1


def test_background_submission_source_participates_in_readiness_and_shutdown() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph)
    store = _Store()
    backend = _Backend()

    class _Source:
        started = False
        healthy = False
        closed = False

        def start(self) -> None:
            self.started = True

        def ready(self) -> bool:
            return self.healthy

        def close(self) -> None:
            self.closed = True

    source = _Source()
    composition = compose_run_control(
        graph_store=graph_store,
        store=cast("RunStore", store),
        plans=(plan,),
        backends={"fargate": cast("ExecutionBackend", backend)},
        environment="production",
        reconcile_interval_seconds=0.01,
        shutdown_grace_seconds=1,
        start_reconciler=False,
    )
    composition.lifecycle.install_submission_source(source)
    composition.lifecycle.start_reconciler()

    for _ in range(100):
        if source.started and composition.lifecycle.reconcile_once() == 0:
            break
        threading.Event().wait(0.005)
    assert composition.lifecycle.ready() is False
    source.healthy = True
    assert composition.lifecycle.ready() is True

    composition.lifecycle.close()
    assert source.closed is True


def test_cancel_is_idempotent_and_reconciliation_records_canceled_truth() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph)
    store = _Store()
    backend = _Backend()
    lifecycle = _lifecycle(graph_store, plan, store, backend)
    first = lifecycle.start(_submission(graph, plan))
    second = lifecycle.start(_submission(graph, plan, key="start-key-0002"))

    canceled = lifecycle.cancel(RunAddress(first.run_id), idempotency_key="cancel-key-0001")
    assert canceled.state is RunState.CANCELING
    restarted = _lifecycle(graph_store, plan, store, backend)
    assert restarted.cancel(RunAddress(first.run_id), idempotency_key="cancel-key-0001") == canceled
    with pytest.raises(ControlOperationIdempotencyConflictError):
        restarted.cancel(RunAddress(second.run_id), idempotency_key="cancel-key-0001")

    restarted.reconcile_once()
    assert restarted.get(RunAddress(first.run_id)).state is RunState.CANCELED
    assert len(backend.cancel_calls) >= 1


def test_restart_finishes_cancellation_claimed_before_the_provider_effect() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph)
    store = _Store()
    backend = _Backend()
    first = _lifecycle(graph_store, plan, store, backend)
    started = first.start(_submission(graph, plan))
    original = (
        MutationResult(
            operation="cancel",
            accepted=True,
            run_id=started.run_id,
            state=RunState.CANCELING,
        )
        .model_dump_json()
        .encode()
    )
    store.claim_mutation(
        key_sha256=hashlib.sha256(b"cancel-key-0001").hexdigest(),
        operation="cancel",
        run_id=started.run_id,
        result=original,
    )

    restarted = _lifecycle(graph_store, plan, store, backend)
    result = restarted.cancel(
        RunAddress(started.run_id),
        idempotency_key="cancel-key-0001",
    )

    assert result.state is RunState.CANCELING
    assert restarted.get(RunAddress(started.run_id)).state is RunState.CANCELING
    assert len(backend.cancel_calls) == 1


def test_single_process_mutation_lock_prevents_queued_cancel_from_orphaning_dispatch() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph)
    store = _Store()
    backend = _Backend(submit_release=threading.Event())
    lifecycle = _lifecycle(graph_store, plan, store, backend)
    queued = create_run_record(_submission(graph, plan))
    store.claim(queued)
    cancel_started = threading.Event()
    cancel_finished = threading.Event()

    reconcile_thread = threading.Thread(target=lifecycle.reconcile_once)
    reconcile_thread.start()
    assert backend.submit_entered.wait(timeout=1)

    def cancel() -> None:
        cancel_started.set()
        lifecycle.cancel(RunAddress(queued.run_id), idempotency_key="cancel-key-0001")
        cancel_finished.set()

    cancel_thread = threading.Thread(target=cancel)
    cancel_thread.start()
    assert cancel_started.wait(timeout=1)
    assert cancel_finished.wait(timeout=0.02) is False
    assert backend.submit_release is not None
    backend.submit_release.set()
    reconcile_thread.join(timeout=1)
    cancel_thread.join(timeout=1)

    assert cancel_finished.is_set()
    assert len(backend.effects) == 1
    assert len(backend.cancel_calls) == 1
    assert lifecycle.get(RunAddress(queued.run_id)).state is RunState.CANCELING


def test_replay_creates_a_new_durable_run_and_rejects_graph_drift() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph)
    store = _Store()
    backend = _Backend()
    lifecycle = _lifecycle(graph_store, plan, store, backend)
    source = lifecycle.start(_submission(graph, plan))
    handle = next(iter(backend.effects.values()))
    backend.observations[handle.execution_id] = _terminal(RunOutcome.FAILED)
    lifecycle.reconcile_once()

    replayed = lifecycle.replay(RunAddress(source.run_id), idempotency_key="replay-key-0001")
    repeated = lifecycle.replay(RunAddress(source.run_id), idempotency_key="replay-key-0001")
    assert replayed == repeated
    assert replayed.resulting_run_id is not None
    assert replayed.resulting_run_id != source.run_id
    assert replayed.state is RunState.RUNNING

    current = graph_store.get(graph.project, graph.graph)
    graph_store.put(
        graph.project,
        graph.graph,
        DOCUMENT.model_copy(update={"name": "changed_graph"}),
        expected_revision=current.revision,
    )
    with pytest.raises(ControlOperationConflictError, match="no longer current"):
        lifecycle.replay(RunAddress(source.run_id), idempotency_key="replay-key-0002")


def test_same_lifecycle_interface_selects_a_non_aws_backend_without_pipeline_changes() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph, backend_id="gcp")
    store = _Store()
    backend = _Backend()
    composition = compose_run_control(
        graph_store=graph_store,
        store=cast("RunStore", store),
        plans=(plan,),
        backends={"gcp": cast("ExecutionBackend", backend)},
        environment="production",
        start_reconciler=False,
        clock=lambda: NOW + timedelta(seconds=1),
    )
    submission = composition.resolver.resolve(
        graph,
        idempotency_key="start-key-0001",
        requested_at=NOW,
    )

    status = composition.lifecycle.start(submission)

    assert status.state is RunState.RUNNING
    assert next(iter(backend.effects.values())).backend_id == "gcp"


def test_plan_registry_rejects_stale_graphs_and_multiple_active_revisions() -> None:
    _store, graph = _graph_store()
    plan = _plan(graph)
    registry = ExecutionPlanRegistry((plan,))
    stale = replace(graph, content_sha256="c" * 64)
    with pytest.raises(ControlOperationConflictError, match="current graph"):
        registry.select(stale, environment="production")

    changed_image = "registry.example.invalid/dander/runtime@sha256:" + "d" * 64
    changed_plan = replace(
        plan,
        image=changed_image,
        execution_template=replace(plan.execution_template, image=changed_image),
    )
    with pytest.raises(OrchestrationContractError, match="more than one active plan"):
        ExecutionPlanRegistry((plan, changed_plan))


def test_compatibility_resolver_selects_the_exact_active_plan() -> None:
    _store, graph = _graph_store()
    plan = _plan(graph)
    resolver = PlanRunSubmissionResolver(ExecutionPlanRegistry((plan,)), "production")

    submission = resolver.resolve(
        graph,
        idempotency_key="start-key-0001",
        requested_at=NOW,
    )

    assert submission.plan_revision == plan.revision
    assert submission.trigger.kind is TriggerKind.API


def test_plan_files_must_be_canonical_and_content_addressed(tmp_path: Path) -> None:
    _store, graph = _graph_store()
    plan = _plan(graph)
    plan_path = tmp_path / "plan.json"
    plan_path.write_bytes(serialize_execution_plan(plan))

    assert load_execution_plans((plan_path,)) == (plan,)

    plan_path.write_bytes(serialize_execution_plan(plan).replace(plan.revision.encode(), b"0" * 64))
    with pytest.raises(ControlRunCompositionError, match="unavailable or invalid"):
        load_execution_plans((plan_path,))


def test_composed_lifecycle_serves_existing_control_run_routes() -> None:
    graph_store, graph = _graph_store()
    plan = _plan(graph)
    store = _Store()
    backend = _Backend()
    lifecycle = _lifecycle(
        graph_store,
        plan,
        store,
        backend,
        clock=lambda: datetime.now(UTC) + timedelta(seconds=1),
    )
    resolver = PlanRunSubmissionResolver(ExecutionPlanRegistry((plan,)), "production")
    application = ControlApplication(
        graph_store,
        lifecycle=lifecycle,
        submission_resolver=resolver,
        projects=("demo",),
    )

    with TestClient(create_control_app(application)) as client:
        response = client.post(
            "/v1/projects/demo/graphs/hosted-graph/runs",
            headers={
                "If-Match": encode_revision_etag(graph.revision),
                "Idempotency-Key": "start-key-0001",
            },
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        assert response.json()["state"] == "running"
        assert client.get("/v1/runs").json()["items"][0]["run_id"] == run_id
        logs = client.get(f"/v1/runs/{run_id}/logs?limit=25")
        assert logs.status_code == 200
        assert logs.json()["records"][0]["message"] == "worker started"
        backend.log_error = True
        unavailable = client.get(f"/v1/runs/{run_id}/logs?limit=25")
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["code"] == "operation_temporarily_unavailable"
        assert "provider-secret" not in unavailable.text


def test_aws_hosted_composition_registers_fargate_and_cloud_run_plans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dander.control.run_composition as composition_module

    graph_store, graph = _graph_store()
    aws_plan = _plan(graph)
    gcp_plan = _gcp_plan(graph)
    aws_path = tmp_path / "aws-plan.json"
    gcp_path = tmp_path / "gcp-plan.json"
    aws_path.write_bytes(serialize_execution_plan(aws_plan))
    gcp_path.write_bytes(serialize_execution_plan(gcp_plan))
    fargate_binding = FargateBinding(
        account_id="123456789012",
        region="us-east-1",
        deployment_name="aws",
        pipeline_id="hosted_graph",
        resource_name="dander-hosted-graph-12345678",
        state_machine_arn=(
            "arn:aws:states:us-east-1:123456789012:stateMachine:dander-hosted-graph-12345678"
        ),
        cluster_name="dander",
        log_group_name="/dander/dander/hosted_graph",
        schedule_paused=True,
        project_dir=tmp_path,
    )
    cloud_run_binding = CloudRunBinding(
        project_id="dander-unit-project",
        region="us-central1",
        deployment_name="gcp_cloud_run",
        profile_id="gcp",
        pipeline_id="hosted_graph",
        job_name="dander-hosted-graph",
        runtime_service_account=("dander-runtime@dander-unit-project.iam.gserviceaccount.com"),
    )
    fargate_backend = _Backend()
    cloud_run_backend = _Backend()
    cloud_binding_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        FargateBinding,
        "from_project",
        classmethod(lambda _cls, /, **_kwargs: fargate_binding),
    )

    def bind_cloud(_cls: object, /, **kwargs: object) -> CloudRunBinding:
        cloud_binding_calls.append(kwargs)
        return cloud_run_binding

    monkeypatch.setattr(CloudRunBinding, "from_project", classmethod(bind_cloud))
    monkeypatch.setattr(composition_module, "S3RunStore", lambda *_args, **_kwargs: _Store())
    monkeypatch.setattr(
        composition_module,
        "FargateExecutionBackend",
        lambda bindings: (
            fargate_backend if bindings == {aws_plan.revision: fargate_binding} else None
        ),
    )
    monkeypatch.setattr(
        composition_module,
        "CloudRunExecutionBackend",
        lambda bindings: (
            cloud_run_backend if bindings == {gcp_plan.revision: cloud_run_binding} else None
        ),
    )

    composition = build_fargate_run_composition(
        graph_store=graph_store,
        project_config=tmp_path / "dander.yaml",
        platforms_config=tmp_path / "dander.platforms.yaml",
        plan_paths=(aws_path, gcp_path),
        run_store_bucket="dander-control-runs",
        run_store_prefix="control/runs/v1",
        environment="production",
        gcp_project_id="dander-unit-project",
        reconcile_interval_seconds=0.01,
        shutdown_grace_seconds=1,
    )

    selected = composition.resolver.resolve(
        graph,
        idempotency_key="gcp-start-key-0001",
        requested_at=NOW,
        environment="gcp",
    )
    assert selected.plan_revision == gcp_plan.revision
    assert cloud_binding_calls == [
        {
            "config": tmp_path / "dander.yaml",
            "platforms_config": tmp_path / "dander.platforms.yaml",
            "deployment": "gcp_cloud_run",
            "pipeline_id": "hosted_graph",
            "project_id": "dander-unit-project",
        }
    ]
    composition.lifecycle.close()
    assert fargate_backend.close_count == 1
    assert cloud_run_backend.close_count == 1


def test_fargate_startup_binds_canonical_plans_to_existing_aws_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dander.control.run_composition as composition_module

    graph_store, graph = _graph_store()
    plan = _plan(graph)
    plan_path = tmp_path / "plan.json"
    plan_path.write_bytes(serialize_execution_plan(plan))
    binding = FargateBinding(
        account_id="123456789012",
        region="us-east-1",
        deployment_name="aws",
        pipeline_id="hosted_graph",
        resource_name="dander-hosted-graph-12345678",
        state_machine_arn=(
            "arn:aws:states:us-east-1:123456789012:stateMachine:dander-hosted-graph-12345678"
        ),
        cluster_name="dander",
        log_group_name="/dander/dander/hosted_graph",
        schedule_paused=True,
        project_dir=tmp_path,
    )
    binding_calls: list[dict[str, object]] = []
    store_calls: list[dict[str, object]] = []
    backend_calls: list[dict[str, FargateBinding]] = []
    fake_store = _Store()
    fake_backend = _Backend()

    def bind(_cls: object, /, **kwargs: object) -> FargateBinding:
        binding_calls.append(kwargs)
        return binding

    def make_store(bucket: str, **kwargs: object) -> _Store:
        store_calls.append({"bucket": bucket, **kwargs})
        return fake_store

    def make_backend(bindings: dict[str, FargateBinding]) -> _Backend:
        backend_calls.append(bindings)
        return fake_backend

    monkeypatch.setattr(FargateBinding, "from_project", classmethod(bind))
    monkeypatch.setattr(composition_module, "S3RunStore", make_store)
    monkeypatch.setattr(composition_module, "FargateExecutionBackend", make_backend)

    composition = build_fargate_run_composition(
        graph_store=graph_store,
        project_config=tmp_path / "dander.yaml",
        platforms_config=tmp_path / "dander.platforms.yaml",
        plan_paths=(plan_path,),
        run_store_bucket="dander-control-runs",
        run_store_prefix="control/runs/v1",
        environment="production",
        reconcile_interval_seconds=0.01,
        shutdown_grace_seconds=1,
    )

    assert binding_calls == [
        {
            "config": tmp_path / "dander.yaml",
            "platforms_config": tmp_path / "dander.platforms.yaml",
            "deployment": "aws",
            "pipeline_id": "hosted_graph",
            "name": "dander",
        }
    ]
    assert store_calls == [
        {
            "bucket": "dander-control-runs",
            "prefix": "control/runs/v1",
            "expected_bucket_owner": "123456789012",
        }
    ]
    assert backend_calls == [{plan.revision: binding}]
    composition.lifecycle.close()

    trigger = TriggerSpec(
        trigger_id="daily-redshift",
        kind=TriggerKind.SCHEDULE,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        enabled=True,
        schedule="rate(1 day)",
        time_zone="UTC",
    )
    trigger_path = tmp_path / "trigger.json"
    trigger_path.write_bytes(serialize_trigger_spec(trigger))
    queue_calls: list[dict[str, object]] = []
    consumer_state: dict[str, bool] = {}

    class _Queue:
        def __init__(self, queue_url: str, **kwargs: object) -> None:
            queue_calls.append({"queue_url": queue_url, **kwargs})

    class _Consumer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            consumer_state["started"] = True

        def ready(self) -> bool:
            return True

        def close(self) -> None:
            consumer_state["closed"] = True

    monkeypatch.setattr(composition_module, "SQSScheduleQueue", _Queue)
    monkeypatch.setattr(composition_module, "ControlScheduleConsumer", _Consumer)
    scheduled = build_fargate_run_composition(
        graph_store=graph_store,
        project_config=tmp_path / "dander.yaml",
        platforms_config=tmp_path / "dander.platforms.yaml",
        plan_paths=(plan_path,),
        run_store_bucket="dander-control-runs",
        run_store_prefix="control/runs/v1",
        environment="production",
        reconcile_interval_seconds=0.01,
        shutdown_grace_seconds=1,
        trigger_paths=(trigger_path,),
        schedule_queue_url=(
            "https://sqs.us-east-1.amazonaws.com/123456789012/dander-control-schedules"
        ),
    )
    assert queue_calls == [
        {
            "queue_url": (
                "https://sqs.us-east-1.amazonaws.com/123456789012/dander-control-schedules"
            ),
            "expected_account_id": "123456789012",
            "expected_region": "us-east-1",
        }
    ]
    assert consumer_state["started"] is True
    scheduled.lifecycle.close()
    assert consumer_state["closed"] is True

    monkeypatch.setattr(
        FargateBinding,
        "from_project",
        classmethod(lambda _cls, /, **_kwargs: replace(binding, schedule_paused=False)),
    )
    with pytest.raises(ControlRunCompositionError, match="schedules must remain paused"):
        build_fargate_run_composition(
            graph_store=graph_store,
            project_config=tmp_path / "dander.yaml",
            plan_paths=(plan_path,),
            run_store_bucket="dander-control-runs",
            run_store_prefix="control/runs/v1",
            environment="production",
        )
