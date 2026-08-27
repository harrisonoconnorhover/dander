"""Provider-neutral contracts for Control-owned hosted run orchestration.

These contracts deliberately stop before durable storage, provider SDK calls, background
reconciliation, or HTTP composition.  Control may issue a provider request more than once after a
crash; backends must turn those at-least-once requests into one idempotent provider effect by using
the deterministic logical run and attempt identities supplied here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from dander.control.graph_store import GraphRecord
    from dander.deployment.projection import ExecutionTemplate

ORCHESTRATION_SCHEMA = "io.dander.control.orchestration/v1"
EXECUTION_PLAN_SCHEMA = "io.dander.control.execution-plan/v1"
EXECUTION_RESULT_SUMMARY_SCHEMA = "io.dander.control.execution-result-summary/v1"
PLACEMENT_DECISION_SCHEMA = "io.dander.control.placement-decision/v1"

_PORTABLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_OPAQUE_VALUE = 1024
_MAX_RESULT_INTEGER = 9_223_372_036_854_775_807


class OrchestrationContractError(ValueError):
    """An orchestration value is incomplete or internally inconsistent."""


class RunTransitionError(OrchestrationContractError):
    """A run lifecycle transition would regress or contradict durable state."""


class RunStoreError(RuntimeError):
    """A durable run-store operation failed without exposing provider details."""


class RunStoreConflictError(RunStoreError):
    """A conditional run-store operation no longer matches durable state."""


class RunStoreCorruptionError(RunStoreError):
    """Durable run state is oversized, malformed, or internally inconsistent."""


class RunStoreIdempotencyConflictError(RunStoreConflictError):
    """An idempotency key was reused for a different logical submission."""


class ExecutionBackendError(RuntimeError):
    """A hosted execution backend failed without exposing provider-native details."""


class TriggerKind(StrEnum):
    """Supported reasons that Control may create a hosted logical run."""

    API = "api"
    MANUAL = "manual"
    SCHEDULE = "schedule"
    DEPENDENCY = "dependency"


class HostedRunState(StrEnum):
    """Execution progress independent of outcome and reconciliation."""

    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    CANCELING = "canceling"
    TERMINAL = "terminal"


class RunOutcome(StrEnum):
    """Terminal execution outcome, or unknown while it is not established."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


class ResultsState(StrEnum):
    """Whether normalized results can be returned by Control."""

    PENDING = "pending"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class CleanupState(StrEnum):
    """Whether provider cleanup has been reconciled independently of outcome."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"


class BackendExecutionState(StrEnum):
    """Small normalized provider observation used by lifecycle composition."""

    SUBMITTED = "submitted"
    RUNNING = "running"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"


class PlacementMode(StrEnum):
    """How Control selected the immutable execution plan for one run."""

    AUTOMATIC = "automatic"
    MANUAL_OVERRIDE = "manual_override"
    CONFIGURED_DEFAULT = "configured_default"
    SCHEDULED = "scheduled"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class PlacementCandidate:
    """One static cost/locality estimate attached to an immutable plan revision."""

    plan_revision: str
    locality: str
    estimated_cost_microusd: int

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.plan_revision) is None:
            raise OrchestrationContractError(
                "placement candidate plan revision must be a lowercase SHA-256"
            )
        _require_portable_id(self.locality, label="placement locality")
        if (
            isinstance(self.estimated_cost_microusd, bool)
            or not isinstance(self.estimated_cost_microusd, int)
            or not 0 <= self.estimated_cost_microusd <= _MAX_RESULT_INTEGER
        ):
            raise OrchestrationContractError(
                "placement estimated cost must be bounded non-negative micro-USD"
            )


@dataclass(frozen=True, slots=True)
class PlacementDecision:
    """Versioned, fixed-size explanation of Control's execution-plan selection."""

    mode: PlacementMode
    selected_environment: str
    selected_locality: str | None
    estimated_cost_microusd: int | None
    preferred_locality: str | None
    max_cost_microusd: int | None
    eligible_plan_count: int
    schema: str = PLACEMENT_DECISION_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PLACEMENT_DECISION_SCHEMA:
            raise OrchestrationContractError("unsupported placement-decision contract")
        _require_portable_id(self.selected_environment, label="selected environment")
        if self.selected_locality is not None:
            _require_portable_id(self.selected_locality, label="selected locality")
        if self.preferred_locality is not None:
            _require_portable_id(self.preferred_locality, label="preferred locality")
        for label, value in (
            ("estimated cost", self.estimated_cost_microusd),
            ("maximum cost", self.max_cost_microusd),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_RESULT_INTEGER
            ):
                raise OrchestrationContractError(
                    f"placement {label} must be bounded non-negative micro-USD"
                )
        if (self.selected_locality is None) != (self.estimated_cost_microusd is None):
            raise OrchestrationContractError(
                "placement selection locality and cost must be supplied together"
            )
        if isinstance(self.eligible_plan_count, bool) or not 1 <= self.eligible_plan_count <= 100:
            raise OrchestrationContractError("placement eligible plan count is invalid")
        if self.mode is PlacementMode.AUTOMATIC:
            if (
                self.selected_locality is None
                or self.preferred_locality is None
                or self.max_cost_microusd is None
            ):
                raise OrchestrationContractError(
                    "automatic placement requires locality, cost, and budget evidence"
                )
            assert self.estimated_cost_microusd is not None
            if self.estimated_cost_microusd > self.max_cost_microusd:
                raise OrchestrationContractError(
                    "automatic placement exceeds its maximum estimated cost"
                )


