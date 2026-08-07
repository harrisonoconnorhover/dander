"""Versioned, launcher-neutral contract for the Dander OCI runtime."""

from __future__ import annotations

import json
import os
import re
import signal
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from types import FrameType

    from dander.executor import PipelineExecutionResult

RUNTIME_CONTRACT = "io.dander.runtime/v1"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_LAUNCHER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_OPAQUE_LENGTH = 256


class RuntimeExitCode(IntEnum):
    """Stable process outcomes consumed by launchers."""

    SUCCESS = 0
    PERMANENT_FAILURE = 1
    INVALID_INVOCATION = 2
    RETRYABLE_FAILURE = 75
    CANCELLED = 130


class RuntimeContractError(ValueError):
    """Raised when a launcher supplies an invalid runtime-contract value."""


class RuntimeCancelledError(RuntimeError):
    """Raised after a catchable process signal requests graceful cancellation."""

    def __init__(self, signal_name: str) -> None:
        super().__init__("runtime cancellation requested")
        self.signal_name = signal_name


@dataclass(frozen=True, slots=True)
class LauncherContext:
    """Validated, non-secret launcher correlation supplied to one execution."""

    run_id: str
    launcher: str
    execution_id: str | None
    attempt: int
    shard_index: int
    shard_count: int
    deadline_at: str | None
    principal: str | None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> LauncherContext:
        """Resolve Dander variables with Cloud Run's native context as a compatibility source."""
        values = os.environ if environment is None else environment
        launcher = values.get("DANDER_LAUNCHER") or (
            "cloud_run" if values.get("CLOUD_RUN_EXECUTION") else "local"
        )
        execution_id = values.get("DANDER_LAUNCHER_EXECUTION_ID") or values.get(
            "CLOUD_RUN_EXECUTION"
        )
        run_id = values.get("DANDER_RUN_ID") or execution_id or uuid4().hex
        attempt_value = values.get("DANDER_ATTEMPT")
        if attempt_value is None:
            cloud_run_attempt = values.get("CLOUD_RUN_TASK_ATTEMPT")
            attempt_value = (
                str(
                    _bounded_integer(
                        cloud_run_attempt,
                        label="Cloud Run task attempt",
                        minimum=0,
                        maximum=999,
                    )
                    + 1
                )
                if cloud_run_attempt is not None
                else "1"
            )
        shard_index_value = (
            values.get("DANDER_SHARD_INDEX") or values.get("CLOUD_RUN_TASK_INDEX") or "0"
        )
        shard_count_value = (
            values.get("DANDER_SHARD_COUNT") or values.get("CLOUD_RUN_TASK_COUNT") or "1"
        )
        deadline_at = values.get("DANDER_DEADLINE_AT")
        principal = values.get("DANDER_PRINCIPAL")

        _require_identifier(run_id, label="run id")
        if not _SAFE_LAUNCHER.fullmatch(launcher):
            raise RuntimeContractError(
                "launcher must be 1-64 characters using letters, numbers, '.', '_', or '-'"
            )
        _require_opaque(execution_id, label="launcher execution id")
        _require_opaque(principal, label="principal")
        attempt = _bounded_integer(attempt_value, label="attempt", minimum=1, maximum=1000)
        shard_index = _bounded_integer(
            shard_index_value, label="shard index", minimum=0, maximum=9999
        )
        shard_count = _bounded_integer(
            shard_count_value, label="shard count", minimum=1, maximum=10_000
        )
        if shard_index >= shard_count:
            raise RuntimeContractError("shard index must be less than shard count")
        if deadline_at is not None:
            _parse_deadline(deadline_at)
        return cls(
            run_id=run_id,
            launcher=launcher,
            execution_id=execution_id,
            attempt=attempt,
            shard_index=shard_index,
            shard_count=shard_count,
            deadline_at=deadline_at,
            principal=principal,
        )

    def dimensions(self) -> dict[str, object]:
        """Return the presentation-safe correlation dimensions included in runtime events."""
        dimensions: dict[str, object] = {
            "launcher": self.launcher,
            "launcher_execution_id": self.execution_id,
            "attempt": self.attempt,
            "shard_index": self.shard_index,
            "shard_count": self.shard_count,
        }
        if self.deadline_at is not None:
            dimensions["deadline_at"] = self.deadline_at
        if self.principal is not None:
            dimensions["principal"] = self.principal
        return dimensions


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """One JSON-Line runtime event with an intentionally non-sensitive payload."""

    event: Literal["runtime.started", "runtime.completed"]
    timestamp: str
    run_id: str
    pipeline_id: str
    platform: str
    stage: str
    dimensions: dict[str, object]
    status: Literal["succeeded", "skipped", "failed"] | None = None
    outputs: dict[str, object] | None = None
    failure_code: str | None = None
    retryable: bool | None = None

    @classmethod
    def started(
        cls,
        *,
        context: LauncherContext,
        pipeline_id: str,
        platform: str,
    ) -> RuntimeEvent:
        """Build the first event for a validated execution request."""
        return cls(
            event="runtime.started",
            timestamp=_timestamp(),
            run_id=context.run_id,
            pipeline_id=pipeline_id,
            platform=platform,
            stage="starting",
            dimensions=context.dimensions(),
        )

    @classmethod
    def completed(
        cls,
        result: PipelineExecutionResult,
        *,
        context: LauncherContext,
        platform: str,
    ) -> RuntimeEvent:
        """Build the terminal success/overlap record from non-sensitive aggregates."""
        endpoints = [
            {
                "name": endpoint.endpoint,
                "extracted_rows": endpoint.extracted,
                "affected_rows": endpoint.affected,
                "cursor_committed": endpoint.committed_cursor is not None,
            }
            for endpoint in result.ingestion.endpoints
        ]
        return cls(
            event="runtime.completed",
            timestamp=_timestamp(),
            run_id=result.run_id,
            pipeline_id=result.pipeline_id,
            platform=platform,
            stage="complete",
            dimensions=context.dimensions(),
            status="skipped" if result.skipped else "succeeded",
            outputs={
                "source": result.ingestion.source,
                "endpoints": endpoints,
                "models": list(result.models),
                "metrics": {
                    "endpoints": len(endpoints),
                    "extracted_rows": sum(
                        endpoint.extracted for endpoint in result.ingestion.endpoints
                    ),
                    "affected_rows": sum(
                        endpoint.affected for endpoint in result.ingestion.endpoints
                    ),
                    "models": len(result.models),
                    "assertions": result.assertions,
                    "assets": result.assets,
                },
            },
            retryable=False,
        )

    @classmethod
    def failed(
        cls,
        *,
        context: LauncherContext,
        pipeline_id: str,
        platform: str,
        stage: str,
        failure_code: str,
        retryable: bool,
    ) -> RuntimeEvent:
        """Build a terse terminal failure without copying exception text."""
        return cls(
            event="runtime.completed",
            timestamp=_timestamp(),
            run_id=context.run_id,
            pipeline_id=pipeline_id,
            platform=platform,
            stage=stage,
            dimensions=context.dimensions(),
            status="failed",
            outputs={},
            failure_code=failure_code,
            retryable=retryable,
        )

    def to_json(self) -> str:
        """Render one compact JSON line suitable for launchers and log processors."""
        payload: dict[str, object] = {
            "contract": RUNTIME_CONTRACT,
            "event": self.event,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "platform": self.platform,
            "stage": self.stage,
            "dimensions": self.dimensions,
        }
        if self.status is not None:
            payload["status"] = self.status
        if self.outputs is not None:
            payload["outputs"] = self.outputs
        if self.failure_code is not None:
            payload["failure_code"] = self.failure_code
        if self.retryable is not None:
            payload["retryable"] = self.retryable
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def validate_runtime_contract(value: str) -> None:
    """Reject unknown invocation contracts before any provider or project access."""
    if value != RUNTIME_CONTRACT:
        raise RuntimeContractError(
            f"unsupported runtime contract {value!r}; expected {RUNTIME_CONTRACT!r}"
        )


