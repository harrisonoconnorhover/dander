"""Collect approved statistics from completed BigQuery jobs."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns
from typing import TYPE_CHECKING, Protocol

from dander._bigquery_retry import run_mutation_with_retry
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.writer.base import WriteTransport

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class BigQueryTelemetry:
    """Normalize stable BigQuery job counters and identifiers."""

    def operation(
        self,
        job: object,
        *,
        operation: TelemetryOperation,
        duration_ms: int = 0,
        retry_count: int = 0,
    ) -> OperationTelemetry:
        """Read only approved scalar attributes from a completed BigQuery job."""
        return OperationTelemetry(
            provider="bigquery",
            operation=operation,
            duration_ms=duration_ms,
            retry_count=retry_count,
            rows_written=_nonnegative_attribute(job, "output_rows"),
            rows_affected=_nonnegative_attribute(job, "num_dml_affected_rows"),
            bytes_processed=_nonnegative_attribute(job, "total_bytes_processed"),
            bytes_billed=_nonnegative_attribute(job, "total_bytes_billed"),
            job_id=_optional_identifier(job, "job_id"),
        )


class _Job[ResultT](Protocol):
    def result(self) -> ResultT:
        """Wait for the submitted job."""


class BigQueryJobTelemetry:
    """Capture successful jobs and committed streams without changing their retry policy."""

    def __init__(self) -> None:
        self._operations: list[OperationTelemetry] = []

    def run[JobT: _Job[object]](
        self,
        submit: Callable[[], JobT],
        *,
        operation: TelemetryOperation,
        retry_mutation: bool = False,
    ) -> JobT:
        started = monotonic_ns()
        attempts = 0

        def counted_submit() -> JobT:
            nonlocal attempts
            attempts += 1
            return submit()

        if retry_mutation:
            job = run_mutation_with_retry(counted_submit)
        else:
            job = counted_submit()
            job.result()
        self._operations.append(
            BigQueryTelemetry().operation(
                job,
                operation=operation,
                duration_ms=(monotonic_ns() - started) // 1_000_000,
                retry_count=attempts - 1,
            )
        )
        return job

    def run_result[ResultT](
        self,
        submit: Callable[[], _Job[ResultT]],
        *,
        operation: TelemetryOperation,
    ) -> ResultT:
        """Observe a query while retaining its result without a second result() call."""
        started = monotonic_ns()
        job = submit()
        result = job.result()
        self._operations.append(
            BigQueryTelemetry().operation(
                job, operation=operation, duration_ms=(monotonic_ns() - started) // 1_000_000
            )
        )
        return result

    def record_committed_stream(self, *, rows_written: int, duration_ms: int) -> None:
        """Observe only a successfully committed pending stream; it has no query-job counters."""
        self._operations.append(
            OperationTelemetry(
                provider="bigquery",
                operation=TelemetryOperation.LOAD,
                duration_ms=duration_ms,
                rows_written=rows_written,
                transport=WriteTransport.STORAGE_WRITE,
            )
        )

    def drain(self) -> tuple[OperationTelemetry, ...]:
        operations = tuple(self._operations)
        self._operations.clear()
        return operations


def _nonnegative_attribute(job: object, name: str) -> int:
    value = getattr(job, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _optional_identifier(job: object, name: str) -> str | None:
    value = getattr(job, name, None)
    return value if isinstance(value, str) and value else None
