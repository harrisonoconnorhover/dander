"""Installed runtime compatibility matrix and fail-closed pair selection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources

_SCHEMA = "io.dander.runtime.compatibility/v1"


class CompatibilityStatus(StrEnum):
    """Release status for one explicitly tested provider combination."""

    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"
    UNSUPPORTED = "unsupported"


class CompatibilityError(ValueError):
    """Raised when compatibility metadata is invalid or a pair cannot execute."""


@dataclass(frozen=True, slots=True)
class StateWarehousePair:
    """One state/warehouse pairing published by the installed package."""

    state: str
    warehouse: str
    status: CompatibilityStatus
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "state": self.state,
            "warehouse": self.warehouse,
            "status": self.status.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class WarehouseCapabilityReport:
    """Credential-free capability declaration for one packaged warehouse adapter."""

    provider: str
    write_modes: tuple[str, ...]
    transports: tuple[str, ...]
    logical_types: tuple[str, ...]
    max_decimal_precision: int
    max_temporal_precision: int
    supports_transforms: bool
    supports_graphs: bool
    supports_target_fencing: bool
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "write_modes": list(self.write_modes),
            "transports": list(self.transports),
            "logical_types": list(self.logical_types),
            "max_decimal_precision": self.max_decimal_precision,
            "max_temporal_precision": self.max_temporal_precision,
            "supports_transforms": self.supports_transforms,
            "supports_graphs": self.supports_graphs,
            "supports_target_fencing": self.supports_target_fencing,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class RuntimeCompatibility:
    """Validated, deterministic compatibility report for the installed runtime."""

    schema: str
    state_warehouse_pairs: tuple[StateWarehousePair, ...]
    warehouses: tuple[WarehouseCapabilityReport, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": self.schema,
                "state_warehouse_pairs": [pair.as_dict() for pair in self.state_warehouse_pairs],
                "warehouses": [warehouse.as_dict() for warehouse in self.warehouses],
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def pair(self, *, state: str, warehouse: str) -> StateWarehousePair:
        for pair in self.state_warehouse_pairs:
            if pair.state == state and pair.warehouse == warehouse:
                return pair
        raise CompatibilityError(
            f"State provider {state!r} with warehouse {warehouse!r} is not in the "
            "installed compatibility matrix"
        )

    def require_executable(self, *, state: str, warehouse: str) -> StateWarehousePair:
        pair = self.pair(state=state, warehouse=warehouse)
        if pair.status is CompatibilityStatus.UNSUPPORTED:
            raise CompatibilityError(pair.reason)
        return pair


def load_runtime_compatibility() -> RuntimeCompatibility:
    """Load and validate the package-owned matrix without contacting providers."""
    try:
        raw = (
            resources.files("dander")
            .joinpath("runtime-compatibility.json")
            .read_text(encoding="utf-8")
        )
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise CompatibilityError("runtime compatibility matrix is unavailable") from error
    if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
        raise CompatibilityError("runtime compatibility matrix is incompatible")
    raw_pairs = payload.get("state_warehouse_pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise CompatibilityError("runtime compatibility matrix has no state/warehouse pairs")
    pairs: list[StateWarehousePair] = []
    for raw_pair in raw_pairs:
        if not isinstance(raw_pair, dict):
            raise CompatibilityError("runtime compatibility matrix contains an invalid pair")
        try:
            pair = StateWarehousePair(
                state=_required_text(raw_pair, "state"),
                warehouse=_required_text(raw_pair, "warehouse"),
                status=CompatibilityStatus(_required_text(raw_pair, "status")),
                reason=_required_text(raw_pair, "reason"),
            )
        except ValueError as error:
            raise CompatibilityError(
                "runtime compatibility matrix contains an invalid status"
            ) from error
        pairs.append(pair)
    keys = [(pair.state, pair.warehouse) for pair in pairs]
    if len(keys) != len(set(keys)) or keys != sorted(keys):
        raise CompatibilityError(
            "runtime compatibility pairs must be unique and sorted by state/warehouse"
        )
    raw_warehouses = payload.get("warehouses")
    if not isinstance(raw_warehouses, list) or not raw_warehouses:
        raise CompatibilityError("runtime compatibility matrix has no warehouse capabilities")
    warehouses = tuple(_warehouse_capability(item) for item in raw_warehouses)
    providers = [warehouse.provider for warehouse in warehouses]
    if len(providers) != len(set(providers)) or providers != sorted(providers):
        raise CompatibilityError(
            "runtime warehouse capabilities must be unique and sorted by provider"
        )
    return RuntimeCompatibility(
        schema=_SCHEMA,
        state_warehouse_pairs=tuple(pairs),
        warehouses=warehouses,
    )


def _warehouse_capability(raw: object) -> WarehouseCapabilityReport:
    if not isinstance(raw, dict):
        raise CompatibilityError("runtime compatibility matrix contains an invalid warehouse")
    return WarehouseCapabilityReport(
        provider=_required_text(raw, "provider"),
        write_modes=_required_sorted_texts(raw, "write_modes"),
        transports=_required_sorted_texts(raw, "transports"),
        logical_types=_required_sorted_texts(raw, "logical_types"),
        max_decimal_precision=_required_positive_int(raw, "max_decimal_precision"),
        max_temporal_precision=_required_nonnegative_int(raw, "max_temporal_precision"),
        supports_transforms=_required_bool(raw, "supports_transforms"),
        supports_graphs=_required_bool(raw, "supports_graphs"),
        supports_target_fencing=_required_bool(raw, "supports_target_fencing"),
        limitations=_required_sorted_texts(raw, "limitations", allow_empty=True),
    )


def _required_text(payload: dict[object, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CompatibilityError(f"runtime compatibility pair has invalid {name}")
    return value


def _required_sorted_texts(
    payload: dict[object, object],
    name: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    value = payload.get(name)
    if (
        not isinstance(value, list)
        or (not value and not allow_empty)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or value != sorted(set(value))
    ):
        raise CompatibilityError(f"runtime warehouse capability has invalid {name}")
    return tuple(value)


def _required_positive_int(payload: dict[object, object], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise CompatibilityError(f"runtime warehouse capability has invalid {name}")
    return value


def _required_nonnegative_int(payload: dict[object, object], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 9:
        raise CompatibilityError(f"runtime warehouse capability has invalid {name}")
    return value


def _required_bool(payload: dict[object, object], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise CompatibilityError(f"runtime warehouse capability has invalid {name}")
    return value


__all__ = [
    "CompatibilityError",
    "CompatibilityStatus",
    "RuntimeCompatibility",
    "StateWarehousePair",
    "WarehouseCapabilityReport",
    "load_runtime_compatibility",
]
