"""Provider-neutral launcher runtime composition."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from dander.deployment.projection import ExecutionTemplate, LauncherCapabilities


def _freeze_resolved_value(value: object) -> object:
    """Copy container values into read-only equivalents at the contract boundary."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_resolved_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_resolved_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_resolved_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ResolvedTemplateRequest:
    """Immutable provider-neutral intent for one launcher template projection."""

    pipelines: Mapping[str, Mapping[str, object]]
    image: str
    profile_id: str
    cpu: int
    memory: str
    deadline_seconds: int
    launcher_retry_count: int
    batch_rows: int
    alert_target: str | None
    deployment_id: str | None = None
    platforms_config_json: str | None = None

    def __post_init__(self) -> None:
        immutable = _freeze_resolved_value(self.pipelines)
        if not isinstance(immutable, Mapping):  # pragma: no cover - structural invariant
            raise TypeError("resolved template pipelines must be a mapping")
        object.__setattr__(self, "pipelines", immutable)
        if self.platforms_config_json is not None:
            if len(self.platforms_config_json.encode("utf-8")) > 32_768:
                raise ValueError("resolved platform configuration exceeds 32 KiB")
            try:
                document = json.loads(self.platforms_config_json)
            except json.JSONDecodeError as error:
                raise ValueError("resolved platform configuration must be JSON") from error
            if not isinstance(document, dict):
                raise ValueError("resolved platform configuration must be a JSON object")


@runtime_checkable
class ExecutionTemplateFactory(Protocol):
    """Build launcher-specific templates from validated deployment inputs."""

    def build(self, request: ResolvedTemplateRequest) -> dict[str, ExecutionTemplate]:
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
