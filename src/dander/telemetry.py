"""Provider-neutral, non-sensitive telemetry for one Dander pipeline run."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

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
