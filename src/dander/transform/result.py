"""Provider-neutral transform execution outcomes."""

from dataclasses import dataclass

from dander.telemetry import OperationTelemetry


class TransformRunError(RuntimeError):
    """Raised when model execution or a generic assertion fails."""


@dataclass(frozen=True)
class TransformRunResult:
    """Summary of models materialized and assertions evaluated."""

    models: tuple[str, ...]
    assertions: int
    telemetry: tuple[OperationTelemetry, ...] = ()


__all__ = ["TransformRunError", "TransformRunResult"]
