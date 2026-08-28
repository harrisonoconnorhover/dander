"""Provider-neutral input-size estimates used by fixed-size execution-plan selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from dander.control.graph_store import GraphRecord

_PORTABLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_MAX_BOUNDED_INTEGER = 9_223_372_036_854_775_807


class InputSizeEstimationError(RuntimeError):
    """Input metadata is temporarily unavailable or invalid."""


@dataclass(frozen=True, slots=True)
class InputSizeEstimate:
    """One bounded metadata observation without provider-native payloads."""

    estimated_input_bytes: int
    source: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            isinstance(self.estimated_input_bytes, bool)
            or not isinstance(self.estimated_input_bytes, int)
            or not 0 <= self.estimated_input_bytes <= _MAX_BOUNDED_INTEGER
        ):
            raise ValueError("estimated input bytes are invalid")
        if _PORTABLE_ID.fullmatch(self.source) is None:
            raise ValueError("estimate source is invalid")
        offset = self.observed_at.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("estimate observation time must use UTC")


class InputSizeEstimator(Protocol):
    """Estimate the current input bytes for one canonical graph revision."""

    def estimate(self, record: GraphRecord) -> InputSizeEstimate: ...


__all__ = ["InputSizeEstimate", "InputSizeEstimationError", "InputSizeEstimator"]
