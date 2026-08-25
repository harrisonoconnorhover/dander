"""Provider-neutral schedule wakeup consumption for always-on Control."""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from dander.control.application import (
    ControlOperationConflictError,
    ControlOperationDependencyError,
    ControlOperationError,
)
from dander.control.graph_store import GraphStoreError, GraphStoreNotFoundError
from dander.control.orchestration import (
    OrchestrationContractError,
    RunSubmission,
    RunTrigger,
    ScheduleWakeup,
    TriggerKind,
    TriggerSpec,
)
from dander.control.orchestration_serialization import (
    OrchestrationSerializationError,
    deserialize_schedule_wakeup,
    serialize_schedule_wakeup,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from dander.control.application import RunLifecyclePort
    from dander.control.graph_store import GraphStore
    from dander.control.run_lifecycle import ExecutionPlanRegistry

_LOGGER = logging.getLogger("dander.control.schedule_consumer")
_MAX_MESSAGE_BYTES = 256 * 1024


class ScheduleQueueError(RuntimeError):
    """A schedule queue operation failed without exposing provider details."""


@dataclass(frozen=True, slots=True)
class QueuedScheduleMessage:
    """Opaque queue receipt plus its bounded untrusted body."""

    receipt_handle: str
    body: bytes

    def __post_init__(self) -> None:
        if not self.receipt_handle or len(self.receipt_handle) > 4096:
            raise ScheduleQueueError("Schedule queue receipt is invalid.")
        if not self.body or len(self.body) > _MAX_MESSAGE_BYTES:
            raise ScheduleQueueError("Schedule queue message size is invalid.")


class ScheduleQueuePort(Protocol):
    """Small at-least-once queue boundary used by the Control consumer."""

    def receive(self) -> tuple[QueuedScheduleMessage, ...]: ...

    def delete(self, receipt_handle: str) -> None: ...

    def close(self) -> None: ...


class ScheduledRunSubmissionResolver:
    """Resolve an exact configured occurrence into the existing lifecycle contract."""

    def __init__(
        self,
        plans: ExecutionPlanRegistry,
        graph_store: GraphStore,
        triggers: Iterable[TriggerSpec],
    ) -> None:
        selected: dict[str, TriggerSpec] = {}
        for spec in triggers:
            if spec.kind is not TriggerKind.SCHEDULE:
                raise OrchestrationContractError(
                    "the schedule consumer accepts only scheduled trigger specs"
                )
            existing = selected.setdefault(spec.trigger_id, spec)
            if existing != spec:
                raise OrchestrationContractError("a trigger id contains conflicting definitions")
            try:
                plan = plans.require_revision(spec.plan_revision)
            except ControlOperationError as error:
                raise OrchestrationContractError(
                    "a scheduled trigger selects an unregistered plan revision"
                ) from error
            if plan.plan_id != spec.plan_id:
                raise OrchestrationContractError(
                    "a scheduled trigger does not match its selected plan"
                )
        if not selected or len(selected) > 100:
            raise OrchestrationContractError(
                "the schedule registry requires 1 to 100 unique triggers"
            )
        self._plans = plans
        self._graph_store = graph_store
        self._triggers = selected

    def resolve(self, wakeup: ScheduleWakeup, *, requested_at: datetime) -> RunSubmission:
        """Resolve one enabled, exact-plan occurrence and its deterministic idempotency key."""
        spec = self._triggers.get(wakeup.trigger_id)
        if spec is None or not spec.enabled:
            raise ControlOperationConflictError("The scheduled trigger is unavailable.")
        if spec.plan_revision != wakeup.plan_revision:
            raise ControlOperationConflictError(
                "The scheduled occurrence does not select the configured plan revision."
            )
        plan = self._plans.require_revision(wakeup.plan_revision)
        try:
            graph = self._graph_store.get(plan.project, plan.graph)
        except GraphStoreNotFoundError as error:
            raise ControlOperationConflictError(
                "The scheduled graph is no longer available."
            ) from error
        except GraphStoreError as error:
            raise ControlOperationDependencyError(
                "The scheduled graph is temporarily unavailable."
            ) from error
        try:
            submission = RunSubmission(
                environment=plan.environment,
                project=plan.project,
                graph=graph,
                plan_id=plan.plan_id,
                plan_revision=plan.revision,
                trigger=RunTrigger(
                    kind=TriggerKind.SCHEDULE,
                    trigger_id=spec.trigger_id,
                    scheduled_occurrence=wakeup.scheduled_occurrence,
                ),
                idempotency_key=schedule_occurrence_idempotency_key(wakeup),
                requested_at=requested_at,
            )
            self._plans.for_submission(submission)
            return submission
        except OrchestrationContractError as error:
            raise ControlOperationConflictError(
                "The scheduled occurrence conflicts with its execution plan."
            ) from error


def schedule_occurrence_idempotency_key(wakeup: ScheduleWakeup) -> str:
    """Return one opaque key stable across provider and queue delivery retries."""
    digest = hashlib.sha256(serialize_schedule_wakeup(wakeup)).hexdigest()
    return f"schedule:{digest}"


class ControlScheduleConsumer:
    """Long-running bounded queue consumer that hands occurrences to Control."""

    def __init__(
        self,
        queue: ScheduleQueuePort,
        resolver: ScheduledRunSubmissionResolver,
        lifecycle: RunLifecyclePort,
        *,
        clock: Callable[[], datetime] | None = None,
        retry_interval_seconds: float = 1.0,
        shutdown_grace_seconds: float = 35.0,
    ) -> None:
        if retry_interval_seconds <= 0 or shutdown_grace_seconds <= 0:
            raise ValueError("Schedule consumer timing must be positive.")
        self._queue = queue
        self._resolver = resolver
        self._lifecycle = lifecycle
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retry_interval = float(retry_interval_seconds)
        self._shutdown_grace = float(shutdown_grace_seconds)
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._initial_poll_complete = False
        self._last_poll_failed = False
        self._closed = False

    def start(self) -> None:
        """Start the one schedule-consumer thread owned by this Control process."""
        with self._state_lock:
            if self._closed:
                raise ControlOperationDependencyError("The schedule consumer is closed.")
            if self._thread is not None:
                return
            thread = threading.Thread(
                target=self._loop,
                name="dander-control-schedule-consumer",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def ready(self) -> bool:
        """Report ready after one successful queue poll and while the thread remains healthy."""
        with self._state_lock:
            thread = self._thread
            return (
                not self._closed
                and thread is not None
                and thread.is_alive()
                and self._initial_poll_complete
                and not self._last_poll_failed
            )

    def poll_once(self) -> int:
        """Receive one bounded batch, deleting only successfully handed-off occurrences."""
        try:
            messages = self._queue.receive()
        except ScheduleQueueError:
            with self._state_lock:
                self._last_poll_failed = True
            raise
        with self._state_lock:
            self._initial_poll_complete = True
            self._last_poll_failed = False
        accepted = 0
        for message in messages:
            try:
                wakeup = deserialize_schedule_wakeup(message.body)
                submission = self._resolver.resolve(wakeup, requested_at=self._clock())
                self._lifecycle.start(submission)
                self._queue.delete(message.receipt_handle)
                accepted += 1
            except (
                ControlOperationError,
                OrchestrationSerializationError,
                ScheduleQueueError,
            ):
                _LOGGER.warning("control_schedule_message_rejected")
            except Exception:  # noqa: BLE001 - keep poison messages retryable without killing Control
                _LOGGER.warning("control_schedule_message_failed")
        return accepted

    def close(self) -> None:
        """Stop receiving before closing the queue transport."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        self._stop.set()
        if thread is not None:
            thread.join(timeout=self._shutdown_grace)
            if thread.is_alive():
                raise ControlOperationDependencyError(
                    "The schedule consumer did not stop within its shutdown grace period."
                )
        try:
            self._queue.close()
        except ScheduleQueueError as error:
            raise ControlOperationDependencyError(
                "The schedule queue could not close cleanly."
            ) from error

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except ScheduleQueueError:
                _LOGGER.warning("control_schedule_poll_failed")
                self._stop.wait(self._retry_interval)


__all__ = [
    "ControlScheduleConsumer",
    "QueuedScheduleMessage",
    "ScheduleQueueError",
    "ScheduleQueuePort",
    "ScheduledRunSubmissionResolver",
    "schedule_occurrence_idempotency_key",
]
