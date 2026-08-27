"""Provider-neutral collection of bounded results from runtime completion logs."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import cast

from dander.control.orchestration import BackendLogPage, ExecutionResultSummary
from dander.runtime_contract import RUNTIME_CONTRACT

_MAX_LOG_PAGES = 10
_LOG_PAGE_SIZE = 500
_MAX_OPERATIONS = 10_000


class ExecutionResultCollectionError(ValueError):
    """A terminal execution result is absent or violates the runtime contract."""


def collect_execution_result_summary(
    read_page: Callable[[str | None, int], BackendLogPage],
    *,
    pipeline_id: str,
) -> ExecutionResultSummary:
    """Read a bounded execution-scoped log window and return its completion summary."""
    cursor: str | None = None
    for _ in range(_MAX_LOG_PAGES):
        page = read_page(cursor, _LOG_PAGE_SIZE)
        for record in reversed(page.records):
            summary = parse_execution_result_summary(
                record.message,
                pipeline_id=pipeline_id,
            )
            if summary is not None:
                return summary
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    raise ExecutionResultCollectionError(
        "the successful execution result is temporarily unavailable"
    )


def parse_execution_result_summary(
    message: str,
    *,
    pipeline_id: str,
) -> ExecutionResultSummary | None:
    """Normalize one successful canonical ``runtime.completed`` JSON log record."""
    try:
        raw = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping) or raw.get("event") != "runtime.completed":
        return None
    try:
        event = _mapping(raw, "runtime completion event")
        if event.get("contract") != RUNTIME_CONTRACT:
            raise ExecutionResultCollectionError("runtime completion contract is unsupported")
        if event.get("pipeline_id") != pipeline_id:
            raise ExecutionResultCollectionError(
                "runtime completion does not match the execution plan"
            )
        status = event.get("status")
        if status not in {"succeeded", "skipped"}:
            raise ExecutionResultCollectionError(
                "successful provider execution lacks a successful runtime completion"
            )
        outputs = _mapping(event["outputs"], "runtime outputs")
        metrics = _mapping(outputs["metrics"], "runtime metrics")
        telemetry = _mapping(outputs["telemetry"], "runtime telemetry")
        operations = telemetry["operations"]
        if not isinstance(operations, list) or len(operations) > _MAX_OPERATIONS:
            raise ExecutionResultCollectionError("runtime telemetry operations are invalid")
        return ExecutionResultSummary(
            endpoints=_integer(metrics["endpoints"], "endpoints"),
            extracted_rows=_integer(metrics["extracted_rows"], "extracted_rows"),
            affected_rows=_integer(metrics["affected_rows"], "affected_rows"),
            models=_integer(metrics["models"], "models"),
            assertions=_integer(metrics["assertions"], "assertions"),
            assets=_integer(metrics["assets"], "assets"),
            duration_ms=_integer(telemetry["duration_ms"], "duration_ms"),
            operation_count=len(operations),
            retry_count=_integer(telemetry["retry_count"], "retry_count"),
            rows_read=_integer(telemetry["rows_read"], "rows_read"),
            rows_written=_integer(telemetry["rows_written"], "rows_written"),
            rows_affected=_integer(telemetry["rows_affected"], "rows_affected"),
            bytes_read=_integer(telemetry["bytes_read"], "bytes_read"),
            bytes_written=_integer(telemetry["bytes_written"], "bytes_written"),
            bytes_processed=_integer(telemetry["bytes_processed"], "bytes_processed"),
            bytes_billed=_integer(telemetry["bytes_billed"], "bytes_billed"),
            queue_duration_ms=_integer(telemetry["queue_duration_ms"], "queue_duration_ms"),
            execution_duration_ms=_integer(
                telemetry["execution_duration_ms"], "execution_duration_ms"
            ),
            spill_bytes=_integer(telemetry["spill_bytes"], "spill_bytes"),
            skipped=status == "skipped",
        )
    except ExecutionResultCollectionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise ExecutionResultCollectionError("runtime completion result is invalid") from error


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ExecutionResultCollectionError(f"{label} must be an object")
    return cast("Mapping[str, object]", value)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExecutionResultCollectionError(f"runtime {label} must be non-negative")
    return value


__all__ = [
    "ExecutionResultCollectionError",
    "collect_execution_result_summary",
    "parse_execution_result_summary",
]
