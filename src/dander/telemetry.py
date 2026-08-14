"""Provider-neutral, non-sensitive telemetry for one Dander pipeline run."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dander.writer.base import WriteTransport

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,126}$")
_SAFE_CURRENCY = re.compile(r"^[A-Z]{3}$")
_MAX_OPAQUE_LENGTH = 256


class TelemetryOperation(StrEnum):
    """Closed operation kinds shared by warehouse and runtime adapters."""

    EXTRACT = "extract"
    LOAD = "load"
    QUERY = "query"
    TRANSFORM = "transform"
    TEST = "test"
    CATALOG = "catalog"
    STATE = "state"


class MeasurementStatus(StrEnum):
    """Whether a qualification metric was observed or is explicitly unavailable."""

    MEASURED = "measured"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class PerformanceMeasurement:
    """One typed qualification measurement without ambiguous zero defaults."""

    name: str
    unit: str
    status: MeasurementStatus
    value: Decimal | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_name(self.name, label="measurement name")
        _require_name(self.unit, label="measurement unit")
        if not isinstance(self.status, MeasurementStatus):
            raise ValueError("measurement status must be a MeasurementStatus")
        if self.status is MeasurementStatus.MEASURED:
            if not isinstance(self.value, Decimal) or not self.value.is_finite():
                raise ValueError("measured qualification value must be a finite Decimal")
            if self.value < 0:
                raise ValueError("measured qualification value must be non-negative")
            if self.reason is not None:
                raise ValueError(
                    "measured qualification value must not declare an unavailable reason"
                )
        else:
            if self.value is not None:
                raise ValueError("unavailable qualification value must not contain a value")
            _require_reason(self.reason, label="measurement unavailable reason")

    @classmethod
    def measured(cls, name: str, unit: str, value: int | Decimal) -> PerformanceMeasurement:
        """Build an explicitly measured value, including an honestly measured zero."""
        if isinstance(value, bool):
            raise ValueError("measured qualification value must not be a boolean")
        if not isinstance(value, (int, Decimal)):
            raise ValueError("measured qualification value must be an integer or Decimal")
        decimal_value = Decimal(value)
        return cls(
            name=name,
            unit=unit,
            status=MeasurementStatus.MEASURED,
            value=decimal_value,
        )

    @classmethod
    def unavailable(cls, name: str, unit: str, reason: str) -> PerformanceMeasurement:
        """Build an unavailable value with a bounded operator-facing reason."""
        return cls(
            name=name,
            unit=unit,
            status=MeasurementStatus.UNAVAILABLE,
            reason=reason,
        )

    def to_payload(self) -> dict[str, object]:
        """Return deterministic JSON-compatible measurement evidence."""
        payload: dict[str, object] = {
            "name": self.name,
            "unit": self.unit,
            "status": self.status.value,
        }
        if self.value is not None:
            payload["value"] = str(self.value)
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True, slots=True)
class CostAttribution:
    """One provider-reported or estimated monetary cost for an operation."""

    provider: str
    service: str
    amount: Decimal
    currency: str = "USD"
    estimated: bool = True

    def __post_init__(self) -> None:
        _require_name(self.provider, label="cost provider")
        _require_name(self.service, label="cost service")
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise ValueError("cost amount must be a finite Decimal")
        if self.amount < 0:
            raise ValueError("cost amount must be non-negative")
        if not _SAFE_CURRENCY.fullmatch(self.currency):
            raise ValueError("cost currency must be a three-letter uppercase code")
        if not isinstance(self.estimated, bool):
            raise ValueError("cost estimated must be a boolean")

    def to_payload(self) -> dict[str, object]:
        """Return deterministic JSON-compatible cost metadata."""
        return {
            "provider": self.provider,
            "service": self.service,
            "amount": str(self.amount),
            "currency": self.currency,
            "estimated": self.estimated,
        }


@dataclass(frozen=True, slots=True)
class OperationTelemetry:
    """Normalized statistics for one bounded provider operation."""

    provider: str
    operation: TelemetryOperation
    duration_ms: int = 0
    retry_count: int = 0
    rows_read: int = 0
    rows_written: int = 0
    rows_affected: int = 0
    bytes_read: int = 0
    bytes_written: int = 0
    bytes_processed: int = 0
    bytes_billed: int = 0
    query_id: str | None = None
    job_id: str | None = None
    costs: tuple[CostAttribution, ...] = ()
    queue_duration_ms: int = 0
    execution_duration_ms: int = 0
    spill_bytes: int = 0
    resource_name: str | None = None
    resource_size: str | None = None
    capacity_units: Decimal | None = None
    capacity_unit: str | None = None
    transport: WriteTransport | None = None

    def __post_init__(self) -> None:
        _require_name(self.provider, label="telemetry provider")
        if not isinstance(self.operation, TelemetryOperation):
            raise ValueError("telemetry operation must be a TelemetryOperation")
        for label, value in self._numeric_values().items():
            _require_nonnegative_integer(value, label=label)
        _require_opaque(self.query_id, label="query id")
        _require_opaque(self.job_id, label="job id")
        _require_opaque(self.resource_name, label="resource name")
        _require_opaque(self.resource_size, label="resource size")
        if self.transport is not None:
            from dander.writer.base import WriteTransport

            if not isinstance(self.transport, WriteTransport):
                raise ValueError("telemetry transport must be a WriteTransport")
        if (self.capacity_units is None) != (self.capacity_unit is None):
            raise ValueError("capacity_units and capacity_unit must be declared together")
        if self.capacity_units is not None:
            if not isinstance(self.capacity_units, Decimal) or not self.capacity_units.is_finite():
                raise ValueError("capacity_units must be a finite Decimal")
            if self.capacity_units < 0:
                raise ValueError("capacity_units must be non-negative")
            assert self.capacity_unit is not None
            _require_name(self.capacity_unit, label="capacity unit")
        if not isinstance(self.costs, tuple) or not all(
            isinstance(cost, CostAttribution) for cost in self.costs
        ):
            raise ValueError("telemetry costs must be a tuple of CostAttribution values")

    def to_payload(self) -> dict[str, object]:
        """Return deterministic JSON-compatible operation statistics."""
        payload: dict[str, object] = {
            "provider": self.provider,
            "operation": self.operation.value,
            **self._numeric_values(),
        }
        if self.query_id is not None:
            payload["query_id"] = self.query_id
        if self.job_id is not None:
            payload["job_id"] = self.job_id
        if self.resource_name is not None:
            payload["resource_name"] = self.resource_name
        if self.resource_size is not None:
            payload["resource_size"] = self.resource_size
        if self.transport is not None:
            payload["transport"] = self.transport.value
        if self.capacity_units is not None:
            payload["capacity_units"] = str(self.capacity_units)
            payload["capacity_unit"] = self.capacity_unit
        if self.costs:
            payload["costs"] = [cost.to_payload() for cost in self.costs]
        return payload

    def _numeric_values(self) -> dict[str, int]:
        return {
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "rows_affected": self.rows_affected,
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
            "bytes_processed": self.bytes_processed,
            "bytes_billed": self.bytes_billed,
            "queue_duration_ms": self.queue_duration_ms,
            "execution_duration_ms": self.execution_duration_ms,
            "spill_bytes": self.spill_bytes,
        }


@dataclass(frozen=True, slots=True)
class RunTelemetry:
    """Whole-run duration plus ordered provider-operation telemetry."""

    duration_ms: int = 0
    operations: tuple[OperationTelemetry, ...] = ()

    def __post_init__(self) -> None:
        _require_nonnegative_integer(self.duration_ms, label="duration_ms")
        if not isinstance(self.operations, tuple) or not all(
            isinstance(operation, OperationTelemetry) for operation in self.operations
        ):
            raise ValueError("telemetry operations must be a tuple of OperationTelemetry values")

    def with_duration(self, duration_ms: int) -> RunTelemetry:
        """Return a copy using a launcher-observed whole-process duration."""
        return replace(self, duration_ms=duration_ms)

    def to_payload(self) -> dict[str, object]:
        """Return stable totals and ordered details without arbitrary extension data."""
        totals = {
            key: sum(operation._numeric_values()[key] for operation in self.operations)
            for key in (
                "retry_count",
                "rows_read",
                "rows_written",
                "rows_affected",
                "bytes_read",
                "bytes_written",
                "bytes_processed",
                "bytes_billed",
                "queue_duration_ms",
                "execution_duration_ms",
                "spill_bytes",
            )
        }
        return {
            "duration_ms": self.duration_ms,
            **totals,
            "operations": [operation.to_payload() for operation in self.operations],
        }


@dataclass(frozen=True, slots=True)
class RunPerformance:
    """Normalized scale evidence for one run, distinct from best-effort telemetry."""

    rows: PerformanceMeasurement
    logical_bytes: PerformanceMeasurement
    duration_ms: PerformanceMeasurement
    throughput_rows_per_second: PerformanceMeasurement
    peak_rss_bytes: PerformanceMeasurement
    retries: PerformanceMeasurement
    queue_duration_ms: PerformanceMeasurement
    load_duration_ms: PerformanceMeasurement
    transform_duration_ms: PerformanceMeasurement
    catalog_duration_ms: PerformanceMeasurement
    provider_metrics: tuple[PerformanceMeasurement, ...] = ()
    costs: tuple[CostAttribution, ...] = ()

    def __post_init__(self) -> None:
        expected = {
            "rows": "rows",
            "logical_bytes": "bytes",
            "duration_ms": "milliseconds",
            "throughput_rows_per_second": "rows_per_second",
            "peak_rss_bytes": "bytes",
            "retries": "count",
            "queue_duration_ms": "milliseconds",
            "load_duration_ms": "milliseconds",
            "transform_duration_ms": "milliseconds",
            "catalog_duration_ms": "milliseconds",
        }
        for field_name, unit in expected.items():
            measurement = getattr(self, field_name)
            if not isinstance(measurement, PerformanceMeasurement):
                raise ValueError(f"{field_name} must be a PerformanceMeasurement")
            if measurement.name != field_name or measurement.unit != unit:
                raise ValueError(f"{field_name} must use name={field_name!r} and unit={unit!r}")
        if not isinstance(self.provider_metrics, tuple) or not all(
            isinstance(metric, PerformanceMeasurement) for metric in self.provider_metrics
        ):
            raise ValueError("provider_metrics must be PerformanceMeasurement values")
        metric_names = [metric.name for metric in self.provider_metrics]
        if metric_names != sorted(metric_names) or len(metric_names) != len(set(metric_names)):
            raise ValueError("provider_metrics must be unique and sorted by name")
        common_names = {measurement.name for measurement in self.common_measurements()}
        if common_names.intersection(metric_names):
            raise ValueError("provider_metrics must not duplicate common measurement names")
        if not isinstance(self.costs, tuple) or not all(
            isinstance(cost, CostAttribution) for cost in self.costs
        ):
            raise ValueError("qualification costs must be CostAttribution values")

    @property
    def complete(self) -> bool:
        """Return true only when every required common metric was measured."""
        return all(
            measurement.status is MeasurementStatus.MEASURED
            for measurement in self.common_measurements()
        )

    def common_measurements(self) -> tuple[PerformanceMeasurement, ...]:
        """Return the fixed provider-neutral metric set in contract order."""
        return (
            self.rows,
            self.logical_bytes,
            self.duration_ms,
            self.throughput_rows_per_second,
            self.peak_rss_bytes,
            self.retries,
            self.queue_duration_ms,
            self.load_duration_ms,
            self.transform_duration_ms,
            self.catalog_duration_ms,
        )

    def to_payload(self) -> dict[str, object]:
        """Return deterministic performance evidence with explicit availability."""
        return {
            "measurements": [
                measurement.to_payload()
                for measurement in (*self.common_measurements(), *self.provider_metrics)
            ],
            "costs": [cost.to_payload() for cost in self.costs],
        }


def _require_name(value: str, *, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{label} must use lowercase portable name syntax")


def _require_nonnegative_integer(value: int, *, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _require_opaque(value: str | None, *, label: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_OPAQUE_LENGTH
        or not value.isprintable()
    ):
        raise ValueError(
            f"{label} must be 1-{_MAX_OPAQUE_LENGTH} characters without control characters"
        )


def _require_reason(value: str | None, *, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_OPAQUE_LENGTH
        or not value.isprintable()
    ):
        raise ValueError(f"{label} must be 1-{_MAX_OPAQUE_LENGTH} printable characters")
