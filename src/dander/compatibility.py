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
class RuntimeCompatibility:
    """Validated, deterministic compatibility report for the installed runtime."""

    schema: str
    state_warehouse_pairs: tuple[StateWarehousePair, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": self.schema,
                "state_warehouse_pairs": [pair.as_dict() for pair in self.state_warehouse_pairs],
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
    return RuntimeCompatibility(schema=_SCHEMA, state_warehouse_pairs=tuple(pairs))


def _required_text(payload: dict[object, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CompatibilityError(f"runtime compatibility pair has invalid {name}")
    return value


__all__ = [
    "CompatibilityError",
    "CompatibilityStatus",
    "RuntimeCompatibility",
    "StateWarehousePair",
    "load_runtime_compatibility",
]
