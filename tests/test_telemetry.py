"""Provider-neutral telemetry validation and deterministic serialization."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from dander.telemetry import (
    CostAttribution,
    OperationTelemetry,
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