@dataclass(frozen=True, slots=True)
class ExecutionResultSummary:
    """Fixed-size, provider-neutral aggregates from one completed runtime event."""

    endpoints: int
    extracted_rows: int
    affected_rows: int
    models: int
    assertions: int
    assets: int
    duration_ms: int
    operation_count: int
    retry_count: int
    rows_read: int
    rows_written: int
    rows_affected: int
    bytes_read: int
    bytes_written: int
    bytes_processed: int
    bytes_billed: int
    queue_duration_ms: int
    execution_duration_ms: int
    spill_bytes: int
    skipped: bool = False
    schema: str = EXECUTION_RESULT_SUMMARY_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != EXECUTION_RESULT_SUMMARY_SCHEMA:
            raise OrchestrationContractError("unsupported execution-result summary contract")
        if not isinstance(self.skipped, bool):
            raise OrchestrationContractError("execution-result skipped state must be boolean")
        for label, value in (
            ("endpoints", self.endpoints),
            ("extracted_rows", self.extracted_rows),
            ("affected_rows", self.affected_rows),
            ("models", self.models),
            ("assertions", self.assertions),
            ("assets", self.assets),
            ("duration_ms", self.duration_ms),
            ("operation_count", self.operation_count),
            ("retry_count", self.retry_count),
            ("rows_read", self.rows_read),
            ("rows_written", self.rows_written),
            ("rows_affected", self.rows_affected),
            ("bytes_read", self.bytes_read),
            ("bytes_written", self.bytes_written),
            ("bytes_processed", self.bytes_processed),
            ("bytes_billed", self.bytes_billed),
            ("queue_duration_ms", self.queue_duration_ms),
            ("execution_duration_ms", self.execution_duration_ms),
            ("spill_bytes", self.spill_bytes),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_RESULT_INTEGER
            ):
                raise OrchestrationContractError(
                    f"execution-result {label} must be a bounded non-negative integer"
                )


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded launcher-attempt policy owned by one immutable execution plan."""

    max_attempts: int
    retryable_exit_codes: tuple[int, ...] = (75,)

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise OrchestrationContractError("max_attempts must be a positive integer")
        if (
            not self.retryable_exit_codes
            or len(set(self.retryable_exit_codes)) != len(self.retryable_exit_codes)
            or tuple(sorted(self.retryable_exit_codes)) != self.retryable_exit_codes
            or any(
                isinstance(code, bool) or not 0 <= code <= 255 for code in self.retryable_exit_codes
            )
        ):
            raise OrchestrationContractError(
                "retryable exit codes must be unique, sorted process exit codes"
            )


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Immutable description of what Control asks an execution backend to run.

    Scheduling is intentionally absent.  ``TriggerSpec`` may change independently while still
    selecting this exact plan revision.
    """

    plan_id: str
    environment: str
    project: str
    graph: str
    graph_revision: str
    graph_content_sha256: str
    backend_id: str
    profile_id: str
    image: str
    execution_template: ExecutionTemplate
    deadline_seconds: int
    retry_policy: RetryPolicy
    schema: str = ORCHESTRATION_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != ORCHESTRATION_SCHEMA:
            raise OrchestrationContractError("unsupported orchestration contract")
        for label, value in (
            ("plan", self.plan_id),
            ("environment", self.environment),
            ("project", self.project),
            ("graph", self.graph),
        ):
            _require_portable_id(value, label=label)
        _require_opaque_id(self.backend_id, label="backend")
        if not self.graph_revision or len(self.graph_revision) > 512:
            raise OrchestrationContractError("graph revision is missing or oversized")
        if _SHA256.fullmatch(self.graph_content_sha256) is None:
            raise OrchestrationContractError("graph content identity must be a lowercase SHA-256")
        if isinstance(self.deadline_seconds, bool) or self.deadline_seconds < 1:
            raise OrchestrationContractError("plan deadline must be a positive integer")
        template = self.execution_template
        if self.backend_id != template.launcher:
            raise OrchestrationContractError("plan backend does not match its execution template")
        if self.profile_id != template.profile_id:
            raise OrchestrationContractError("plan profile does not match its execution template")
        if self.image != template.image:
            raise OrchestrationContractError("plan image does not match its execution template")
        if self.deadline_seconds != template.resources.deadline_seconds:
            raise OrchestrationContractError("plan deadline does not match its execution template")
        if self.retry_policy.max_attempts != template.resources.launcher_retry_count + 1:
            raise OrchestrationContractError(
                "plan retry policy does not match its execution template"
            )
        if template.schedule.expression is not None or template.schedule.time_zone is not None:
            raise OrchestrationContractError(
                "execution plans must not embed schedule or time-zone selection"
            )

    @property
    def revision(self) -> str:
        """Return the SHA-256 identity computed from versioned canonical plan contents."""
        return hashlib.sha256(canonical_execution_plan_contents(self)).hexdigest()


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    """Independently versionable trigger selection kept separate from execution identity."""

    trigger_id: str
    kind: TriggerKind
    plan_id: str
    plan_revision: str
    enabled: bool
    schedule: str | None = None
    time_zone: str | None = None
    dependency: str | None = None

    def __post_init__(self) -> None:
        _require_portable_id(self.trigger_id, label="trigger")
        _require_portable_id(self.plan_id, label="plan")
        if _SHA256.fullmatch(self.plan_revision) is None:
            raise OrchestrationContractError("trigger plan revision must be a lowercase SHA-256")
        if not isinstance(self.enabled, bool):
            raise OrchestrationContractError("trigger enabled state must be boolean")
        if self.kind is TriggerKind.SCHEDULE:
            if not self.schedule or not self.schedule.strip() or not self.time_zone:
                raise OrchestrationContractError(
                    "scheduled triggers require an expression and time zone"
                )
            if self.dependency is not None:
                raise OrchestrationContractError("scheduled triggers cannot name a dependency")
            return
        if self.schedule is not None or self.time_zone is not None:
            raise OrchestrationContractError("only scheduled triggers may carry schedule fields")
        if self.kind is TriggerKind.DEPENDENCY:
            _require_opaque_value(self.dependency, label="dependency")
        elif self.dependency is not None:
            raise OrchestrationContractError("only dependency triggers may name a dependency")


