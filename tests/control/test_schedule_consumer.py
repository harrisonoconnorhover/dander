"""Scheduled occurrence serialization, resolution, and SQS handoff tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dander.control.application import ControlOperationConflictError
from dander.control.graph_store import InMemoryGraphStore
from dander.control.models import PipelineGraphDocument
from dander.control.orchestration import (
    ExecutionPlan,
    OrchestrationContractError,
    RetryPolicy,
    RunSubmission,
    ScheduleWakeup,
    TriggerKind,
    TriggerSpec,
)
from dander.control.orchestration_serialization import (
    SCHEDULED_TIME_TOKEN,
    OrchestrationSerializationError,
    deserialize_schedule_wakeup,
    deserialize_trigger_spec,
    render_schedule_wakeup_template,
    serialize_schedule_wakeup,
    serialize_trigger_spec,
)
from dander.control.run_lifecycle import ExecutionPlanRegistry
from dander.control.schedule_consumer import (
    ControlScheduleConsumer,
    QueuedScheduleMessage,
    ScheduledRunSubmissionResolver,
    ScheduleQueueError,
    schedule_occurrence_idempotency_key,
)
from dander.control.sqs_schedule_queue import SQSScheduleQueue
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
)
from dander.runtime_contract import RUNTIME_CONTRACT

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:" + "a" * 64
DOCUMENT = PipelineGraphDocument.model_validate({"name": "hosted_graph", "nodes": [], "edges": []})


def _graph_and_plan() -> tuple[InMemoryGraphStore, ExecutionPlan]:
    store = InMemoryGraphStore(clock=lambda: NOW, revision_factory=lambda: "graph-r1")
    graph = store.create(
        "demo",
        "hosted-graph",
        DOCUMENT,
        idempotency_key="graph-key-0001",
    )
    template = ExecutionTemplate(
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
        configuration_reference="/app/dander.yaml",
        environment=(),
        secret_bindings=(),
        workload_identity="task-role",
        resources=ResourceProjection(
            cpu_millis=1000,
            memory_mib=2048,
            ephemeral_storage_mib=21504,
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
            log_destination="cloudwatch",
            metric_namespace="dander",
            alert_target=None,
            retention_days=30,
        ),
    )
    return store, ExecutionPlan(
        plan_id="aws-redshift",
        environment="production",
        project=graph.project,
        graph=graph.graph,
        graph_revision=graph.revision,
        graph_content_sha256=graph.content_sha256,
        backend_id="fargate",
        profile_id="aws",
        image=IMAGE,
        execution_template=template,
        deadline_seconds=300,
        retry_policy=RetryPolicy(max_attempts=2),
    )


def _spec(plan: ExecutionPlan, *, enabled: bool = True) -> TriggerSpec:
    return TriggerSpec(
        trigger_id="daily-redshift",
        kind=TriggerKind.SCHEDULE,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        enabled=enabled,
        schedule="cron(0 6 * * ? *)",
        time_zone="America/New_York",
    )


def test_trigger_and_wakeup_codecs_are_canonical_and_versioned() -> None:
    _store, plan = _graph_and_plan()
    spec = _spec(plan)
    wakeup = ScheduleWakeup(
        trigger_id=spec.trigger_id,
        plan_revision=plan.revision,
        scheduled_occurrence=NOW,
    )

    assert deserialize_trigger_spec(serialize_trigger_spec(spec)) == spec
    assert deserialize_schedule_wakeup(serialize_schedule_wakeup(wakeup)) == wakeup
    template = render_schedule_wakeup_template(spec)
    assert SCHEDULED_TIME_TOKEN in template
    assert (
        deserialize_schedule_wakeup(
            template.replace(SCHEDULED_TIME_TOKEN, "2026-08-25T12:00:00Z").encode()
        )
        == wakeup
    )
    with pytest.raises(OrchestrationSerializationError, match="not canonical"):
        deserialize_trigger_spec(serialize_trigger_spec(spec) + b"\n")


def test_schedule_resolution_uses_exact_occurrence_idempotency_and_current_plan() -> None:
    graph_store, plan = _graph_and_plan()
    resolver = ScheduledRunSubmissionResolver(
        ExecutionPlanRegistry((plan,)),
        graph_store,
        (_spec(plan),),
    )
    wakeup = ScheduleWakeup("daily-redshift", plan.revision, NOW)

    first = resolver.resolve(wakeup, requested_at=NOW)
    repeated = resolver.resolve(wakeup, requested_at=NOW + timedelta(minutes=1))
    later = ScheduleWakeup("daily-redshift", plan.revision, NOW + timedelta(days=1))

    assert first.idempotency_key == repeated.idempotency_key
    assert first.fingerprint == repeated.fingerprint
    assert first.trigger.scheduled_occurrence == NOW
    assert first.idempotency_key != schedule_occurrence_idempotency_key(later)

    disabled = ScheduledRunSubmissionResolver(
        ExecutionPlanRegistry((plan,)),
        graph_store,
        (_spec(plan, enabled=False),),
    )
    with pytest.raises(ControlOperationConflictError, match="unavailable"):
        disabled.resolve(wakeup, requested_at=NOW)

    with pytest.raises(OrchestrationContractError, match="unregistered"):
        ScheduledRunSubmissionResolver(
            ExecutionPlanRegistry((plan,)),
            graph_store,
            (
                TriggerSpec(
                    trigger_id="other",
                    kind=TriggerKind.SCHEDULE,
                    plan_id=plan.plan_id,
                    plan_revision="f" * 64,
                    enabled=True,
                    schedule="rate(1 day)",
                    time_zone="UTC",
                ),
            ),
        )


class _Queue:
    def __init__(self, messages: tuple[QueuedScheduleMessage, ...]) -> None:
        self.messages = messages
        self.deleted: list[str] = []
        self.closed = False

    def receive(self) -> tuple[QueuedScheduleMessage, ...]:
        messages, self.messages = self.messages, ()
        return messages

    def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    def close(self) -> None:
        self.closed = True


class _Lifecycle:
    def __init__(self) -> None:
        self.submissions: list[RunSubmission] = []

    def start(self, submission: RunSubmission) -> object:
        self.submissions.append(submission)
        return object()


def test_consumer_deletes_only_successfully_handed_off_canonical_messages() -> None:
    graph_store, plan = _graph_and_plan()
    resolver = ScheduledRunSubmissionResolver(
        ExecutionPlanRegistry((plan,)),
        graph_store,
        (_spec(plan),),
    )
    lifecycle = _Lifecycle()
    wakeup = ScheduleWakeup("daily-redshift", plan.revision, NOW)
    queue = _Queue(
        (
            QueuedScheduleMessage("good-receipt", serialize_schedule_wakeup(wakeup)),
            QueuedScheduleMessage("poison-receipt", b"{}"),
        )
    )
    consumer = ControlScheduleConsumer(queue, resolver, lifecycle, clock=lambda: NOW)  # type: ignore[arg-type]

    assert consumer.poll_once() == 1
    assert queue.deleted == ["good-receipt"]
    assert len(lifecycle.submissions) == 1
    assert lifecycle.submissions[0].idempotency_key == schedule_occurrence_idempotency_key(wakeup)
    consumer.close()
    assert queue.closed


class _SQSClient:
    def __init__(self, body: str) -> None:
        self.body = body
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def receive_message(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("receive", kwargs))
        return {"Messages": [{"ReceiptHandle": "receipt", "Body": self.body}]}

    def delete_message(self, **kwargs: object) -> None:
        self.calls.append(("delete", kwargs))

    def close(self) -> None:
        self.closed = True


def test_sqs_adapter_uses_long_polling_and_exact_queue_coordinates() -> None:
    _store, plan = _graph_and_plan()
    wakeup = ScheduleWakeup("daily-redshift", plan.revision, NOW)
    client = _SQSClient(serialize_schedule_wakeup(wakeup).decode())
    queue = SQSScheduleQueue(
        "https://sqs.us-east-1.amazonaws.com/123456789012/dander-d7-control-schedules",
        expected_account_id="123456789012",
        expected_region="us-east-1",
        client=client,
    )

    message = queue.receive()[0]
    queue.delete(message.receipt_handle)
    queue.close()

    assert client.calls[0] == (
        "receive",
        {
            "QueueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/dander-d7-control-schedules",
            "MaxNumberOfMessages": 10,
            "WaitTimeSeconds": 20,
        },
    )
    assert client.calls[1][0] == "delete"
    assert client.closed
    with pytest.raises(ScheduleQueueError, match="selected AWS boundary"):
        SQSScheduleQueue(
            "https://sqs.us-west-2.amazonaws.com/123456789012/wrong-region",
            expected_account_id="123456789012",
            expected_region="us-east-1",
        )
