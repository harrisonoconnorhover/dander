"""Versioned canonical JSON codecs for durable Control orchestration records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Literal, cast

from dander.control.orchestration import (
    EXECUTION_PLAN_SCHEMA,
    AttemptRecord,
    BackendHandle,
    CleanupState,
    ExecutionPlan,
    HostedRunState,
    OrchestrationContractError,
    ResultsState,
    RetryPolicy,
    RunOutcome,
    RunRecord,
    RunTrigger,
    TriggerKind,
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

RUN_RECORD_SCHEMA = "io.dander.control.run-record/v1"
ATTEMPT_RECORD_SCHEMA = "io.dander.control.attempt-record/v1"

_MAX_PLAN_BYTES = 1024 * 1024
_MAX_RUN_BYTES = 256 * 1024
_MAX_ATTEMPT_BYTES = 128 * 1024
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
    trigger = _trigger_payload(record.trigger)
    handle = record.backend_handle
    return _canonical_json(
        {
            "schema": RUN_RECORD_SCHEMA,
            "record": {
                "run_id": record.run_id,
                "environment": record.environment,
                "project": record.project,
                "graph": record.graph,
                "graph_revision": record.graph_revision,
                "graph_content_sha256": record.graph_content_sha256,
                "plan_id": record.plan_id,
                "plan_revision": record.plan_revision,
                "trigger": trigger,
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
            },
        }
    )


def deserialize_run_record(data: bytes) -> RunRecord:
    """Load one canonical versioned run snapshot."""
    try:
        envelope = _load_envelope(data, RUN_RECORD_SCHEMA, _MAX_RUN_BYTES)
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
        )
        _require_canonical(data, serialize_run_record(record))
        return record
    except OrchestrationSerializationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise OrchestrationSerializationError("run record is invalid") from error


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
    if not isinstance(data, bytes) or not data or len(data) > max_bytes:
        raise OrchestrationSerializationError("orchestration record size is invalid")
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OrchestrationSerializationError("orchestration record is not valid JSON") from error
    envelope = _mapping(value, "orchestration envelope")
    if envelope.get("schema") != schema:
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
    "RUN_RECORD_SCHEMA",
    "OrchestrationSerializationError",
    "deserialize_attempt_record",
    "deserialize_execution_plan",
    "deserialize_run_record",
    "serialize_attempt_record",
    "serialize_execution_plan",
    "serialize_run_record",
]