@dataclass(frozen=True, slots=True)
class ScheduleWakeup:
    """One provider-neutral scheduled occurrence delivered to always-on Control."""

    trigger_id: str
    plan_revision: str
    scheduled_occurrence: datetime

    def __post_init__(self) -> None:
        _require_portable_id(self.trigger_id, label="trigger")
        if _SHA256.fullmatch(self.plan_revision) is None:
            raise OrchestrationContractError("wakeup plan revision must be a lowercase SHA-256")
        _require_utc(self.scheduled_occurrence, label="scheduled occurrence")


@dataclass(frozen=True, slots=True)
class RunTrigger:
    """The exact trigger occurrence attached to one logical run submission."""

    kind: TriggerKind
    trigger_id: str
    scheduled_occurrence: datetime | None = None
    replay_of_run_id: str | None = None

    def __post_init__(self) -> None:
        _require_portable_id(self.trigger_id, label="trigger")
        if self.kind is TriggerKind.SCHEDULE:
            if self.scheduled_occurrence is None:
                raise OrchestrationContractError(
                    "scheduled run triggers require their scheduled occurrence"
                )
            _require_utc(self.scheduled_occurrence, label="scheduled occurrence")
        elif self.scheduled_occurrence is not None:
            raise OrchestrationContractError(
                "only scheduled run triggers may carry a scheduled occurrence"
            )
        if self.replay_of_run_id is not None:
            _require_opaque_id(self.replay_of_run_id, label="replayed run")


