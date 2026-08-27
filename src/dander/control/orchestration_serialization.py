"""Versioned canonical JSON codecs for durable Control orchestration records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Literal, cast

from dander.control.orchestration import (
    EXECUTION_PLAN_SCHEMA,
    EXECUTION_RESULT_SUMMARY_SCHEMA,
    AttemptRecord,
    BackendHandle,
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
    RunTrigger,
    ScheduleWakeup,
    TriggerKind,
    TriggerSpec,
    canonical_execution_plan_contents,
)
from dander.deployment.projection import (
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
    SecretReference,
)

RUN_RECORD_SCHEMA_V1 = "io.dander.control.run-record/v1"
RUN_RECORD_SCHEMA_V2 = "io.dander.control.run-record/v2"
RUN_RECORD_SCHEMA = "io.dander.control.run-record/v3"
ATTEMPT_RECORD_SCHEMA = "io.dander.control.attempt-record/v1"
TRIGGER_SPEC_SCHEMA = "io.dander.control.trigger-spec/v1"
SCHEDULE_WAKEUP_SCHEMA = "io.dander.control.schedule-wakeup/v1"
SCHEDULED_TIME_TOKEN = "<aws.scheduler.scheduled-time>"

_MAX_PLAN_BYTES = 1024 * 1024
_MAX_RUN_BYTES = 256 * 1024
_MAX_RESULT_SUMMARY_BYTES = 16 * 1024
_MAX_ATTEMPT_BYTES = 128 * 1024
_MAX_TRIGGER_BYTES = 64 * 1024
_MAX_WAKEUP_BYTES = 8 * 1024
type _SecretProvider = Literal[
    "environment",
    "gcp_secret_manager",
    "aws_secret_manager",
    "azure_key_vault",
    "oci_vault",
]


class OrchestrationSerializationError(OrchestrationContractError):
    """A durable orchestration record is unsupported, invalid, or non-canonical."""


def serialize_execution_plan(plan: ExecutionPlan) -> bytes:
    """Return canonical versioned bytes with the computed plan revision."""
    contents = _mapping(json.loads(canonical_execution_plan_contents(plan)), "plan contents")
    return _canonical_json(
        {
            "schema": EXECUTION_PLAN_SCHEMA,
            "revision": plan.revision,
            "plan": contents["plan"],
        }
    )


def deserialize_execution_plan(data: bytes) -> ExecutionPlan:
    """Load a canonical plan and reject a stored revision that does not match its contents."""
    try:
        envelope = _load_envelope(data, EXECUTION_PLAN_SCHEMA, _MAX_PLAN_BYTES)
        values = _mapping(envelope["plan"], "execution plan")
        plan = ExecutionPlan(
            plan_id=_string(values["plan_id"], "plan_id"),
            environment=_string(values["environment"], "environment"),
            project=_string(values["project"], "project"),
            graph=_string(values["graph"], "graph"),
            graph_revision=_string(values["graph_revision"], "graph_revision"),
            graph_content_sha256=_string(values["graph_content_sha256"], "graph_content_sha256"),
            backend_id=_string(values["backend_id"], "backend_id"),
            profile_id=_string(values["profile_id"], "profile_id"),
            image=_string(values["image"], "image"),
            execution_template=_execution_template(values["execution_template"]),
            deadline_seconds=_integer(values["deadline_seconds"], "deadline_seconds"),
            retry_policy=_retry_policy(values["retry_policy"]),
            schema=_string(values["orchestration_schema"], "orchestration_schema"),
        )
        stored_revision = _string(envelope["revision"], "revision")
        if stored_revision != plan.revision:
            raise OrchestrationSerializationError(
                "execution-plan revision does not match its canonical contents"
            )
        _require_canonical(data, serialize_execution_plan(plan))
        return plan
    except OrchestrationSerializationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise OrchestrationSerializationError("execution-plan record is invalid") from error


def serialize_run_record(record: RunRecord) -> bytes:
    """Return canonical versioned bytes for one mutable run snapshot."""
    return _canonical_json(
        {
            "schema": RUN_RECORD_SCHEMA,
            "record": _run_record_payload(
                record,
                include_result_summary=True,
                include_placement_decision=True,
            ),
        }
    )


def deserialize_run_record(data: bytes) -> RunRecord:
    """Load one canonical versioned run snapshot."""
    try:
        envelope = _load_any_envelope(
            data,
            frozenset({RUN_RECORD_SCHEMA_V1, RUN_RECORD_SCHEMA_V2, RUN_RECORD_SCHEMA}),
            _MAX_RUN_BYTES,
        )
        schema = _string(envelope["schema"], "run record schema")
        values = _mapping(envelope["record"], "run record")
        record = RunRecord(
            run_id=_string(values["run_id"], "run_id"),
            environment=_string(values["environment"], "environment"),
            project=_string(values["project"], "project"),
            graph=_string(values["graph"], "graph"),
            graph_revision=_string(values["graph_revision"], "graph_revision"),
            graph_content_sha256=_string(values["graph_content_sha256"], "graph_content_sha256"),
            plan_id=_string(values["plan_id"], "plan_id"),
            plan_revision=_string(values["plan_revision"], "plan_revision"),
            trigger=_trigger(values["trigger"]),
            idempotency_key_sha256=_string(
                values["idempotency_key_sha256"], "idempotency_key_sha256"
            ),
            submission_sha256=_string(values["submission_sha256"], "submission_sha256"),
            requested_at=_datetime(values["requested_at"], "requested_at"),
            requested_deadline_seconds=_optional_integer(
                values["requested_deadline_seconds"], "requested_deadline_seconds"
            ),
            run_state=_enum(HostedRunState, values["run_state"], "run_state"),
            outcome=_enum(RunOutcome, values["outcome"], "outcome"),
            results_state=_enum(ResultsState, values["results_state"], "results_state"),
            cleanup_state=_enum(CleanupState, values["cleanup_state"], "cleanup_state"),
            created_at=_datetime(values["created_at"], "created_at"),
            updated_at=_datetime(values["updated_at"], "updated_at"),
            terminal_at=_optional_datetime(values["terminal_at"], "terminal_at"),
            stage=_optional_string(values["stage"], "stage"),
            attempt_count=_integer(values["attempt_count"], "attempt_count"),
            current_attempt_id=_optional_string(values["current_attempt_id"], "current_attempt_id"),
            backend_handle=_backend_handle(values["backend_handle"]),
            result_summary=(
                _deserialize_execution_result_summary_value(values["result_summary"])
                if schema in {RUN_RECORD_SCHEMA_V2, RUN_RECORD_SCHEMA}
                and values["result_summary"] is not None
                else None
            ),
            placement_decision=(
                _placement_decision(values["placement_decision"])
                if schema == RUN_RECORD_SCHEMA and values["placement_decision"] is not None
                else None
            ),
        )
        expected = {
            RUN_RECORD_SCHEMA: serialize_run_record,
            RUN_RECORD_SCHEMA_V2: _serialize_run_record_v2,
            RUN_RECORD_SCHEMA_V1: _serialize_run_record_v1,
        }[schema](record)
        _require_canonical(data, expected)
        return record
    except OrchestrationSerializationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise OrchestrationSerializationError("run record is invalid") from error


def serialize_execution_result_summary(summary: ExecutionResultSummary) -> bytes:
    """Return canonical versioned bytes for fixed-size runtime result aggregates."""
    return _canonical_json(
        {
            "schema": EXECUTION_RESULT_SUMMARY_SCHEMA,
            "summary": _execution_result_summary_payload(summary),
        }
    )


def deserialize_execution_result_summary(data: bytes) -> ExecutionResultSummary:
    """Load one canonical fixed-size runtime result summary."""
    try:
        envelope = _load_envelope(
            data,
            EXECUTION_RESULT_SUMMARY_SCHEMA,
            _MAX_RESULT_SUMMARY_BYTES,
        )
        summary = _execution_result_summary(envelope["summary"])
        _require_canonical(data, serialize_execution_result_summary(summary))
        return summary
    except OrchestrationSerializationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise OrchestrationSerializationError("execution-result summary is invalid") from error


def serialize_attempt_record(record: AttemptRecord) -> bytes:
    """Return canonical versioned bytes for one immutable attempt intent."""
    return _canonical_json(
        {
            "schema": ATTEMPT_RECORD_SCHEMA,
            "record": {
                "run_id": record.run_id,
                "attempt_id": record.attempt_id,
                "attempt_number": record.attempt_number,
                "plan_id": record.plan_id,
                "plan_revision": record.plan_revision,
                "backend_id": record.backend_id,
                "trigger": _trigger_payload(record.trigger),
                "created_at": _timestamp(record.created_at),
            },
        }
    )


def deserialize_attempt_record(data: bytes) -> AttemptRecord:
    """Load one canonical versioned attempt intent."""
    try:
        envelope = _load_envelope(data, ATTEMPT_RECORD_SCHEMA, _MAX_ATTEMPT_BYTES)
        values = _mapping(envelope["record"], "attempt record")
        record = AttemptRecord(
            run_id=_string(values["run_id"], "run_id"),
            attempt_id=_string(values["attempt_id"], "attempt_id"),
            attempt_number=_integer(values["attempt_number"], "attempt_number"),
            plan_id=_string(values["plan_id"], "plan_id"),
            plan_revision=_string(values["plan_revision"], "plan_revision"),
            backend_id=_string(values["backend_id"], "backend_id"),
            trigger=_trigger(values["trigger"]),
            created_at=_datetime(values["created_at"], "created_at"),
        )
        _require_canonical(data, serialize_attempt_record(record))
        return record
    except OrchestrationSerializationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise OrchestrationSerializationError("attempt record is invalid") from error


def serialize_trigger_spec(spec: TriggerSpec) -> bytes:
    """Return canonical versioned bytes for one independently managed trigger."""
    return _canonical_json(
        {
            "schema": TRIGGER_SPEC_SCHEMA,
            "trigger": {
                "trigger_id": spec.trigger_id,
                "kind": spec.kind.value,
                "plan_id": spec.plan_id,
                "plan_revision": spec.plan_revision,
                "enabled": spec.enabled,
                "schedule": spec.schedule,
                "time_zone": spec.time_zone,
                "dependency": spec.dependency,
            },
        }
    )


def deserialize_trigger_spec(data: bytes) -> TriggerSpec:
    """Load one canonical trigger without coupling its revision to plan contents."""
    try:
        envelope = _load_envelope(data, TRIGGER_SPEC_SCHEMA, _MAX_TRIGGER_BYTES)
        values = _mapping(envelope["trigger"], "trigger spec")
        spec = TriggerSpec(
            trigger_id=_string(values["trigger_id"], "trigger_id"),
            kind=_enum(TriggerKind, values["kind"], "trigger kind"),
            plan_id=_string(values["plan_id"], "plan_id"),
            plan_revision=_string(values["plan_revision"], "plan_revision"),
            enabled=_boolean(values["enabled"], "enabled"),
            schedule=_optional_string(values["schedule"], "schedule"),
            time_zone=_optional_string(values["time_zone"], "time_zone"),
            dependency=_optional_string(values["dependency"], "dependency"),
        )
        _require_canonical(data, serialize_trigger_spec(spec))
        return spec
    except OrchestrationSerializationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise OrchestrationSerializationError("trigger-spec record is invalid") from error


def serialize_schedule_wakeup(wakeup: ScheduleWakeup) -> bytes:
    """Return the canonical queue body for one exact scheduled occurrence."""
    return _canonical_json(
        {
            "schema": SCHEDULE_WAKEUP_SCHEMA,
            "trigger_id": wakeup.trigger_id,
            "plan_revision": wakeup.plan_revision,
            "scheduled_occurrence": _timestamp(wakeup.scheduled_occurrence),
        }
    )


def deserialize_schedule_wakeup(data: bytes) -> ScheduleWakeup:
    """Load a canonical scheduled occurrence delivered by a provider queue."""
    try:
        envelope = _load_envelope(data, SCHEDULE_WAKEUP_SCHEMA, _MAX_WAKEUP_BYTES)
        wakeup = ScheduleWakeup(
            trigger_id=_string(envelope["trigger_id"], "trigger_id"),
            plan_revision=_string(envelope["plan_revision"], "plan_revision"),
            scheduled_occurrence=_datetime(
                envelope["scheduled_occurrence"], "scheduled_occurrence"
            ),
        )
        _require_canonical(data, serialize_schedule_wakeup(wakeup))
        return wakeup
    except OrchestrationSerializationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise OrchestrationSerializationError("schedule-wakeup record is invalid") from error


def render_schedule_wakeup_template(spec: TriggerSpec) -> str:
    """Render a canonical Scheduler input with only its occurrence token unresolved."""
    if spec.kind is not TriggerKind.SCHEDULE:
        raise OrchestrationSerializationError("only scheduled triggers can render wakeups")
    return _canonical_json(
        {
            "schema": SCHEDULE_WAKEUP_SCHEMA,
            "trigger_id": spec.trigger_id,
            "plan_revision": spec.plan_revision,
            "scheduled_occurrence": SCHEDULED_TIME_TOKEN,
        }
    ).decode("utf-8")


def _execution_template(value: object) -> ExecutionTemplate:
    values = _mapping(value, "execution_template")
    resources = _mapping(values["resources"], "resources")
    schedule = _mapping(values["schedule"], "schedule")
    network = _mapping(values["network"], "network")
    observability = _mapping(values["observability"], "observability")
    secrets = _mapping(values["secret_bindings"], "secret_bindings")
    secret_bindings = tuple(
        (
            name,
            SecretReference(
                provider=cast(
                    "_SecretProvider",
                    _string(_mapping(reference, "secret reference")["provider"], "provider"),
                ),
                reference=_string(
                    _mapping(reference, "secret reference")["reference"], "reference"
                ),
            ),
        )
        for name, reference in sorted(secrets.items())
        if isinstance(name, str)
    )
    if len(secret_bindings) != len(secrets):
        raise OrchestrationSerializationError("secret binding names must be strings")
    return ExecutionTemplate(
        schema=_string(values["schema"], "template schema"),
        contract=_string(values["contract"], "runtime contract"),
        pipeline_id=_string(values["pipeline_id"], "pipeline_id"),
        profile_id=_string(values["profile_id"], "profile_id"),
        launcher=_string(values["launcher"], "launcher"),
        image=_string(values["image"], "image"),
        command=_string_tuple(values["command"], "command"),
        configuration_reference=_string(
            values["configuration_reference"], "configuration_reference"
        ),
        environment=_string_pairs(values["environment"], "environment"),
        secret_bindings=secret_bindings,
        workload_identity=_string(values["workload_identity"], "workload_identity"),
        resources=ResourceProjection(
            cpu_millis=_integer(resources["cpu_millis"], "cpu_millis"),
            memory_mib=_integer(resources["memory_mib"], "memory_mib"),
            ephemeral_storage_mib=_optional_integer(
                resources["ephemeral_storage_mib"], "ephemeral_storage_mib"
            ),
            deadline_seconds=_integer(resources["deadline_seconds"], "deadline_seconds"),
            runtime_retry_count=_integer(resources["runtime_retry_count"], "runtime_retry_count"),
            launcher_retry_count=_integer(
                resources["launcher_retry_count"], "launcher_retry_count"
            ),
        ),
        schedule=ScheduleProjection(
            task_count=_integer(schedule["task_count"], "task_count"),
            maximum_parallelism=_integer(schedule["maximum_parallelism"], "maximum_parallelism"),
            expression=_optional_string(schedule["expression"], "expression"),
            time_zone=_optional_string(schedule["time_zone"], "time_zone"),
            paused=_boolean(schedule["paused"], "paused"),
        ),
        network=NetworkPlacement(
            placement=_optional_string(network["placement"], "placement"),
            extensions=_string_pairs(network["extensions"], "network extensions"),
        ),
        labels=_string_pairs(values["labels"], "labels"),
        observability=ObservabilityProjection(
            log_destination=_string(observability["log_destination"], "log_destination"),
            metric_namespace=_string(observability["metric_namespace"], "metric_namespace"),
            alert_target=_optional_string(observability["alert_target"], "alert_target"),
            retention_days=_optional_integer(observability["retention_days"], "retention_days"),
        ),
        extensions=_string_pairs(values["extensions"], "extensions"),
    )


def _retry_policy(value: object) -> RetryPolicy:
    values = _mapping(value, "retry_policy")
    codes = values["retryable_exit_codes"]
    if not isinstance(codes, list):
        raise OrchestrationSerializationError("retryable_exit_codes must be a list")
    return RetryPolicy(
        max_attempts=_integer(values["max_attempts"], "max_attempts"),
        retryable_exit_codes=tuple(_integer(code, "retryable_exit_code") for code in codes),
    )


def _serialize_run_record_v1(record: RunRecord) -> bytes:
    return _canonical_json(
        {
            "schema": RUN_RECORD_SCHEMA_V1,
            "record": _run_record_payload(
                record,
                include_result_summary=False,
                include_placement_decision=False,
            ),
        }
    )


def _serialize_run_record_v2(record: RunRecord) -> bytes:
    return _canonical_json(
        {
            "schema": RUN_RECORD_SCHEMA_V2,
            "record": _run_record_payload(
                record,
                include_result_summary=True,
                include_placement_decision=False,
            ),
        }
    )


def _run_record_payload(
    record: RunRecord,
    *,
    include_result_summary: bool,
    include_placement_decision: bool,
) -> dict[str, object]:
    handle = record.backend_handle
    payload: dict[str, object] = {
        "run_id": record.run_id,
        "environment": record.environment,
        "project": record.project,
        "graph": record.graph,
        "graph_revision": record.graph_revision,
        "graph_content_sha256": record.graph_content_sha256,
        "plan_id": record.plan_id,
        "plan_revision": record.plan_revision,
        "trigger": _trigger_payload(record.trigger),
        "idempotency_key_sha256": record.idempotency_key_sha256,
        "submission_sha256": record.submission_sha256,
        "requested_at": _timestamp(record.requested_at),
        "requested_deadline_seconds": record.requested_deadline_seconds,
        "run_state": record.run_state.value,
        "outcome": record.outcome.value,
        "results_state": record.results_state.value,
        "cleanup_state": record.cleanup_state.value,
        "created_at": _timestamp(record.created_at),
        "updated_at": _timestamp(record.updated_at),
        "terminal_at": _timestamp(record.terminal_at) if record.terminal_at else None,
        "stage": record.stage,
        "attempt_count": record.attempt_count,
        "current_attempt_id": record.current_attempt_id,
        "backend_handle": (
            {
                "backend_id": handle.backend_id,
                "execution_id": handle.execution_id,
            }
            if handle is not None
            else None
        ),
    }
    if include_result_summary:
        payload["result_summary"] = (
            json.loads(serialize_execution_result_summary(record.result_summary))
            if record.result_summary is not None
            else None
        )
    if include_placement_decision:
        payload["placement_decision"] = (
            _placement_decision_payload(record.placement_decision)
            if record.placement_decision is not None
            else None
        )
    return payload


def _placement_decision_payload(decision: PlacementDecision) -> dict[str, object]:
    return {
        "schema": decision.schema,
        "mode": decision.mode.value,
        "selected_environment": decision.selected_environment,
        "selected_locality": decision.selected_locality,
        "estimated_cost_microusd": decision.estimated_cost_microusd,
        "preferred_locality": decision.preferred_locality,
        "max_cost_microusd": decision.max_cost_microusd,
        "eligible_plan_count": decision.eligible_plan_count,
    }


def _placement_decision(value: object) -> PlacementDecision:
    values = _mapping(value, "placement decision")
    return PlacementDecision(
        schema=_string(values["schema"], "placement schema"),
        mode=_enum(PlacementMode, values["mode"], "placement mode"),
        selected_environment=_string(values["selected_environment"], "selected_environment"),
        selected_locality=_optional_string(values["selected_locality"], "selected_locality"),
        estimated_cost_microusd=_optional_integer(
            values["estimated_cost_microusd"], "estimated_cost_microusd"
        ),
        preferred_locality=_optional_string(values["preferred_locality"], "preferred_locality"),
        max_cost_microusd=_optional_integer(values["max_cost_microusd"], "max_cost_microusd"),
        eligible_plan_count=_integer(values["eligible_plan_count"], "eligible_plan_count"),
    )


def _execution_result_summary_payload(summary: ExecutionResultSummary) -> dict[str, object]:
    return {
        "endpoints": summary.endpoints,
        "extracted_rows": summary.extracted_rows,
        "affected_rows": summary.affected_rows,
        "models": summary.models,
        "assertions": summary.assertions,
        "assets": summary.assets,
        "duration_ms": summary.duration_ms,
        "operation_count": summary.operation_count,
        "retry_count": summary.retry_count,
        "rows_read": summary.rows_read,
        "rows_written": summary.rows_written,
        "rows_affected": summary.rows_affected,
        "bytes_read": summary.bytes_read,
        "bytes_written": summary.bytes_written,
        "bytes_processed": summary.bytes_processed,
        "bytes_billed": summary.bytes_billed,
        "queue_duration_ms": summary.queue_duration_ms,
        "execution_duration_ms": summary.execution_duration_ms,
        "spill_bytes": summary.spill_bytes,
        "skipped": summary.skipped,
    }


def _execution_result_summary(value: object) -> ExecutionResultSummary:
    values = _mapping(value, "execution-result summary")
    return ExecutionResultSummary(
        endpoints=_integer(values["endpoints"], "endpoints"),
        extracted_rows=_integer(values["extracted_rows"], "extracted_rows"),
        affected_rows=_integer(values["affected_rows"], "affected_rows"),
        models=_integer(values["models"], "models"),
        assertions=_integer(values["assertions"], "assertions"),
        assets=_integer(values["assets"], "assets"),
        duration_ms=_integer(values["duration_ms"], "duration_ms"),
        operation_count=_integer(values["operation_count"], "operation_count"),
        retry_count=_integer(values["retry_count"], "retry_count"),
        rows_read=_integer(values["rows_read"], "rows_read"),
        rows_written=_integer(values["rows_written"], "rows_written"),
        rows_affected=_integer(values["rows_affected"], "rows_affected"),
        bytes_read=_integer(values["bytes_read"], "bytes_read"),
        bytes_written=_integer(values["bytes_written"], "bytes_written"),
        bytes_processed=_integer(values["bytes_processed"], "bytes_processed"),
        bytes_billed=_integer(values["bytes_billed"], "bytes_billed"),
        queue_duration_ms=_integer(values["queue_duration_ms"], "queue_duration_ms"),
        execution_duration_ms=_integer(values["execution_duration_ms"], "execution_duration_ms"),
        spill_bytes=_integer(values["spill_bytes"], "spill_bytes"),
        skipped=_boolean(values["skipped"], "skipped"),
    )


def _deserialize_execution_result_summary_value(value: object) -> ExecutionResultSummary:
    return deserialize_execution_result_summary(_canonical_json(value))


def _trigger_payload(trigger: RunTrigger) -> dict[str, object]:
    return {
        "kind": trigger.kind.value,
        "trigger_id": trigger.trigger_id,
        "scheduled_occurrence": (
            _timestamp(trigger.scheduled_occurrence) if trigger.scheduled_occurrence else None
        ),
        "replay_of_run_id": trigger.replay_of_run_id,
    }


def _trigger(value: object) -> RunTrigger:
    values = _mapping(value, "trigger")
    return RunTrigger(
        kind=_enum(TriggerKind, values["kind"], "trigger kind"),
        trigger_id=_string(values["trigger_id"], "trigger_id"),
        scheduled_occurrence=_optional_datetime(
            values["scheduled_occurrence"], "scheduled_occurrence"
        ),
        replay_of_run_id=_optional_string(values["replay_of_run_id"], "replay_of_run_id"),
    )


def _backend_handle(value: object) -> BackendHandle | None:
    if value is None:
        return None
    values = _mapping(value, "backend_handle")
    return BackendHandle(
        backend_id=_string(values["backend_id"], "backend_id"),
        execution_id=_string(values["execution_id"], "execution_id"),
    )


def _load_envelope(data: bytes, schema: str, max_bytes: int) -> Mapping[str, object]:
    return _load_any_envelope(data, frozenset({schema}), max_bytes)


def _load_any_envelope(
    data: bytes,
    schemas: frozenset[str],
    max_bytes: int,
) -> Mapping[str, object]:
    if not isinstance(data, bytes) or not data or len(data) > max_bytes:
        raise OrchestrationSerializationError("orchestration record size is invalid")
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OrchestrationSerializationError("orchestration record is not valid JSON") from error
    envelope = _mapping(value, "orchestration envelope")
    if envelope.get("schema") not in schemas:
        raise OrchestrationSerializationError("unsupported orchestration record schema")
    return envelope


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _require_canonical(received: bytes, expected: bytes) -> None:
    if received != expected:
        raise OrchestrationSerializationError("orchestration record is not canonical")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise OrchestrationSerializationError(f"{label} must be an object")
    return cast("Mapping[str, object]", value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise OrchestrationSerializationError(f"{label} must be a string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OrchestrationSerializationError(f"{label} must be an integer")
    return value


def _optional_integer(value: object, label: str) -> int | None:
    return None if value is None else _integer(value, label)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise OrchestrationSerializationError(f"{label} must be a boolean")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise OrchestrationSerializationError(f"{label} must be a list")
    return tuple(_string(item, label) for item in value)


def _string_pairs(value: object, label: str) -> tuple[tuple[str, str], ...]:
    values = _mapping(value, label)
    if not all(isinstance(name, str) for name in values):
        raise OrchestrationSerializationError(f"{label} names must be strings")
    return tuple(sorted((name, _string(item, label)) for name, item in values.items()))


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _datetime(value: object, label: str) -> datetime:
    raw = _string(value, label)
    if not raw.endswith("Z"):
        raise OrchestrationSerializationError(f"{label} must use canonical UTC notation")
    try:
        return datetime.fromisoformat(f"{raw[:-1]}+00:00")
    except ValueError as error:
        raise OrchestrationSerializationError(f"{label} is invalid") from error


def _optional_datetime(value: object, label: str) -> datetime | None:
    return None if value is None else _datetime(value, label)


def _enum[EnumT: StrEnum](enum_type: type[EnumT], value: object, label: str) -> EnumT:
    try:
        return enum_type(_string(value, label))
    except ValueError as error:
        raise OrchestrationSerializationError(f"{label} is unsupported") from error


__all__ = [
    "ATTEMPT_RECORD_SCHEMA",
    "EXECUTION_RESULT_SUMMARY_SCHEMA",
    "RUN_RECORD_SCHEMA",
    "RUN_RECORD_SCHEMA_V1",
    "SCHEDULED_TIME_TOKEN",
    "SCHEDULE_WAKEUP_SCHEMA",
    "TRIGGER_SPEC_SCHEMA",
    "OrchestrationSerializationError",
    "deserialize_attempt_record",
    "deserialize_execution_plan",
    "deserialize_execution_result_summary",
    "deserialize_run_record",
    "deserialize_schedule_wakeup",
    "deserialize_trigger_spec",
    "render_schedule_wakeup_template",
    "serialize_attempt_record",
    "serialize_execution_plan",
    "serialize_execution_result_summary",
    "serialize_run_record",
    "serialize_schedule_wakeup",
    "serialize_trigger_spec",
]
