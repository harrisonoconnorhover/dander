"""Typed GCP data-plane context captured by selected launcher factories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_CONTEXT_KEY = "gcp_launcher"


@dataclass(frozen=True, slots=True)
class GcpLauncherContext:
    """GCP-only values that must not leak into provider-neutral template requests."""

    project: str
    require_guarded_free_tier: bool


def gcp_launcher_factory_context(context: GcpLauncherContext) -> dict[str, object]:
    """Return the provider-registry construction context for one GCP data plane."""
    return {_CONTEXT_KEY: context}


def optional_gcp_launcher_context(context: Mapping[str, object]) -> GcpLauncherContext | None:
    """Read and validate an optional typed GCP launcher construction context."""
    value = context.get(_CONTEXT_KEY)
    if value is None:
        return None
    if not isinstance(value, GcpLauncherContext):
        raise TypeError("launcher factory received an invalid GCP construction context")
    return value


def require_gcp_launcher_context(context: Mapping[str, object]) -> GcpLauncherContext:
    """Require the selected launcher to receive its GCP construction context."""
    value = optional_gcp_launcher_context(context)
    if value is None:
        raise TypeError("launcher factory requires a GCP construction context")
    return value


__all__ = [
    "GcpLauncherContext",
    "gcp_launcher_factory_context",
    "optional_gcp_launcher_context",
    "require_gcp_launcher_context",
]
