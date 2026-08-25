"""Always-on Control lifecycle composition over durable plans, stores, and backends."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from dander.control.application import (
    ControlOperationConflictError,
    ControlOperationDependencyError,
    ControlOperationIdempotencyConflictError,
    ControlOperationNotFoundError,
    ControlOperationUnavailableError,
    RunAddress,
)
from dander.control.graph_store import GraphStoreError, GraphStoreNotFoundError
from dander.control.models import (
    LogLevel,
    LogPageResponse,
    LogRecord,
    MutationResult,
    RunPageResponse,
    RunState,
    RunStatusResponse,
)
from dander.control.orchestration import (
    BackendExecutionState,
    CleanupState,
    ExecutionBackendError,
    ExecutionPlan,
    HostedRunState,
    OrchestrationContractError,
    ResultsState,
    RunOutcome,
    RunRecord,
    RunStoreConflictError,
    RunStoreError,
    RunStoreIdempotencyConflictError,
    RunSubmission,
    RunTrigger,
    StoredRun,
    TriggerKind,
    dispatch_run_attempt,
    dispatch_stored_run_attempt,
    logical_run_identity,
    transition_run,
    validate_submission_plan,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from dander.control.graph_store import GraphRecord, GraphStore
    from dander.control.orchestration import ExecutionBackend, RunStore

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_LOGGER = logging.getLogger("dander.control.reconciler")


class RunSubmissionSource(Protocol):
    """Background ingress owned and shut down by the composed run lifecycle."""

    def start(self) -> None: ...

    def ready(self) -> bool: ...

    def close(self) -> None: ...


@runtime_checkable
class DurableMutationClaimStore(Protocol):
    """Create-only mutation claim extension required by the composed lifecycle."""

    def claim_mutation(
        self,
        *,
        key_sha256: str,
        operation: str,
        run_id: str,
        result: bytes,
    ) -> bytes: ...


class ExecutionPlanRegistry:
    """Select one active immutable plan per environment/project/graph route."""

    def __init__(self, plans: Iterable[ExecutionPlan]) -> None:
        by_revision: dict[str, ExecutionPlan] = {}
        by_route: dict[tuple[str, str, str], ExecutionPlan] = {}
        for plan in plans:
            existing = by_revision.setdefault(plan.revision, plan)
            if existing != plan:
                raise OrchestrationContractError(
                    "an execution-plan revision contains conflicting contents"
                )
            route = (plan.environment, plan.project, plan.graph)
            selected = by_route.setdefault(route, plan)
            if selected.revision != plan.revision:
                raise OrchestrationContractError(
                    "an execution route selects more than one active plan revision"
                )
        if not by_revision:
            raise OrchestrationContractError("at least one execution plan is required")
        if len(by_revision) > 100:
            raise OrchestrationContractError("the execution-plan registry exceeds its bound")
        self._by_revision = by_revision
        self._by_route = by_route

    @property
    def plans(self) -> tuple[ExecutionPlan, ...]:
        """Return registered plans in deterministic revision order."""
        return tuple(self._by_revision[key] for key in sorted(self._by_revision))

    def select(self, record: GraphRecord, *, environment: str) -> ExecutionPlan:
        """Select the active plan and reject graph/image deployment staleness."""
        plan = self._by_route.get((environment, record.project, record.graph))
        if plan is None:
            raise ControlOperationUnavailableError(
                "No hosted execution plan is configured for this graph."
            )
        if (
            plan.graph_revision != record.revision
            or plan.graph_content_sha256 != record.content_sha256
        ):
            raise ControlOperationConflictError(
                "The hosted execution plan does not match the current graph revision."
            )
        return plan

    def require_revision(self, revision: str) -> ExecutionPlan:
        """Resolve an exact retained plan revision for lifecycle reconciliation."""
        plan = self._by_revision.get(revision)
        if plan is None:
            raise ControlOperationUnavailableError(
                "The run's immutable execution plan is not registered."
            )
        return plan

    def for_submission(self, submission: RunSubmission) -> ExecutionPlan:
        """Resolve and validate the exact plan selected by a submission."""
        plan = self.require_revision(submission.plan_revision)
        try:
            validate_submission_plan(submission, plan)
        except OrchestrationContractError as error:
            raise ControlOperationConflictError(
                "The run submission no longer matches its immutable execution plan."
            ) from error
        return plan

    def for_run(self, record: RunRecord) -> ExecutionPlan:
        """Resolve and validate the exact plan retained by a durable run."""
        plan = self.require_revision(record.plan_revision)
        expected = (
            record.environment,
            record.project,
            record.graph,
            record.graph_revision,
            record.graph_content_sha256,
            record.plan_id,
            record.plan_revision,
        )
        actual = (
            plan.environment,
            plan.project,
            plan.graph,
            plan.graph_revision,
            plan.graph_content_sha256,
            plan.plan_id,
            plan.revision,
        )
        if expected != actual:
            raise ControlOperationConflictError(
                "The durable run no longer matches its immutable execution plan."
            )
        return plan


class ExecutionBackendRegistry:
    """Provider-neutral backend selection and one-time transport shutdown."""

    def __init__(self, backends: Mapping[str, ExecutionBackend]) -> None:
        selected = dict(backends)
        if not selected or any(not backend_id for backend_id in selected):
            raise OrchestrationContractError("at least one named execution backend is required")
        self._backends = selected
        self._closed = False

    def require(self, backend_id: str) -> ExecutionBackend:
        """Return the selected backend without importing another provider implementation."""
        backend = self._backends.get(backend_id)
        if backend is None:
            raise ControlOperationUnavailableError(
                "The execution plan selects an unavailable backend."
            )
        return backend

    def close(self) -> None:
        """Close distinct backend instances exactly once."""
        if self._closed:
            return
        seen: set[int] = set()
        failed = False
        for backend in self._backends.values():
            if id(backend) in seen:
                continue
            seen.add(id(backend))
            try:
                backend.close()
            except ExecutionBackendError:
                failed = True
        self._closed = True
        if failed:
            raise ExecutionBackendError("One or more execution backends failed to close.")


@dataclass(frozen=True, slots=True)
class PlanRunSubmissionResolver:
    """Compatibility-route resolver for one configured Control environment."""

    plans: ExecutionPlanRegistry
    environment: str

    def resolve(
        self,
        record: GraphRecord,
        *,
        idempotency_key: str,
        requested_at: datetime,
    ) -> RunSubmission:
        plan = self.plans.select(record, environment=self.environment)
        return RunSubmission(
            environment=self.environment,
            project=record.project,
            graph=record,
            plan_id=plan.plan_id,
            plan_revision=plan.revision,
            trigger=RunTrigger(kind=TriggerKind.API, trigger_id="control-api"),
            idempotency_key=idempotency_key,
            requested_at=requested_at,
        )


class ControlRunLifecycle:
    """Compose durable state and provider backends behind Control's run API."""

    def __init__(
        self,
        store: RunStore,
        plans: ExecutionPlanRegistry,
        backends: ExecutionBackendRegistry,
        graph_store: GraphStore,
        *,
        clock: Callable[[], datetime] | None = None,
        reconcile_interval_seconds: float = 5.0,
        reconcile_page_size: int = 100,
        shutdown_grace_seconds: float = 35.0,
    ) -> None:
        if (
            isinstance(reconcile_interval_seconds, bool)
            or reconcile_interval_seconds <= 0
            or isinstance(shutdown_grace_seconds, bool)
            or shutdown_grace_seconds <= 0
        ):
            raise ValueError("Control reconciliation timing must be positive.")
        if (
            isinstance(reconcile_page_size, bool)
            or not isinstance(reconcile_page_size, int)
            or not 1 <= reconcile_page_size <= 100
        ):
            raise ValueError("Control reconciliation page size is invalid.")
        if not isinstance(store, DurableMutationClaimStore):
            raise ValueError("Control's durable run store must support mutation idempotency.")
        self._store = store
        self._mutations = store
        self._plans = plans
        self._backends = backends
        self._graph_store = graph_store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._interval = float(reconcile_interval_seconds)
        self._page_size = reconcile_page_size
        self._shutdown_grace = float(shutdown_grace_seconds)
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._mutation_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._submission_sources: list[RunSubmissionSource] = []
        self._scan_cursor: str | None = None
        self._initial_recovery_complete = False
        self._scan_failed = False
        self._last_pass_failed = False
        self._closed = False

    def install_submission_source(self, source: RunSubmissionSource) -> None:
        """Install one ingress source before the reconciler and its workers start."""
        with self._state_lock:
            if self._closed or self._thread is not None:
                raise ValueError("Run submission sources must be installed before startup.")
            self._submission_sources.append(source)

    def start_reconciler(self) -> None:
        """Start the one active background reconciler for this Control process."""
        with self._state_lock:
            if self._closed:
                raise ControlOperationDependencyError("The run lifecycle is closed.")
            if self._thread is not None:
                return
            thread = threading.Thread(
                target=self._reconcile_loop,
                name="dander-control-reconciler",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            sources = tuple(self._submission_sources)
        for source in sources:
            source.start()

    def ready(self) -> bool:
        """Report readiness only after one complete durable recovery scan."""
        with self._state_lock:
            thread = self._thread
            return (
                not self._closed
                and thread is not None
                and thread.is_alive()
                and self._initial_recovery_complete
                and not self._last_pass_failed
                and all(source.ready() for source in self._submission_sources)
            )

    def start(self, submission: RunSubmission) -> RunStatusResponse:
        with self._mutation_lock:
            return self._start(submission)

    def _start(self, submission: RunSubmission) -> RunStatusResponse:
        plan = self._plans.for_submission(submission)
        backend = self._backends.require(plan.backend_id)
        try:
            stored = dispatch_run_attempt(
                self._store,
                backend,
                submission,
                plan,
                now=self._now(),
            )
        except RunStoreIdempotencyConflictError as error:
            raise ControlOperationIdempotencyConflictError(
                "The idempotency key belongs to a different run submission."
            ) from error
        except RunStoreConflictError as error:
            recovered = self._store.get(logical_run_identity(submission))
            if recovered is None or recovered.record.submission_sha256 != submission.fingerprint:
                raise ControlOperationDependencyError(
                    "The durable run transition could not be reconciled."
                ) from error
            stored = recovered
        except (ExecutionBackendError, RunStoreError) as error:
            raise ControlOperationDependencyError(
                "The hosted run could not be durably dispatched."
            ) from error
        except OrchestrationContractError as error:
            raise ControlOperationConflictError(
                "The hosted run request conflicts with its execution plan."
            ) from error
        return _status_response(stored.record)

    def list(self, *, cursor: str | None, limit: int) -> RunPageResponse:
        try:
            page = self._store.list(cursor=cursor, limit=limit)
        except RunStoreError as error:
            raise ControlOperationDependencyError(
                "Durable run state is temporarily unavailable."
            ) from error
        return RunPageResponse(
            items=tuple(_status_response(item.record) for item in page.items),
            next_cursor=page.next_cursor,
        )

    def get(self, address: RunAddress) -> RunStatusResponse:
        return _status_response(self._require_run(address.run_id).record)

    def logs(
        self,
        address: RunAddress,
        *,
        cursor: str | None,
        limit: int,
    ) -> LogPageResponse:
        stored = self._require_run(address.run_id)
        record = stored.record
        if record.backend_handle is None:
            return LogPageResponse(records=(), next_cursor=None)
        plan = self._plans.for_run(record)
        backend = self._backends.require(plan.backend_id)
        try:
            page = backend.logs(
                plan,
                record.backend_handle,
                cursor=cursor,
                limit=limit,
            )
        except ExecutionBackendError as error:
            raise ControlOperationDependencyError(
                "Hosted run logs are temporarily unavailable."
            ) from error
        return LogPageResponse(
            records=tuple(
                LogRecord(
                    timestamp=_timestamp(item.occurred_at),
                    level=_log_level(item.level),
                    code="provider_log",
                    message=item.message,
                    correlation_id=record.run_id,
                )
                for item in page.records
            ),
            next_cursor=page.next_cursor,
        )

    def cancel(self, address: RunAddress, *, idempotency_key: str) -> MutationResult:
        with self._mutation_lock:
            return self._cancel(address, idempotency_key=idempotency_key)

    def _cancel(self, address: RunAddress, *, idempotency_key: str) -> MutationResult:
        if _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
            raise ControlOperationConflictError("The cancellation idempotency key is malformed.")
        record = self._require_run(address.run_id).record
        self._validate_cancel_preconditions(record)
        planned = _planned_cancel_result(record)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        try:
            original = self._mutations.claim_mutation(
                key_sha256=key_hash,
                operation="cancel",
                run_id=address.run_id,
                result=_mutation_result_bytes(planned),
            )
        except RunStoreIdempotencyConflictError as error:
            raise ControlOperationIdempotencyConflictError(
                "The idempotency key belongs to a different cancellation."
            ) from error
        except RunStoreError as error:
            raise ControlOperationDependencyError(
                "The cancellation idempotency claim is temporarily unavailable."
            ) from error
        try:
            result = MutationResult.model_validate_json(original)
        except ValueError as error:
            raise ControlOperationDependencyError(
                "The durable cancellation result is invalid."
            ) from error
        if result.operation != "cancel" or result.run_id != address.run_id:
            raise ControlOperationDependencyError(
                "The durable cancellation result is inconsistent."
            )
        self._cancel_once(address.run_id)
        return result

    def replay(self, address: RunAddress, *, idempotency_key: str) -> MutationResult:
        source = self._require_run(address.run_id).record
        if source.run_state is not HostedRunState.TERMINAL:
            raise ControlOperationConflictError("Only a terminal run can be replayed.")
        plan = self._plans.for_run(source)
        try:
            graph = self._graph_store.get(source.project, source.graph)
        except GraphStoreNotFoundError as error:
            raise ControlOperationConflictError(
                "The replayed run's graph is no longer available."
            ) from error
        except GraphStoreError as error:
            raise ControlOperationDependencyError(
                "The replayed run's graph is temporarily unavailable."
            ) from error
        if (
            graph.revision != source.graph_revision
            or graph.content_sha256 != source.graph_content_sha256
        ):
            raise ControlOperationConflictError(
                "The replayed run's immutable graph revision is no longer current."
            )
        try:
            submission = RunSubmission(
                environment=source.environment,
                project=source.project,
                graph=graph,
                plan_id=plan.plan_id,
                plan_revision=plan.revision,
                trigger=RunTrigger(
                    kind=TriggerKind.API,
                    trigger_id="control-api",
                    replay_of_run_id=source.run_id,
                ),
                idempotency_key=idempotency_key,
                requested_at=self._now(),
                requested_deadline_seconds=source.requested_deadline_seconds,
            )
        except OrchestrationContractError as error:
            raise ControlOperationConflictError("The replay request is invalid.") from error
        replayed = self.start(submission)
        return MutationResult(
            operation="replay",
            accepted=True,
            run_id=source.run_id,
            resulting_run_id=replayed.run_id,
            state=replayed.state,
        )

    def reconcile_once(self) -> int:
        """Reconcile one bounded durable page and advance the restart-recovery cursor."""
        with self._state_lock:
            cursor = self._scan_cursor
        try:
            page = self._store.list(cursor=cursor, limit=self._page_size)
        except RunStoreError:
            with self._state_lock:
                self._scan_failed = True
                self._last_pass_failed = True
            _LOGGER.warning("control_reconcile_page_failed")
            return 0

        failed = False
        processed = 0
        for stored in page.items:
            try:
                with self._mutation_lock:
                    self._reconcile_stored(stored)
                processed += 1
            except RunStoreConflictError:
                continue
            except (
                ControlOperationConflictError,
                ControlOperationDependencyError,
                ControlOperationUnavailableError,
                ExecutionBackendError,
                OrchestrationContractError,
                RunStoreError,
            ):
                failed = True
                _LOGGER.warning(
                    "control_reconcile_run_failed",
                    extra={"run_id": stored.record.run_id},
                )
        self._mark_reconcile_result(failed=failed, next_cursor=page.next_cursor)
        return processed

    def close(self) -> None:
        """Stop reconciliation before closing provider transports and durable state."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            sources = tuple(self._submission_sources)
        source_failed = False
        for source in sources:
            try:
                source.close()
            except ControlOperationDependencyError:
                source_failed = True
        self._stop.set()
        if thread is not None:
            thread.join(timeout=self._shutdown_grace)
            if thread.is_alive():
                raise ControlOperationDependencyError(
                    "The Control reconciler did not stop within its shutdown grace period."
                )
        failed = source_failed
        try:
            self._backends.close()
        except ExecutionBackendError:
            failed = True
        try:
            self._store.close()
        except RunStoreError:
            failed = True
        if failed:
            raise ControlOperationDependencyError("The Control lifecycle could not close cleanly.")

    def _cancel_once(self, run_id: str) -> MutationResult:
        stored = self._require_run(run_id)
        record = stored.record
        if record.run_state is HostedRunState.TERMINAL:
            return MutationResult(
                operation="cancel",
                accepted=False,
                run_id=run_id,
                state=_run_state(record),
            )
        if record.run_state is HostedRunState.QUEUED:
            canceled = transition_run(
                record,
                HostedRunState.TERMINAL,
                now=self._now(),
                outcome=RunOutcome.CANCELED,
                results_state=ResultsState.UNAVAILABLE,
                cleanup_state=CleanupState.CONFIRMED,
                stage="canceled",
            )
            try:
                stored = self._store.save(stored, canceled)
            except RunStoreConflictError as error:
                raise ControlOperationDependencyError(
                    "The cancellation raced with another run transition."
                ) from error
            return MutationResult(
                operation="cancel",
                accepted=True,
                run_id=run_id,
                state=_run_state(stored.record),
            )
        if record.backend_handle is None:
            raise ControlOperationConflictError(
                "The running execution does not have a provider handle."
            )
        if record.run_state is not HostedRunState.CANCELING:
            canceling = transition_run(
                record,
                HostedRunState.CANCELING,
                now=self._now(),
                stage="canceling",
            )
            try:
                stored = self._store.save(stored, canceling)
            except RunStoreConflictError as error:
                raise ControlOperationDependencyError(
                    "The cancellation raced with another run transition."
                ) from error
            record = stored.record
        plan = self._plans.for_run(record)
        backend = self._backends.require(plan.backend_id)
        assert record.backend_handle is not None
        try:
            backend.cancel(plan, record.backend_handle)
        except ExecutionBackendError as error:
            raise ControlOperationDependencyError(
                "The provider cancellation could not be confirmed."
            ) from error
        return MutationResult(
            operation="cancel",
            accepted=True,
            run_id=run_id,
            state=RunState.CANCELING,
        )

    def _validate_cancel_preconditions(self, record: RunRecord) -> None:
        if record.run_state in {HostedRunState.TERMINAL, HostedRunState.QUEUED}:
            return
        if record.backend_handle is None:
            raise ControlOperationConflictError(
                "The running execution does not have a provider handle."
            )
        plan = self._plans.for_run(record)
        self._backends.require(plan.backend_id)

    def _reconcile_stored(self, stored: StoredRun) -> StoredRun:
        record = stored.record
        if (
            record.run_state is HostedRunState.TERMINAL
            and record.cleanup_state is CleanupState.CONFIRMED
        ):
            return stored
        plan = self._plans.for_run(record)
        backend = self._backends.require(plan.backend_id)
        if record.run_state in {HostedRunState.QUEUED, HostedRunState.RETRYING}:
            return dispatch_stored_run_attempt(
                self._store,
                backend,
                stored,
                plan,
                now=self._now(),
            )
        handle = record.backend_handle
        if handle is None:
            if record.run_state is HostedRunState.TERMINAL:
                return stored
            raise OrchestrationContractError("an active durable run has no provider handle")
        if record.run_state is HostedRunState.CANCELING:
            backend.cancel(plan, handle)
        observation = backend.observe(plan, handle)
        if observation.execution_state is BackendExecutionState.TERMINAL:
            updated = transition_run(
                record,
                HostedRunState.TERMINAL,
                now=observation.observed_at,
                outcome=observation.outcome,
                results_state=observation.results_state,
                cleanup_state=observation.cleanup_state,
                stage=observation.stage,
            )
        elif record.run_state is HostedRunState.TERMINAL:
            return stored
        else:
            next_state = (
                HostedRunState.CANCELING
                if record.run_state is HostedRunState.CANCELING
                else HostedRunState.RUNNING
            )
            if record.run_state is next_state and record.stage == observation.stage:
                return stored
            updated = transition_run(
                record,
                next_state,
                now=observation.observed_at,
                stage=observation.stage,
            )
        if updated == record:
            return stored
        return self._store.save(stored, updated)

    def _require_run(self, run_id: str) -> StoredRun:
        try:
            stored = self._store.get(run_id)
        except RunStoreError as error:
            raise ControlOperationDependencyError(
                "Durable run state is temporarily unavailable."
            ) from error
        if stored is None:
            raise ControlOperationNotFoundError("The run does not exist.")
        return stored

    def _reconcile_loop(self) -> None:
        while not self._stop.is_set():
            self.reconcile_once()
            self._stop.wait(self._interval)

    def _mark_reconcile_result(self, *, failed: bool, next_cursor: str | None) -> None:
        with self._state_lock:
            self._scan_cursor = next_cursor
            self._scan_failed = self._scan_failed or failed
            if next_cursor is None:
                self._initial_recovery_complete = True
                self._last_pass_failed = self._scan_failed
                self._scan_failed = False
            elif failed:
                self._last_pass_failed = True

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ControlOperationDependencyError("The Control clock is invalid.")
        return now.astimezone(UTC)


def _run_state(record: RunRecord) -> RunState:
    if record.run_state is HostedRunState.QUEUED:
        return RunState.QUEUED
    if record.run_state is HostedRunState.RUNNING:
        return RunState.RUNNING
    if record.run_state is HostedRunState.RETRYING:
        return RunState.RETRYING
    if record.run_state is HostedRunState.CANCELING:
        return RunState.CANCELING
    terminal = {
        RunOutcome.SUCCEEDED: RunState.SUCCEEDED,
        RunOutcome.FAILED: RunState.FAILED,
        RunOutcome.CANCELED: RunState.CANCELED,
    }.get(record.outcome)
    if terminal is None:
        raise OrchestrationContractError("a terminal run does not have a known outcome")
    return terminal


def _status_response(record: RunRecord) -> RunStatusResponse:
    state = _run_state(record)
    failed = record.outcome is RunOutcome.FAILED
    return RunStatusResponse(
        run_id=record.run_id,
        state=state,
        stage=record.stage,
        started_at=_timestamp(record.created_at) if record.attempt_count else None,
        finished_at=_timestamp(record.terminal_at) if record.terminal_at else None,
        failure_code="hosted_execution_failed" if failed else None,
        failure_summary="The hosted execution failed." if failed else None,
        can_cancel=record.run_state
        in {HostedRunState.QUEUED, HostedRunState.RUNNING, HostedRunState.RETRYING},
        can_replay=record.run_state is HostedRunState.TERMINAL,
        logs_available=record.backend_handle is not None,
    )


def _planned_cancel_result(record: RunRecord) -> MutationResult:
    if record.run_state is HostedRunState.TERMINAL:
        return MutationResult(
            operation="cancel",
            accepted=False,
            run_id=record.run_id,
            state=_run_state(record),
        )
    return MutationResult(
        operation="cancel",
        accepted=True,
        run_id=record.run_id,
        state=(
            RunState.CANCELED if record.run_state is HostedRunState.QUEUED else RunState.CANCELING
        ),
    )


def _mutation_result_bytes(result: MutationResult) -> bytes:
    return json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _log_level(value: str | None) -> LogLevel:
    if value is None:
        return LogLevel.INFO
    aliases = {"warn": LogLevel.WARNING, "fatal": LogLevel.ERROR}
    try:
        return LogLevel(value.casefold())
    except ValueError:
        return aliases.get(value.casefold(), LogLevel.INFO)


__all__ = [
    "ControlRunLifecycle",
    "DurableMutationClaimStore",
    "ExecutionBackendRegistry",
    "ExecutionPlanRegistry",
    "PlanRunSubmissionResolver",
]
