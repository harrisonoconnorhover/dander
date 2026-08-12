"""Idempotent OCI Container Instances lifecycle controller."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

OCI_EXECUTION_SCHEMA = "io.dander.oci-execution/v1"
_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
_RUN_ID = re.compile(r"^oci-[0-9a-f]{24}$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:+-]{1,256}$")
_IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9.-]+/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,126}$")
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class OciLifecycleError(RuntimeError):
    """The OCI controller received invalid state or could not complete an operation."""


@dataclass(frozen=True, slots=True)
class OciInstanceStatus:
    """Small provider-neutral view of a Container Instance attempt."""

    state: Literal["pending", "running", "succeeded", "failed"]
    container_id: str | None = None
    exit_code: int | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class OciExecution:
    """Sanitized durable execution record; it never contains row or secret data."""

    schema: str
    run_id: str
    pipeline_id: str
    idempotency_key: str
    image: str
    state: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    attempt: int
    max_attempts: int
    created_at: str
    updated_at: str
    deadline_at: str
    instance_id: str | None = None
    container_id: str | None = None
    exit_code: int | None = None
    failure_code: str | None = None
    replay_of: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StoredExecution:
    """One execution plus the repository compare-and-swap token."""

    execution: OciExecution
    version: str


class OciRunRepository(Protocol):
    """Atomic active-run lock and immutable terminal-history boundary."""

    def claim(self, execution: OciExecution) -> tuple[StoredExecution, bool]: ...

    def get(self, pipeline_id: str, run_id: str | None = None) -> StoredExecution | None: ...

    def save(self, stored: StoredExecution, execution: OciExecution) -> StoredExecution: ...

    def finish(self, stored: StoredExecution, execution: OciExecution) -> StoredExecution: ...

    def save_logs(self, execution: OciExecution, content: bytes) -> None: ...


class OciContainerGateway(Protocol):
    """Provider mutations and observations owned by the lifecycle controller."""

    def create(self, projection: Mapping[str, object], execution: OciExecution) -> str: ...

    def status(self, instance_id: str) -> OciInstanceStatus: ...

    def stop(self, instance_id: str) -> None: ...

    def delete(self, instance_id: str) -> None: ...

    def logs(self, container_id: str, *, limit_bytes: int) -> bytes: ...


class OciLifecycleController:
    """Launch, reconcile, retry, interrupt, and replay one OCI pipeline."""

    def __init__(
        self,
        *,
        projection: Mapping[str, object],
        repository: OciRunRepository,
        gateway: OciContainerGateway,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._projection = _validated_projection(projection)
        self._pipeline_id = str(self._projection["pipeline_id"])
        self._repository = repository
        self._gateway = gateway
        self._clock = clock or (lambda: datetime.now(UTC))

    def start(
        self,
        *,
        idempotency_key: str,
        replay_of: str | None = None,
    ) -> OciExecution:
        """Claim the pipeline atomically and create at most one first attempt."""
        if _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
            raise OciLifecycleError("OCI idempotency key contains unsupported characters")
        now = self._now()
        run_id = _run_id(self._pipeline_id, idempotency_key)
        resources = _mapping(self._projection, "resources")
        record = OciExecution(
            schema=OCI_EXECUTION_SCHEMA,
            run_id=run_id,
            pipeline_id=self._pipeline_id,
            idempotency_key=idempotency_key,
            image=str(self._projection["image"]),
            state="pending",
            attempt=1,
            max_attempts=cast("int", resources["launcher_retry_count"]) + 1,
            created_at=_timestamp(now),
            updated_at=_timestamp(now),
            deadline_at=_timestamp(
                now + timedelta(seconds=cast("int", resources["deadline_seconds"]))
            ),
            replay_of=replay_of,
        )
        stored, claimed = self._repository.claim(record)
        if not claimed:
            existing = stored.execution
            if existing.idempotency_key != idempotency_key:
                raise OciLifecycleError(
                    f"OCI pipeline {self._pipeline_id!r} already has active run {existing.run_id!r}"
                )
            return existing
        return self._launch(stored).execution

    def reconcile(self, run_id: str | None = None) -> OciExecution | None:
        """Advance one run exactly one provider observation."""
        stored = self._repository.get(self._pipeline_id, run_id)
        if stored is None:
            return None
        execution = stored.execution
        if execution.terminal:
            # A prior invocation may have persisted history but failed while releasing the active
            # Object Storage lock. Re-running finish is idempotent and repairs that partial step.
            return self._repository.finish(stored, execution).execution
        if execution.state == "pending" or execution.instance_id is None:
            return self._launch(stored).execution
        now = self._now()
        if now >= _parse_timestamp(execution.deadline_at):
            self._stop_capture_delete(execution)
            return self._finish(
                stored,
                state="failed",
                failure_code="launcher_deadline_exceeded",
            ).execution
        status = self._gateway.status(execution.instance_id)
        if status.state in {"pending", "running"}:
            updated = replace(
                execution,
                state="running",
                container_id=status.container_id or execution.container_id,
                updated_at=_timestamp(now),
            )
            return self._repository.save(stored, updated).execution
        updated = replace(
            execution,
            container_id=status.container_id or execution.container_id,
            exit_code=status.exit_code,
            updated_at=_timestamp(now),
        )
        self._capture_logs(updated)
        self._gateway.delete(execution.instance_id)
        if status.state == "succeeded" and status.exit_code == 0:
            return self._finish(stored, execution=updated, state="succeeded").execution
        if status.exit_code == 75 and execution.attempt < execution.max_attempts:
            retry = replace(
                updated,
                state="pending",
                attempt=execution.attempt + 1,
                instance_id=None,
                container_id=None,
                exit_code=None,
                failure_code=None,
                updated_at=_timestamp(now),
            )
            return self._launch(self._repository.save(stored, retry)).execution
        failure = status.failure_code or (
            "launcher_retry_exhausted" if status.exit_code == 75 else "runtime_failed"
        )
        return self._finish(
            stored,
            execution=updated,
            state="failed",
            failure_code=failure,
        ).execution

    def cancel(self, run_id: str) -> OciExecution:
        """Stop and delete one owned active instance, preserving a terminal record."""
        stored = self._repository.get(self._pipeline_id, run_id)
        if stored is None:
            raise OciLifecycleError("OCI execution was not found")
        if stored.execution.terminal:
            return stored.execution
        if stored.execution.instance_id is not None:
            self._stop_capture_delete(stored.execution)
        return self._finish(
            stored,
            state="cancelled",
            failure_code="interrupted_run",
        ).execution

    def replay(self, run_id: str, *, idempotency_key: str) -> OciExecution:
        """Start a fresh attempt chain only after the selected run is terminal."""
        previous = self._repository.get(self._pipeline_id, run_id)
        if previous is None:
            raise OciLifecycleError("OCI execution was not found")
        if not previous.execution.terminal:
            raise OciLifecycleError("Only a terminal OCI execution can be replayed")
        return self.start(idempotency_key=idempotency_key, replay_of=run_id)

    def _launch(self, stored: StoredExecution) -> StoredExecution:
        execution = stored.execution
        try:
            instance_id = self._gateway.create(self._projection, execution)
        except Exception as error:  # noqa: BLE001 - SDK errors vary by version.
            self._finish(
                stored,
                state="failed",
                failure_code="launcher_create_failed",
            )
            raise OciLifecycleError("OCI Container Instance creation failed") from error
        updated = replace(
            execution,
            state="running",
            instance_id=instance_id,
            updated_at=_timestamp(self._now()),
        )
        return self._repository.save(stored, updated)

    def _finish(
        self,
        stored: StoredExecution,
        *,
        state: Literal["succeeded", "failed", "cancelled"],
        failure_code: str | None = None,
        execution: OciExecution | None = None,
    ) -> StoredExecution:
        updated = replace(
            execution or stored.execution,
            state=state,
            failure_code=failure_code,
            updated_at=_timestamp(self._now()),
        )
        return self._repository.finish(stored, updated)

    def _capture_logs(self, execution: OciExecution) -> None:
        if execution.container_id is None:
            return
        try:
            content = self._gateway.logs(execution.container_id, limit_bytes=262_144)
        except Exception:  # noqa: BLE001 - log preservation must not mask task outcome.
            return
        self._repository.save_logs(execution, content)

    def _stop_capture_delete(self, execution: OciExecution) -> None:
        if execution.instance_id is None:
            return
        try:
            self._gateway.stop(execution.instance_id)
        finally:
            self._capture_logs(execution)
            self._gateway.delete(execution.instance_id)

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            raise OciLifecycleError("OCI lifecycle clock must be timezone-aware")
        return current.astimezone(UTC)


def _validated_projection(projection: Mapping[str, object]) -> dict[str, object]:
    required = {
        "schema",
        "contract",
        "pipeline_id",
        "profile_id",
        "launcher",
        "image",
        "command",
        "configuration_reference",
        "environment",
        "secret_bindings",
        "workload_identity",
        "resources",
        "schedule",
        "network",
        "labels",
        "observability",
        "extensions",
    }
    if set(projection) != required:
        raise OciLifecycleError("OCI execution projection is incomplete")
    if (
        projection.get("schema") != "io.dander.execution/v1"
        or projection.get("launcher") != "oci_container_instances"
    ):
        raise OciLifecycleError("OCI execution projection has an unsupported contract")
    pipeline_id = projection.get("pipeline_id")
    image = projection.get("image")
    command = projection.get("command")
    if (
        not isinstance(pipeline_id, str)
        or _IDENTIFIER.fullmatch(pipeline_id) is None
        or not isinstance(image, str)
        or _IMMUTABLE_IMAGE.fullmatch(image) is None
        or not isinstance(command, list)
        or not 1 <= len(command) <= 64
        or any(not isinstance(part, str) or not part or len(part) > 256 for part in command)
    ):
        raise OciLifecycleError("OCI execution projection identity or command is invalid")
    if command[:2] != ["runtime", "execute"]:
        raise OciLifecycleError("OCI execution projection command is unsupported")
    environment = _mapping(projection, "environment")
    secret_bindings = _mapping(projection, "secret_bindings")
    if len(environment) > 128 or any(
        not isinstance(name, str)
        or _ENVIRONMENT_NAME.fullmatch(name) is None
        or not isinstance(value, str)
        or len(value) > 2_048
        for name, value in environment.items()
    ):
        raise OciLifecycleError("OCI execution projection environment is invalid")
    if len(secret_bindings) > 128 or set(environment) & set(secret_bindings):
        raise OciLifecycleError("OCI execution projection secret bindings are invalid")
    for name, binding in secret_bindings.items():
        if (
            not isinstance(name, str)
            or _ENVIRONMENT_NAME.fullmatch(name) is None
            or not isinstance(binding, dict)
            or set(binding) != {"provider", "reference"}
            or binding.get("provider") != "oci_vault"
            or not isinstance(binding.get("reference"), str)
            or not str(binding["reference"]).startswith("oci-vault://")
        ):
            raise OciLifecycleError("OCI execution projection secret bindings are invalid")
    resources = _mapping(projection, "resources")
    deadline = resources.get("deadline_seconds")
    retries = resources.get("launcher_retry_count")
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, int)
        or not 1 <= deadline <= 3_300
        or isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 10
        or resources.get("runtime_retry_count") != 0
    ):
        raise OciLifecycleError(
            "OCI controller requires a 1-3300 second deadline and 0-10 launcher retries"
        )
    return dict(projection)


def _mapping(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise OciLifecycleError(f"OCI execution projection {key} is invalid")
    return value


def _run_id(pipeline_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{pipeline_id}\0{idempotency_key}".encode()).hexdigest()[:24]
    return f"oci-{digest}"


def execution_run_id(pipeline_id: str, idempotency_key: str) -> str:
    """Return the controller identity a detached caller can know before execution starts."""
    return _run_id(pipeline_id, idempotency_key)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OciLifecycleError("OCI execution timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise OciLifecycleError("OCI execution timestamp must include a timezone")
    return parsed.astimezone(UTC)


def execution_from_json(content: bytes) -> OciExecution:
    """Parse one repository record without accepting unknown or secret-bearing fields."""
    try:
        document = json.loads(content)
        if not isinstance(document, dict):
            raise TypeError
        execution = OciExecution(**document)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise OciLifecycleError("OCI execution record is invalid") from error
    integer_values = (execution.attempt, execution.max_attempts)
    if (
        execution.schema != OCI_EXECUTION_SCHEMA
        or _RUN_ID.fullmatch(execution.run_id) is None
        or _IDENTIFIER.fullmatch(execution.pipeline_id) is None
        or _IDEMPOTENCY_KEY.fullmatch(execution.idempotency_key) is None
        or _IMMUTABLE_IMAGE.fullmatch(execution.image) is None
        or execution.state not in _TERMINAL_STATES | {"pending", "running"}
        or any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values)
        or not 1 <= execution.attempt <= execution.max_attempts <= 11
        or (
            execution.exit_code is not None
            and (isinstance(execution.exit_code, bool) or not isinstance(execution.exit_code, int))
        )
        or (
            execution.failure_code is not None
            and _FAILURE_CODE.fullmatch(execution.failure_code) is None
        )
        or (execution.replay_of is not None and _RUN_ID.fullmatch(execution.replay_of) is None)
    ):
        raise OciLifecycleError("OCI execution record has an unsupported contract")
    created = _parse_timestamp(execution.created_at)
    _parse_timestamp(execution.updated_at)
    if _parse_timestamp(execution.deadline_at) <= created:
        raise OciLifecycleError("OCI execution record has an invalid deadline")
    return execution


__all__ = [
    "OCI_EXECUTION_SCHEMA",
    "OciContainerGateway",
    "OciExecution",
    "OciInstanceStatus",
    "OciLifecycleController",
    "OciLifecycleError",
    "OciRunRepository",
    "StoredExecution",
    "execution_from_json",
    "execution_run_id",
]
