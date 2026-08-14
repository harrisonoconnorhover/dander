"""Provider-neutral telemetry validation and deterministic serialization."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from dander.telemetry import (
    CostAttribution,
    MeasurementStatus,
    OperationTelemetry,
    PerformanceMeasurement,
    RunPerformance,
    RunTelemetry,
    TelemetryOperation,
)
from dander.writer import WriteTransport


def _operation() -> OperationTelemetry:
    return OperationTelemetry(
        provider="bigquery",
        operation=TelemetryOperation.LOAD,
        duration_ms=42,
        retry_count=1,
        rows_read=7,
        rows_written=6,
        rows_affected=5,
        bytes_read=400,
        bytes_written=300,
        bytes_processed=500,
        bytes_billed=1_000,
        queue_duration_ms=3,
        execution_duration_ms=39,
        spill_bytes=17,
        query_id="query/abc-123",
        job_id="job:abc-123",
        resource_name="COMPUTE_WH",
        resource_size="X-SMALL",
        capacity_units=Decimal("0.0025"),
        capacity_unit="credits",
        transport=WriteTransport.COPY,
        costs=(
            CostAttribution(
                provider="gcp",
                service="bigquery",
                amount=Decimal("0.000125"),
            ),
        ),
    )


def test_run_telemetry_serializes_normalized_totals_and_operation_details() -> None:
    telemetry = RunTelemetry(duration_ms=50, operations=(_operation(),))

    payload = telemetry.to_payload()

    assert payload == {
        "duration_ms": 50,
        "retry_count": 1,
        "rows_read": 7,
        "rows_written": 6,
        "rows_affected": 5,
        "bytes_read": 400,
        "bytes_written": 300,
        "bytes_processed": 500,
        "bytes_billed": 1_000,
        "queue_duration_ms": 3,
        "execution_duration_ms": 39,
        "spill_bytes": 17,
        "operations": [
            {
                "provider": "bigquery",
                "operation": "load",
                "duration_ms": 42,
                "retry_count": 1,
                "rows_read": 7,
                "rows_written": 6,
                "rows_affected": 5,
                "bytes_read": 400,
                "bytes_written": 300,
                "bytes_processed": 500,
                "bytes_billed": 1_000,
                "queue_duration_ms": 3,
                "execution_duration_ms": 39,
                "spill_bytes": 17,
                "query_id": "query/abc-123",
                "job_id": "job:abc-123",
                "resource_name": "COMPUTE_WH",
                "resource_size": "X-SMALL",
                "transport": "copy",
                "capacity_units": "0.0025",
                "capacity_unit": "credits",
                "costs": [
                    {
                        "provider": "gcp",
                        "service": "bigquery",
                        "amount": "0.000125",
                        "currency": "USD",
                        "estimated": True,
                    }
                ],
            }
        ],
    }
    assert json.dumps(payload, separators=(",", ":"), sort_keys=True) == json.dumps(
        telemetry.to_payload(), separators=(",", ":"), sort_keys=True
    )


def test_telemetry_rejects_unsafe_or_ambiguous_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        OperationTelemetry(
            provider="bigquery",
            operation=TelemetryOperation.QUERY,
            retry_count=-1,
        )
    with pytest.raises(ValueError, match="control characters"):
        OperationTelemetry(
            provider="bigquery",
            operation=TelemetryOperation.QUERY,
            query_id="query\nsecret",
        )
    with pytest.raises(ValueError, match="finite Decimal"):
        CostAttribution(
            provider="gcp",
            service="bigquery",
            amount=Decimal("NaN"),
        )
    with pytest.raises(TypeError):
        OperationTelemetry(  # type: ignore[call-arg]
            provider="bigquery",
            operation=TelemetryOperation.QUERY,
            unrestricted_details={"token": "secret"},
        )
    with pytest.raises(ValueError, match="declared together"):
        OperationTelemetry(
            provider="snowflake",
            operation=TelemetryOperation.QUERY,
            capacity_units=Decimal("0.1"),
        )
    with pytest.raises(ValueError, match="WriteTransport"):
        OperationTelemetry(
            provider="snowflake",
            operation=TelemetryOperation.LOAD,
            transport="copy",  # type: ignore[arg-type]
        )


def test_whole_run_duration_can_be_replaced_without_losing_operations() -> None:
    original = RunTelemetry(duration_ms=10, operations=(_operation(),))

    replaced = original.with_duration(25)

    assert replaced.duration_ms == 25
    assert replaced.operations == original.operations
    assert original.duration_ms == 10


def test_run_performance_distinguishes_measured_zero_from_unavailable() -> None:
    performance = _performance(
        catalog_duration=PerformanceMeasurement.unavailable(
            "catalog_duration_ms",
            "milliseconds",
            "profile_has_no_catalog",
        )
    )

    payload = performance.to_payload()
    measurements = payload["measurements"]

    assert performance.complete is False
    assert isinstance(measurements, list)
    assert measurements[5] == {
        "name": "retries",
        "unit": "count",
        "status": "measured",
        "value": "0",
    }
    assert measurements[9] == {
        "name": "catalog_duration_ms",
        "unit": "milliseconds",
        "status": "unavailable",
        "reason": "profile_has_no_catalog",
    }


def test_run_performance_requires_fixed_common_names_and_sorted_provider_metrics() -> None:
    with pytest.raises(ValueError, match="name='rows'"):
        _performance(rows=PerformanceMeasurement.measured("records", "rows", 1))
    with pytest.raises(ValueError, match="unique and sorted"):
        _performance(
            provider_metrics=(
                PerformanceMeasurement.measured("z_metric", "count", 1),
                PerformanceMeasurement.measured("a_metric", "count", 1),
            )
        )
    with pytest.raises(ValueError, match="must not duplicate"):
        _performance(provider_metrics=(PerformanceMeasurement.measured("rows", "rows", 1),))
    with pytest.raises(ValueError, match="unavailable reason"):
        PerformanceMeasurement(
            name="peak_rss_bytes",
            unit="bytes",
            status=MeasurementStatus.UNAVAILABLE,
        )


def _performance(
    *,
    rows: PerformanceMeasurement | None = None,
    catalog_duration: PerformanceMeasurement | None = None,
    provider_metrics: tuple[PerformanceMeasurement, ...] = (),
) -> RunPerformance:
    measured = PerformanceMeasurement.measured
    return RunPerformance(
        rows=rows or measured("rows", "rows", 10),
        logical_bytes=measured("logical_bytes", "bytes", 1_000),
        duration_ms=measured("duration_ms", "milliseconds", 100),
        throughput_rows_per_second=measured("throughput_rows_per_second", "rows_per_second", 100),
        peak_rss_bytes=measured("peak_rss_bytes", "bytes", 500),
        retries=measured("retries", "count", 0),
        queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
        load_duration_ms=measured("load_duration_ms", "milliseconds", 80),
        transform_duration_ms=measured("transform_duration_ms", "milliseconds", 20),
        catalog_duration_ms=catalog_duration or measured("catalog_duration_ms", "milliseconds", 0),
        provider_metrics=provider_metrics,
    )
