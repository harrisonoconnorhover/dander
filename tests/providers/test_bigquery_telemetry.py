"""Completed BigQuery jobs keep their measured counters and existing retry policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from google.api_core.exceptions import BadRequest

from dander.providers.bigquery.telemetry import BigQueryJobTelemetry
from dander.telemetry import TelemetryOperation

if TYPE_CHECKING:
    from pytest import MonkeyPatch


class _Job:
    job_id = "query-123"
    total_bytes_processed = 0
    total_bytes_billed = 0

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def result(self) -> object:
        if self.error is not None:
            raise self.error
        self.total_bytes_processed = 1_024
        self.total_bytes_billed = 2_048
        return self


def test_contention_keeps_retry_count_and_completed_job_statistics(
    monkeypatch: MonkeyPatch,
) -> None:
    success = _Job()
    jobs = [
        _Job(BadRequest("Could not serialize access to table")),  # type: ignore[no-untyped-call]
        success,
    ]
    times = iter((1_000_000, 46_000_000))
    monkeypatch.setattr("dander.providers.bigquery.telemetry.monotonic_ns", lambda: next(times))
    monkeypatch.setattr("dander._bigquery_retry.sleep", lambda _: None)
    telemetry = BigQueryJobTelemetry()

    returned = telemetry.run(
        lambda: jobs.pop(0), operation=TelemetryOperation.TRANSFORM, retry_mutation=True
    )

    assert returned is success
    assert jobs == []
    (operation,) = telemetry.drain()
    assert operation.retry_count == 1
    assert operation.duration_ms == 45
    assert operation.bytes_processed == 1_024
    assert operation.bytes_billed == 2_048
    assert operation.job_id == "query-123"
    assert telemetry.drain() == ()


def test_failed_job_does_not_report_a_successful_operation() -> None:
    telemetry = BigQueryJobTelemetry()
    error = BadRequest("Access Denied")  # type: ignore[no-untyped-call]

    with pytest.raises(BadRequest) as caught:
        telemetry.run(lambda: _Job(error), operation=TelemetryOperation.LOAD)

    assert caught.value is error
    assert telemetry.drain() == ()
