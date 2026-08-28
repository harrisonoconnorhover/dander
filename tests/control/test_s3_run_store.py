"""Canonical orchestration serialization and durable S3 RunStore behavior."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from dander.control.graph_store import GraphRecord, InMemoryGraphStore
from dander.control.models import PipelineGraphDocument
from dander.control.orchestration import (
    AttemptRecord,
    CleanupState,
    ExecutionPlan,
    ExecutionResultSummary,
    HostedRunState,
    OrchestrationContractError,
    PlacementDecision,
    PlacementMode,
    ResultsState,
    RetryPolicy,
    RunOutcome,
    RunRecord,
    RunStoreConflictError,
    RunStoreIdempotencyConflictError,
    RunSubmission,
    RunTrigger,
    SizeClassDecision,
    SizeClassMode,
    TriggerKind,
    attempt_identity,
    create_run_record,
    transition_run,
)
from dander.control.orchestration_serialization import (
    OrchestrationSerializationError,
    deserialize_attempt_record,
    deserialize_execution_plan,
    deserialize_execution_result_summary,
    deserialize_run_record,
    serialize_attempt_record,
    serialize_execution_plan,
    serialize_execution_result_summary,
    serialize_run_record,
)
from dander.control.s3_run_store import S3RunStore
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
)
from dander.physical_plan import (
    PhysicalExecutionMode,
    PhysicalPlan,
    PhysicalStage,
    fused_container_physical_plan,
    serialize_physical_plan,
)
from dander.runtime_contract import RUNTIME_CONTRACT
from tests.control.s3_fakes import FakeS3Backend, FakeS3Client

NOW = datetime(2026, 8, 25, 16, tzinfo=UTC)
IMAGE = "registry.example.invalid/dander/runtime@sha256:" + "b" * 64


def _result_summary() -> ExecutionResultSummary:
    return ExecutionResultSummary(
        endpoints=1,
        extracted_rows=3,
        affected_rows=3,
        models=1,
        assertions=3,
        assets=1,
        duration_ms=1_000,
        operation_count=1,
        retry_count=0,
        rows_read=3,
        rows_written=3,
        rows_affected=3,
        bytes_read=30,
        bytes_written=30,
        bytes_processed=30,
        bytes_billed=0,
        queue_duration_ms=0,
        execution_duration_ms=10,
        spill_bytes=0,
    )


def _graph() -> GraphRecord:
    document = PipelineGraphDocument.model_validate(
        {"name": "hosted_graph", "nodes": [], "edges": []}
    )
    return InMemoryGraphStore(clock=lambda: NOW, revision_factory=lambda: "graph-r1").create(
        "demo",
        "hosted-graph",
        document,
        idempotency_key="graph-key-0001",
    )


def _template() -> ExecutionTemplate:
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
        environment=(("DANDER_LOG_LEVEL", "INFO"),),
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
            expression=None,
            time_zone=None,
            paused=False,
        ),
        network=NetworkPlacement(),
        labels=(("managed-by", "dander"),),
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


def _physical_plan(graph: GraphRecord | None = None) -> ExecutionPlan:
    selected = graph or _graph()
    physical = fused_container_physical_plan("hosted_graph")
    template = _template()
    template = replace(
        template,
        command=(
            *template.command,
            "--physical-plan",
            serialize_physical_plan(physical).decode("utf-8"),
        ),
    )
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
        execution_template=template,
        deadline_seconds=300,
        retry_policy=RetryPolicy(max_attempts=2),
        physical_plan=physical,
    )


def _submission(
    *,
    key: str = "start-key-0001",
    requested_at: datetime = NOW,
    plan_revision: str | None = None,
) -> RunSubmission:
    graph = _graph()
    return RunSubmission(
        environment="production",
        project=graph.project,
        graph=graph,
        plan_id="aws-redshift",
        plan_revision=plan_revision or _plan(graph).revision,
        trigger=RunTrigger(kind=TriggerKind.API, trigger_id="control-api"),
        placement_decision=PlacementDecision(
            mode=PlacementMode.CONFIGURED_DEFAULT,
            selected_environment="production",
            selected_locality=None,
            estimated_cost_microusd=None,
            preferred_locality=None,
            max_cost_microusd=None,
            eligible_plan_count=1,
        ),
        size_class_decision=SizeClassDecision(
            mode=SizeClassMode.CONFIGURED_DEFAULT,
            selected_size_class="small",
            estimated_input_bytes=None,
            max_input_bytes=1_000,
            cpu_millis=1_000,
            memory_mib=2_048,
            ephemeral_storage_mib=21_504,
            eligible_plan_count=1,
        ),
        idempotency_key=key,
        requested_at=requested_at,
        requested_deadline_seconds=240,
    )


def _attempt(record: RunRecord) -> AttemptRecord:
    run_id = record.run_id
    return AttemptRecord(
        run_id=run_id,
        attempt_id=attempt_identity(run_id, 1),
        attempt_number=1,
        plan_id=record.plan_id,
        plan_revision=record.plan_revision,
        backend_id="fargate",
        trigger=record.trigger,
        created_at=record.created_at,
    )


def _store(backend: FakeS3Backend | None = None) -> S3RunStore:
    return S3RunStore("unit-bucket", client=FakeS3Client(backend))


def test_versioned_records_round_trip_as_canonical_bytes() -> None:
    plan = _plan()
    run = create_run_record(_submission(plan_revision=plan.revision))
    attempt = _attempt(run)

    assert deserialize_execution_plan(serialize_execution_plan(plan)) == plan
    assert deserialize_run_record(serialize_run_record(run)) == run
    assert deserialize_attempt_record(serialize_attempt_record(attempt)) == attempt
    assert (
        deserialize_execution_result_summary(serialize_execution_result_summary(_result_summary()))
        == _result_summary()
    )
    assert json.loads(serialize_execution_plan(plan))["schema"].endswith("/v1")
    assert json.loads(serialize_run_record(run))["schema"].endswith("/v5")
    assert json.loads(serialize_attempt_record(attempt))["schema"].endswith("/v1")


def test_execution_plan_v2_binds_exact_physical_plan_to_container_command() -> None:
    plan = _physical_plan()
    encoded = serialize_execution_plan(plan)
    envelope = json.loads(encoded)

    assert deserialize_execution_plan(encoded) == plan
    assert plan.physical_plan is not None
    assert envelope["schema"] == "io.dander.control.execution-plan/v2"
    assert envelope["plan"]["physical_plan"]["revision"] == plan.physical_plan.revision
    assert plan.execution_template.command[-2] == "--physical-plan"


def test_execution_plan_rejects_physical_plan_command_drift() -> None:
    plan = _physical_plan()
    template = replace(plan.execution_template, command=plan.execution_template.command[:-2])

    with pytest.raises(OrchestrationContractError, match="exact canonical physical plan"):
        replace(plan, execution_template=template)


def test_existing_control_backends_reject_distributed_physical_execution() -> None:
    plan = _physical_plan()
    distributed = PhysicalPlan(
        pipeline_id="hosted_graph",
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        stages=(
            PhysicalStage(
                stage_id="pipeline",
                operators=("hosted_graph",),
                partition_count=2,
            ),
        ),
        exchanges=(),
        maximum_parallelism=2,
    )
    template = replace(
        plan.execution_template,
        command=(
            *plan.execution_template.command[:-2],
            "--physical-plan",
            serialize_physical_plan(distributed).decode("utf-8"),
        ),
    )

    with pytest.raises(OrchestrationContractError, match="require fused"):
        replace(plan, execution_template=template, physical_plan=distributed)


def test_v1_run_snapshot_and_idempotency_claim_recover_without_invented_results() -> None:
    backend = FakeS3Backend()
    candidate = create_run_record(_submission())
    store = _store(backend)
    claimed = store.claim(candidate).stored
    terminal = transition_run(
        claimed.record,
        HostedRunState.TERMINAL,
        now=NOW + timedelta(seconds=1),
        outcome=RunOutcome.SUCCEEDED,
        results_state=ResultsState.AVAILABLE,
        cleanup_state=CleanupState.CONFIRMED,
        result_summary=_result_summary(),
    )
    store.save(claimed, terminal)

    for item in backend.objects.values():
        payload = json.loads(item.data)
        run_envelope = payload.get("initial_record", payload)
        if not isinstance(run_envelope, dict) or "record" not in run_envelope:
            continue
        run_envelope["schema"] = "io.dander.control.run-record/v1"
        record = run_envelope["record"]
        assert isinstance(record, dict)
        record.pop("result_summary")
        record.pop("placement_decision")
        record.pop("size_class_decision")
        item.data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    restarted = _store(backend)
    recovered = restarted.find_idempotency(
        environment=candidate.environment,
        project=candidate.project,
        idempotency_key_sha256=candidate.idempotency_key_sha256,
    )

    assert recovered is not None
    assert recovered.record.outcome is RunOutcome.SUCCEEDED
    assert recovered.record.results_state is ResultsState.AVAILABLE
    assert recovered.record.result_summary is None
    assert recovered.record.placement_decision is None
    assert recovered.record.size_class_decision is None


def test_v2_run_snapshot_recovers_results_without_invented_placement() -> None:
    record = replace(
        create_run_record(_submission()),
        run_state=HostedRunState.TERMINAL,
        outcome=RunOutcome.SUCCEEDED,
        results_state=ResultsState.AVAILABLE,
        cleanup_state=CleanupState.CONFIRMED,
        terminal_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
        result_summary=_result_summary(),
    )
    envelope = json.loads(serialize_run_record(record))
    envelope["schema"] = "io.dander.control.run-record/v2"
    envelope["record"].pop("placement_decision")
    envelope["record"].pop("size_class_decision")
    recovered = deserialize_run_record(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    )

    assert recovered.result_summary == _result_summary()
    assert recovered.placement_decision is None
    assert recovered.size_class_decision is None


def test_v3_run_snapshot_recovers_placement_without_invented_sizing() -> None:
    record = create_run_record(_submission())
    envelope = json.loads(serialize_run_record(record))
    envelope["schema"] = "io.dander.control.run-record/v3"
    envelope["record"].pop("size_class_decision")

    recovered = deserialize_run_record(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    )

    assert recovered.placement_decision == record.placement_decision
    assert recovered.size_class_decision is None


def test_v4_run_snapshot_recovers_legacy_sizing_without_invented_provenance() -> None:
    record = create_run_record(_submission())
    assert record.size_class_decision is not None
    record = replace(
        record,
        size_class_decision=replace(
            record.size_class_decision,
            mode=SizeClassMode.AUTOMATIC_INPUT,
            estimated_input_bytes=500,
            estimate_source="api_request",
            estimate_observed_at=NOW,
        ),
    )
    envelope = json.loads(serialize_run_record(record))
    envelope["schema"] = "io.dander.control.run-record/v4"
    sizing = envelope["record"]["size_class_decision"]
    sizing["schema"] = "io.dander.control.size-class-decision/v1"
    sizing.pop("estimate_source")
    sizing.pop("estimate_observed_at")

    recovered = deserialize_run_record(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    )

    assert recovered.size_class_decision is not None
    assert recovered.size_class_decision.selected_size_class == "small"
    assert recovered.size_class_decision.estimated_input_bytes == 500
    assert recovered.size_class_decision.estimate_source is None
    assert recovered.size_class_decision.estimate_observed_at is None
    assert json.loads(serialize_run_record(recovered))["schema"].endswith("/v5")


def test_plan_revision_is_computed_and_tampering_is_rejected() -> None:
    plan = _plan()
    changed = replace(plan, graph_content_sha256="c" * 64)
    assert changed.revision != plan.revision

    envelope = json.loads(serialize_execution_plan(plan))
    envelope["revision"] = "a" * 64
    tampered = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(OrchestrationSerializationError, match="does not match"):
        deserialize_execution_plan(tampered)


def test_claim_is_durable_idempotent_and_rejects_different_submission() -> None:
    store = _store()
    first = create_run_record(_submission())
    claimed = store.claim(first)
    replay = store.claim(create_run_record(_submission(requested_at=NOW + timedelta(minutes=1))))

    assert claimed.created is True
    assert replay.created is False
    assert replay.stored == claimed.stored
    with pytest.raises(RunStoreIdempotencyConflictError):
        store.claim(create_run_record(_submission(plan_revision="c" * 64)))


def test_mutation_claim_replays_original_result_across_restart_and_rejects_reuse() -> None:
    backend = FakeS3Backend()
    key_sha256 = hashlib.sha256(b"cancel-key-0001").hexdigest()
    original = b'{"accepted":true,"operation":"cancel","run_id":"run-one","state":"canceling"}'
    changed = b'{"accepted":false,"operation":"cancel","run_id":"run-one","state":"canceled"}'

    first = _store(backend).claim_mutation(
        key_sha256=key_sha256,
        operation="cancel",
        run_id="run-one",
        result=original,
    )
    replayed = _store(backend).claim_mutation(
        key_sha256=key_sha256,
        operation="cancel",
        run_id="run-one",
        result=changed,
    )

    assert first == original
    assert replayed == original
    with pytest.raises(RunStoreIdempotencyConflictError):
        _store(backend).claim_mutation(
            key_sha256=key_sha256,
            operation="cancel",
            run_id="run-two",
            result=original,
        )


def test_snapshot_compare_and_swap_and_attempt_immutability() -> None:
    backend = FakeS3Backend()
    store = _store(backend)
    claimed = store.claim(create_run_record(_submission())).stored
    canceling = transition_run(
        claimed.record,
        HostedRunState.CANCELING,
        now=NOW + timedelta(seconds=1),
    )
    saved = store.save(claimed, canceling)
    assert saved.revision != claimed.revision
    assert _store(backend).get(saved.record.run_id) == saved
    with pytest.raises(RunStoreConflictError, match="precondition"):
        store.save(claimed, canceling)

    attempt = _attempt(claimed.record)
    store.append_attempt(attempt)
    store.append_attempt(attempt)
    with pytest.raises(RunStoreConflictError, match="different immutable"):
        store.append_attempt(replace(attempt, backend_id="cloud_run"))


def test_success_summary_survives_conditional_snapshot_restart() -> None:
    backend = FakeS3Backend()
    store = _store(backend)
    claimed = store.claim(create_run_record(_submission())).stored
    terminal = transition_run(
        claimed.record,
        HostedRunState.TERMINAL,
        now=NOW + timedelta(seconds=1),
        outcome=RunOutcome.SUCCEEDED,
        results_state=ResultsState.AVAILABLE,
        cleanup_state=CleanupState.CONFIRMED,
        result_summary=_result_summary(),
    )

    saved = store.save(claimed, terminal)
    recovered = _store(backend).get(saved.record.run_id)

    assert recovered is not None
    assert recovered.record.result_summary == _result_summary()
    assert recovered.record.results_state is ResultsState.AVAILABLE


def test_run_pagination_uses_an_opaque_exclusive_cursor() -> None:
    store = _store()
    for number in range(3):
        store.claim(create_run_record(_submission(key=f"start-key-000{number + 1}")))

    first = store.list(cursor=None, limit=2)
    second = store.list(cursor=first.next_cursor, limit=2)
    assert len(first.items) == 2
    assert first.next_cursor is not None
    assert len(second.items) == 1
    assert second.next_cursor is None
    assert {item.record.run_id for item in first.items}.isdisjoint(
        {item.record.run_id for item in second.items}
    )


class _InterruptedClaimStore(S3RunStore):
    def __init__(self, backend: FakeS3Backend) -> None:
        super().__init__("unit-bucket", client=FakeS3Client(backend))
        self._failed = False

    def _checkpoint(self, stage: str) -> None:
        if stage == "after_idempotency_claim" and not self._failed:
            self._failed = True
            raise RuntimeError("simulated process exit")


def test_restart_recovers_snapshot_from_durable_idempotency_claim() -> None:
    backend = FakeS3Backend()
    candidate = create_run_record(_submission())
    with pytest.raises(RuntimeError, match="simulated process exit"):
        _InterruptedClaimStore(backend).claim(candidate)

    restarted = _store(backend)
    recovered = restarted.find_idempotency(
        environment=candidate.environment,
        project=candidate.project,
        idempotency_key_sha256=candidate.idempotency_key_sha256,
    )
    assert recovered is not None
    assert recovered.record == candidate
    assert restarted.claim(candidate).created is False