def validate_runtime_identifier(value: str, *, label: str) -> None:
    """Validate a pipeline or compatibility-profile identifier."""
    _require_identifier(value, label=label)


def is_retryable_failure(code: str) -> bool:
    """Return the conservative v1 whole-process retry policy for a stable failure code."""
    return code in {
        "catalog_failed",
        "destination_write_failed",
        "extraction_failed",
        "lease_failed",
        "rate_limited",
    }


@contextmanager
def graceful_signal_handlers() -> Iterator[None]:
    """Translate catchable termination signals into bounded normal exception cleanup."""
    previous: dict[
        signal.Signals,
        Callable[[int, FrameType | None], Any] | int | signal.Handlers | None,
    ] = {}

    def cancel(signum: int, _frame: object) -> None:
        raise RuntimeCancelledError(signal.Signals(signum).name)

    for handled in (signal.SIGTERM, signal.SIGINT):
        previous[handled] = signal.getsignal(handled)
        signal.signal(handled, cancel)
    try:
        yield
    finally:
        for handled, handler in previous.items():
            signal.signal(handled, handler)


def _require_identifier(value: str, *, label: str) -> None:
    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise RuntimeContractError(
            f"{label} must be 1-128 characters using letters, numbers, '.', '_', ':', or '-'"
        )


def _require_opaque(value: str | None, *, label: str) -> None:
    if value is None:
        return
    if (
        not value
        or len(value) > _MAX_OPAQUE_LENGTH
        or any(ord(character) < 32 for character in value)
    ):
        raise RuntimeContractError(
            f"{label} must be 1-{_MAX_OPAQUE_LENGTH} characters without control characters"
        )


def _bounded_integer(value: str, *, label: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise RuntimeContractError(f"{label} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise RuntimeContractError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def _parse_deadline(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeContractError(
            "deadline must be an ISO-8601 timestamp with a timezone"
        ) from error
    if parsed.tzinfo is None:
        raise RuntimeContractError("deadline must be an ISO-8601 timestamp with a timezone")
    return parsed


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
