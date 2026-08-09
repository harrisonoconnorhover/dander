"""Provider-neutral launcher runtime composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dander.deployment.projection import ExecutionTemplate, LauncherCapabilities


@runtime_checkable
class ExecutionTemplateFactory(Protocol):
    """Build launcher-specific templates from validated deployment inputs."""

    def build(
        self,
        pipelines: Mapping[str, Mapping[str, object]],
        *,
        image: str,
        project: str,
        cpu: int,
        memory: str,
        deadline_seconds: int,
        launcher_retry_count: int,
        batch_rows: int,
        require_guarded_free_tier: bool,
        alert_target: str | None,
        profile_id: str = "gcp",
    ) -> dict[str, ExecutionTemplate]:
        """Return validated execution templates keyed by pipeline ID."""
        ...


@dataclass(frozen=True, slots=True)
class LauncherRuntime:
    """A selected launcher projection factory and its declared limits."""

    provider_id: str
    region: str
    templates: ExecutionTemplateFactory
    capabilities: LauncherCapabilities

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("launcher runtime requires a provider id")
        if not self.region:
            raise ValueError("launcher runtime requires a region")
        if self.capabilities.launcher != self.provider_id:
            raise ValueError("launcher runtime and capabilities provider ids must match")
        if not isinstance(self.templates, ExecutionTemplateFactory):
            raise TypeError("launcher template factory has the wrong type")