@dataclass(frozen=True, slots=True)
class RunSubmission:
    """Provider-neutral request resolved before entering the hosted run lifecycle."""

    environment: str
    project: str
    graph: GraphRecord
    plan_id: str
    plan_revision: str
    trigger: RunTrigger
    idempotency_key: str
    requested_at: datetime
    requested_deadline_seconds: int | None = None
    placement_decision: PlacementDecision | None = None

    def __post_init__(self) -> None:
        _require_portable_id(self.environment, label="environment")
        _require_portable_id(self.project, label="project")
        _require_portable_id(self.plan_id, label="plan")
        if self.graph.project != self.project:
            raise OrchestrationContractError("submission project does not match its graph")
        if _SHA256.fullmatch(self.plan_revision) is None:
            raise OrchestrationContractError("submission plan revision must be a lowercase SHA-256")
        if (
            self.placement_decision is not None
            and self.placement_decision.selected_environment != self.environment
        ):
            raise OrchestrationContractError(
                "submission placement decision selects a different environment"
            )
        if _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key) is None:
            raise OrchestrationContractError("submission idempotency key is malformed")
        _require_utc(self.requested_at, label="requested_at")
        if self.requested_deadline_seconds is not None and (
            isinstance(self.requested_deadline_seconds, bool) or self.requested_deadline_seconds < 1
        ):
            raise OrchestrationContractError("requested deadline must be a positive integer")

    @property
    def fingerprint(self) -> str:
        """Identity of logical input excluding request time and the idempotency key itself."""
        occurrence = self.trigger.scheduled_occurrence
        payload = {
            "environment": self.environment,
            "project": self.project,
            "graph": self.graph.graph,
            "graph_revision": self.graph.revision,
            "graph_content_sha256": self.graph.content_sha256,
            "plan_id": self.plan_id,
            "plan_revision": self.plan_revision,
            "trigger": {
                "kind": self.trigger.kind.value,
                "trigger_id": self.trigger.trigger_id,
                "scheduled_occurrence": occurrence.isoformat() if occurrence else None,
                "replay_of_run_id": self.trigger.replay_of_run_id,
            },
            "requested_deadline_seconds": self.requested_deadline_seconds,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def idempotency_key_sha256(self) -> str:
        """Return the safe durable lookup identity without retaining the caller's key."""
        return hashlib.sha256(self.idempotency_key.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class BackendHandle:
    """Opaque normalized address for one provider execution."""

    backend_id: str
    execution_id: str

    def __post_init__(self) -> None:
        _require_opaque_id(self.backend_id, label="backend")
        _require_opaque_value(self.execution_id, label="provider execution")


@dataclass(frozen=True, slots=True)
class BackendObservation:
    """One provider observation without provider-native payloads or exceptions."""

    execution_state: BackendExecutionState
    outcome: RunOutcome
    results_state: ResultsState
    cleanup_state: CleanupState
    observed_at: datetime
    stage: str | None = None
    failure_code: str | None = None
    result_summary: ExecutionResultSummary | None = None

    def __post_init__(self) -> None:
        _require_utc(self.observed_at, label="observed_at")
        _require_optional_summary(self.stage, label="stage")
        _require_optional_summary(self.failure_code, label="failure code")
        if self.execution_state is not BackendExecutionState.TERMINAL and (
            self.outcome is not RunOutcome.UNKNOWN
            or self.results_state is not ResultsState.PENDING
            or self.cleanup_state is not CleanupState.PENDING
            or self.result_summary is not None
        ):
            raise OrchestrationContractError(
                "non-terminal backend observations cannot report terminal reconciliation"
            )
        if (self.results_state is ResultsState.AVAILABLE) != (self.result_summary is not None):
            raise OrchestrationContractError(
                "available backend results require exactly one execution-result summary"
            )
        if self.result_summary is not None and self.outcome is not RunOutcome.SUCCEEDED:
            raise OrchestrationContractError(
                "only successful backend observations may report an execution-result summary"
            )


@dataclass(frozen=True, slots=True)
class BackendLogRecord:
    """One bounded, sanitized, provider-neutral log record."""

    occurred_at: datetime
    message: str
    level: str | None = None

    def __post_init__(self) -> None:
        _require_utc(self.occurred_at, label="log timestamp")
        if not self.message or len(self.message) > 16_384:
            raise OrchestrationContractError("log message is missing or oversized")
        _require_optional_summary(self.level, label="log level")


@dataclass(frozen=True, slots=True)
class BackendLogPage:
    """Bounded log page with a backend-opaque continuation cursor."""

    records: tuple[BackendLogRecord, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        if len(self.records) > 500:
            raise OrchestrationContractError("backend log page exceeds the Control limit")
        if self.next_cursor is not None:
            _require_opaque_value(self.next_cursor, label="log cursor")


class ExecutionBackend(Protocol):
    """Provider adapter for hosted runs; direct CLI launchers remain independent.

    ``submit_or_adopt`` may be called repeatedly for the same logical attempt.  Implementations
    must derive or discover one deterministic provider execution identity from ``run_id`` and
    ``attempt_id`` so repeated calls adopt the original effect instead of launching another.
    """

    def submit_or_adopt(
        self,
        plan: ExecutionPlan,
        *,
        run_id: str,
        attempt_id: str,
        trigger: RunTrigger,
    ) -> BackendHandle: ...

    def observe(self, plan: ExecutionPlan, handle: BackendHandle) -> BackendObservation: ...

    def logs(
        self,
        plan: ExecutionPlan,
        handle: BackendHandle,
        *,
        cursor: str | None,
        limit: int,
    ) -> BackendLogPage: ...

    def cancel(self, plan: ExecutionPlan, handle: BackendHandle) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Canonical hosted-run snapshot with independent reconciliation dimensions."""

    run_id: str
    environment: str
    project: str
    graph: str
    graph_revision: str
    graph_content_sha256: str
    plan_id: str
    plan_revision: str
    trigger: RunTrigger
    idempotency_key_sha256: str
    submission_sha256: str
    requested_at: datetime
    requested_deadline_seconds: int | None
    run_state: HostedRunState
    outcome: RunOutcome
    results_state: ResultsState
    cleanup_state: CleanupState
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None
    stage: str | None = None
    attempt_count: int = 0
    current_attempt_id: str | None = None
    backend_handle: BackendHandle | None = None
    result_summary: ExecutionResultSummary | None = None
    placement_decision: PlacementDecision | None = None

    def __post_init__(self) -> None:
        _require_opaque_id(self.run_id, label="run")
        for label, value in (
            ("environment", self.environment),
            ("project", self.project),
            ("graph", self.graph),
            ("plan", self.plan_id),
        ):
            _require_portable_id(value, label=label)
        for label, value in (
            ("graph content", self.graph_content_sha256),
            ("plan revision", self.plan_revision),
            ("idempotency key", self.idempotency_key_sha256),
            ("submission", self.submission_sha256),
        ):
            if _SHA256.fullmatch(value) is None:
                raise OrchestrationContractError(f"{label} identity must be a lowercase SHA-256")
        if not self.graph_revision or len(self.graph_revision) > 512:
            raise OrchestrationContractError("run graph revision is missing or oversized")
        for label, timestamp in (
            ("requested_at", self.requested_at),
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            _require_utc(timestamp, label=label)
        if self.terminal_at is not None:
            _require_utc(self.terminal_at, label="terminal_at")
        if self.updated_at < self.created_at:
            raise OrchestrationContractError("run update time predates creation")
        if self.attempt_count < 0 or isinstance(self.attempt_count, bool):
            raise OrchestrationContractError("run attempt count must not be negative")
        if self.requested_deadline_seconds is not None and (
            isinstance(self.requested_deadline_seconds, bool) or self.requested_deadline_seconds < 1
        ):
            raise OrchestrationContractError("run requested deadline must be a positive integer")
        if (self.attempt_count == 0) != (self.current_attempt_id is None):
            raise OrchestrationContractError("run attempt identity does not match its count")
        if self.current_attempt_id is not None:
            expected = attempt_identity(self.run_id, self.attempt_count)
            if self.current_attempt_id != expected:
                raise OrchestrationContractError(
                    "run current attempt identity is not deterministic"
                )
        if self.backend_handle is not None and self.backend_handle.backend_id == "":
            raise OrchestrationContractError("run backend handle is malformed")
        _require_optional_summary(self.stage, label="stage")
        if self.result_summary is not None and (
            self.run_state is not HostedRunState.TERMINAL
            or self.outcome is not RunOutcome.SUCCEEDED
            or self.results_state is not ResultsState.AVAILABLE
        ):
            raise OrchestrationContractError(
                "execution-result summaries require available successful terminal results"
            )
        if (
            self.placement_decision is not None
            and self.placement_decision.selected_environment != self.environment
        ):
            raise OrchestrationContractError(
                "run placement decision selects a different environment"
            )
        _validate_run_dimensions(self)


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """Immutable intent record for one deterministic hosted provider attempt."""

    run_id: str
    attempt_id: str
    attempt_number: int
    plan_id: str
    plan_revision: str
    backend_id: str
    trigger: RunTrigger
    created_at: datetime

    def __post_init__(self) -> None:
        _require_opaque_id(self.run_id, label="run")
        _require_opaque_id(self.attempt_id, label="attempt")
        _require_portable_id(self.plan_id, label="plan")
        _require_opaque_id(self.backend_id, label="backend")
        if _SHA256.fullmatch(self.plan_revision) is None:
            raise OrchestrationContractError("attempt plan revision must be a lowercase SHA-256")
        if isinstance(self.attempt_number, bool) or self.attempt_number < 1:
            raise OrchestrationContractError("attempt number must be a positive integer")
        if self.attempt_id != attempt_identity(self.run_id, self.attempt_number):
            raise OrchestrationContractError("attempt identity is not deterministic")
        _require_utc(self.created_at, label="attempt created_at")


@dataclass(frozen=True, slots=True)
class StoredRun:
    """One run snapshot plus its provider-opaque compare-and-swap revision."""

    record: RunRecord
    revision: str

    def __post_init__(self) -> None:
        _require_opaque_value(self.revision, label="run revision")


@dataclass(frozen=True, slots=True)
class RunClaim:
    """Result of idempotently claiming a logical run."""

    stored: StoredRun
    created: bool


@dataclass(frozen=True, slots=True)
class StoredRunPage:
    """Bounded run page with a store-opaque continuation cursor."""

    items: tuple[StoredRun, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        if len(self.items) > 100:
            raise OrchestrationContractError("run page exceeds the Control limit")
        if self.next_cursor is not None:
            _require_opaque_value(self.next_cursor, label="run cursor")


class RunStore(Protocol):
    """Conditional run snapshots plus immutable attempt history.

    Implementations own deterministic keys, idempotency lookup, bounded pagination, and
    compare-and-swap revisions.  ``append_attempt`` must replay an identical record and reject a
    different record at the same ``attempt_id``.
    """

    def claim(self, record: RunRecord) -> RunClaim: ...

    def get(self, run_id: str) -> StoredRun | None: ...

    def find_idempotency(
        self,
        *,
        environment: str,
        project: str,
        idempotency_key_sha256: str,
    ) -> StoredRun | None: ...

    def save(self, stored: StoredRun, record: RunRecord) -> StoredRun: ...

    def append_attempt(self, attempt: AttemptRecord) -> None: ...

    def list(self, *, cursor: str | None, limit: int) -> StoredRunPage: ...

    def close(self) -> None: ...


def create_run_record(submission: RunSubmission) -> RunRecord:
    """Create the deterministic queued candidate used by ``RunStore.claim``."""
    run_id = logical_run_identity(submission)
    return RunRecord(
        run_id=run_id,
        environment=submission.environment,
        project=submission.project,
        graph=submission.graph.graph,
        graph_revision=submission.graph.revision,
        graph_content_sha256=submission.graph.content_sha256,
        plan_id=submission.plan_id,
        plan_revision=submission.plan_revision,
        trigger=submission.trigger,
        idempotency_key_sha256=submission.idempotency_key_sha256,
        submission_sha256=submission.fingerprint,
        requested_at=submission.requested_at,
        requested_deadline_seconds=submission.requested_deadline_seconds,
        run_state=HostedRunState.QUEUED,
        outcome=RunOutcome.UNKNOWN,
        results_state=ResultsState.PENDING,
        cleanup_state=CleanupState.PENDING,
        created_at=submission.requested_at,
        updated_at=submission.requested_at,
        placement_decision=submission.placement_decision,
    )


def parse_placement_candidate_spec(value: str) -> PlacementCandidate:
    """Parse the compact revision,locality,micro-USD Control startup syntax."""
    parts = value.split(",")
    if len(parts) != 3:
        raise OrchestrationContractError(
            "placement candidate must use revision,locality,estimated-cost-microusd syntax"
        )
    revision, locality, cost = parts
    try:
        estimated_cost_microusd = int(cost)
    except ValueError as error:
        raise OrchestrationContractError(
            "placement candidate estimated cost must be an integer"
        ) from error
    candidate = PlacementCandidate(
        plan_revision=revision,
        locality=locality,
        estimated_cost_microusd=estimated_cost_microusd,
    )
    if format_placement_candidate_spec(candidate) != value:
        raise OrchestrationContractError("placement candidate syntax is not canonical")
    return candidate


def format_placement_candidate_spec(candidate: PlacementCandidate) -> str:
    """Render the compact canonical Control startup syntax for one estimate."""
    return ",".join(
        (
            candidate.plan_revision,
            candidate.locality,
            str(candidate.estimated_cost_microusd),
        )
    )


def canonical_execution_plan_contents(plan: ExecutionPlan) -> bytes:
    """Serialize the immutable plan contents used to derive its revision.

    The revision is deliberately absent from these bytes, so callers cannot supply or persist an
    arbitrary SHA-shaped identity independently of the selected graph, image, or launcher input.
    """
    payload = {
        "schema": EXECUTION_PLAN_SCHEMA,
        "plan": {
            "plan_id": plan.plan_id,
            "environment": plan.environment,
            "project": plan.project,
            "graph": plan.graph,
            "graph_revision": plan.graph_revision,
            "graph_content_sha256": plan.graph_content_sha256,
            "backend_id": plan.backend_id,
            "profile_id": plan.profile_id,
            "image": plan.image,
            "execution_template": plan.execution_template.as_dict(),
            "deadline_seconds": plan.deadline_seconds,
            "retry_policy": {
                "max_attempts": plan.retry_policy.max_attempts,
                "retryable_exit_codes": list(plan.retry_policy.retryable_exit_codes),
            },
            "orchestration_schema": plan.schema,
        },
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def logical_run_identity(submission: RunSubmission) -> str:
    """Return the stable logical run ID for one scoped idempotency key."""
    value = ":".join(
        (submission.environment, submission.project, submission.idempotency_key_sha256)
    )
    return f"run-{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def attempt_identity(run_id: str, attempt_number: int) -> str:
    """Return one stable logical attempt ID without selecting provider naming syntax."""
    if isinstance(attempt_number, bool) or attempt_number < 1:
        raise OrchestrationContractError("attempt number must be a positive integer")
    _require_opaque_id(run_id, label="run")
    digest = hashlib.sha256(f"{run_id}:{attempt_number}".encode()).hexdigest()[:20]
    return f"attempt-{attempt_number}-{digest}"


def validate_submission_plan(submission: RunSubmission, plan: ExecutionPlan) -> None:
    """Reject plan selection drift before a provider request can occur."""
    expected = (
        submission.environment,
        submission.project,
        submission.graph.graph,
        submission.graph.revision,
        submission.graph.content_sha256,
        submission.plan_id,
        submission.plan_revision,
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
        raise OrchestrationContractError("submission does not select the exact execution plan")
    if (
        submission.requested_deadline_seconds is not None
        and submission.requested_deadline_seconds > plan.deadline_seconds
    ):
        raise OrchestrationContractError("requested deadline exceeds the execution plan limit")


def dispatch_run_attempt(
    store: RunStore,
    backend: ExecutionBackend,
    submission: RunSubmission,
    plan: ExecutionPlan,
    *,
    now: datetime,
) -> StoredRun:
    """Claim and dispatch one hosted run while remaining safe across the save-after-submit crash.

    A crash after ``submit_or_adopt`` but before ``save`` leaves the durable run queued.  A restart
    derives the same attempt ID, re-appends the same immutable attempt record, and asks the backend
    to adopt the same provider effect.
    """
    _require_utc(now, label="dispatch time")
    validate_submission_plan(submission, plan)
    candidate = create_run_record(submission)
    claim = store.claim(candidate)
    stored = claim.stored
    if stored.record.submission_sha256 != candidate.submission_sha256:
        raise RunStoreIdempotencyConflictError(
            "the idempotency key belongs to a different logical submission"
        )
    return dispatch_stored_run_attempt(store, backend, stored, plan, now=now)


def dispatch_stored_run_attempt(
    store: RunStore,
    backend: ExecutionBackend,
    stored: StoredRun,
    plan: ExecutionPlan,
    *,
    now: datetime,
) -> StoredRun:
    """Dispatch or adopt one already durable queued run during reconciliation.

    Durable run snapshots intentionally retain only a hash of the caller's idempotency key.  This
    helper therefore operates on the claimed snapshot itself, allowing startup recovery to adopt a
    provider effect without reconstructing or persisting the original secret-like request token.
    """
    _require_utc(now, label="dispatch time")
    record = stored.record
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
        raise OrchestrationContractError("durable run does not select the exact execution plan")
    if (
        record.requested_deadline_seconds is not None
        and record.requested_deadline_seconds > plan.deadline_seconds
    ):
        raise OrchestrationContractError("durable run deadline exceeds the execution plan limit")
    if record.run_state not in {HostedRunState.QUEUED, HostedRunState.RETRYING}:
        return stored

    attempt_number = record.attempt_count + 1
    if attempt_number > plan.retry_policy.max_attempts:
        raise OrchestrationContractError("run has exhausted its bounded attempt policy")
    attempt_id = attempt_identity(record.run_id, attempt_number)
    attempt = AttemptRecord(
        run_id=record.run_id,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        backend_id=plan.backend_id,
        trigger=record.trigger,
        created_at=record.updated_at,
    )
    store.append_attempt(attempt)
    handle = backend.submit_or_adopt(
        plan,
        run_id=record.run_id,
        attempt_id=attempt_id,
        trigger=record.trigger,
    )
    if handle.backend_id != plan.backend_id:
        raise OrchestrationContractError("backend returned a handle for a different backend")
    updated = transition_run(
        record,
        HostedRunState.RUNNING,
        now=now,
        attempt_count=attempt_number,
        current_attempt_id=attempt_id,
        backend_handle=handle,
        stage="submitted",
    )
    return store.save(stored, updated)


def transition_run(
    record: RunRecord,
    run_state: HostedRunState,
    *,
    now: datetime,
    outcome: RunOutcome | None = None,
    results_state: ResultsState | None = None,
    cleanup_state: CleanupState | None = None,
    stage: str | None = None,
    attempt_count: int | None = None,
    current_attempt_id: str | None = None,
    backend_handle: BackendHandle | None = None,
    result_summary: ExecutionResultSummary | None = None,
) -> RunRecord:
    """Apply one monotonic run transition while preserving independent reconciliation truth."""
    _require_utc(now, label="transition time")
    allowed = {
        HostedRunState.QUEUED: frozenset(
            {
                HostedRunState.QUEUED,
                HostedRunState.RUNNING,
                HostedRunState.CANCELING,
                HostedRunState.TERMINAL,
            }
        ),
        HostedRunState.RUNNING: frozenset(
            {
                HostedRunState.RUNNING,
                HostedRunState.RETRYING,
                HostedRunState.CANCELING,
                HostedRunState.TERMINAL,
            }
        ),
        HostedRunState.RETRYING: frozenset(
            {
                HostedRunState.RETRYING,
                HostedRunState.RUNNING,
                HostedRunState.CANCELING,
                HostedRunState.TERMINAL,
            }
        ),
        HostedRunState.CANCELING: frozenset({HostedRunState.CANCELING, HostedRunState.TERMINAL}),
        HostedRunState.TERMINAL: frozenset({HostedRunState.TERMINAL}),
    }
    if run_state not in allowed[record.run_state]:
        raise RunTransitionError(
            f"run state cannot move from {record.run_state.value} to {run_state.value}"
        )
    next_outcome = outcome or record.outcome
    next_results = results_state or record.results_state
    next_cleanup = cleanup_state or record.cleanup_state
    next_summary = result_summary or record.result_summary
    _require_monotonic_outcome(record.outcome, next_outcome)
    _require_monotonic_results(record.results_state, next_results)
    _require_monotonic_cleanup(record.cleanup_state, next_cleanup)
    if record.result_summary is not None and next_summary != record.result_summary:
        raise RunTransitionError("run execution-result summary cannot change after it is set")
    if next_results is ResultsState.AVAILABLE and next_summary is None:
        raise RunTransitionError("available run results require an execution-result summary")
    next_terminal_at = record.terminal_at
    if run_state is HostedRunState.TERMINAL and next_terminal_at is None:
        next_terminal_at = now
    updated = replace(
        record,
        run_state=run_state,
        outcome=next_outcome,
        results_state=next_results,
        cleanup_state=next_cleanup,
        updated_at=now,
        terminal_at=next_terminal_at,
        stage=stage if stage is not None else record.stage,
        attempt_count=attempt_count if attempt_count is not None else record.attempt_count,
        current_attempt_id=(
            current_attempt_id if current_attempt_id is not None else record.current_attempt_id
        ),
        backend_handle=backend_handle or record.backend_handle,
        result_summary=next_summary,
    )
    return updated


def _validate_run_dimensions(record: RunRecord) -> None:
    if record.run_state is not HostedRunState.TERMINAL:
        if record.outcome is not RunOutcome.UNKNOWN:
            raise OrchestrationContractError("non-terminal runs cannot have a terminal outcome")
        if record.results_state is not ResultsState.PENDING:
            raise OrchestrationContractError("non-terminal runs cannot have reconciled results")
        if record.cleanup_state is not CleanupState.PENDING:
            raise OrchestrationContractError("non-terminal runs cannot have reconciled cleanup")
        if record.terminal_at is not None:
            raise OrchestrationContractError("non-terminal runs cannot have a terminal time")
    elif record.terminal_at is None:
        raise OrchestrationContractError("terminal runs require a terminal time")
    if record.run_state is HostedRunState.RUNNING and record.backend_handle is None:
        raise OrchestrationContractError("running runs require a backend handle")


def _require_monotonic_outcome(previous: RunOutcome, current: RunOutcome) -> None:
    if previous is not RunOutcome.UNKNOWN and current is not previous:
        raise RunTransitionError("run outcome cannot change after it is established")


def _require_monotonic_results(previous: ResultsState, current: ResultsState) -> None:
    if previous is not ResultsState.PENDING and current is not previous:
        raise RunTransitionError("run results state cannot regress or change terminal value")


def _require_monotonic_cleanup(previous: CleanupState, current: CleanupState) -> None:
    allowed = {
        CleanupState.PENDING: frozenset(CleanupState),
        CleanupState.UNCERTAIN: frozenset({CleanupState.UNCERTAIN, CleanupState.CONFIRMED}),
        CleanupState.CONFIRMED: frozenset({CleanupState.CONFIRMED}),
    }
    if current not in allowed[previous]:
        raise RunTransitionError("run cleanup state cannot regress")


def _require_portable_id(value: str, *, label: str) -> None:
    if _PORTABLE_ID.fullmatch(value) is None:
        raise OrchestrationContractError(f"{label} identifier is malformed")


def _require_opaque_id(value: str, *, label: str) -> None:
    if _OPAQUE_ID.fullmatch(value) is None:
        raise OrchestrationContractError(f"{label} identifier is malformed")


def _require_opaque_value(value: str | None, *, label: str) -> None:
    if value is None or not value.strip() or len(value) > _MAX_OPAQUE_VALUE or "\n" in value:
        raise OrchestrationContractError(f"{label} value is missing or malformed")


def _require_utc(value: datetime, *, label: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise OrchestrationContractError(f"{label} must be an aware UTC datetime")


def _require_optional_summary(value: str | None, *, label: str) -> None:
    if value is not None and (not value.strip() or len(value) > 128 or "\n" in value):
        raise OrchestrationContractError(f"{label} is malformed")


__all__ = [
    "EXECUTION_PLAN_SCHEMA",
    "ORCHESTRATION_SCHEMA",
    "AttemptRecord",
    "BackendExecutionState",
    "BackendHandle",
    "BackendLogPage",
    "BackendLogRecord",
    "BackendObservation",
    "CleanupState",
    "ExecutionBackend",
    "ExecutionBackendError",
    "ExecutionPlan",
    "HostedRunState",
    "OrchestrationContractError",
    "ResultsState",
    "RetryPolicy",
    "RunClaim",
    "RunOutcome",
    "RunRecord",
    "RunStore",
    "RunStoreConflictError",
    "RunStoreCorruptionError",
    "RunStoreError",
    "RunStoreIdempotencyConflictError",
    "RunSubmission",
    "RunTransitionError",
    "RunTrigger",
    "StoredRun",
    "StoredRunPage",
    "TriggerKind",
    "TriggerSpec",
    "attempt_identity",
    "canonical_execution_plan_contents",
    "create_run_record",
    "dispatch_run_attempt",
    "dispatch_stored_run_attempt",
    "logical_run_identity",
    "transition_run",
    "validate_submission_plan",
]
