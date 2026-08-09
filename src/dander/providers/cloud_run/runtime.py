"""Cloud Run execution projection selected through the provider registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.deployment.projection import (
    CLOUD_RUN_CAPABILITIES,
    ExecutionTemplate,
    build_gcp_execution_templates,
)
from dander.deployment.runtime import LauncherRuntime
from dander.providers.cloud_run.config import CloudRunLauncherConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class CloudRunTemplateFactory:
    """Preserve the accepted GCP template projection behind a launcher boundary."""

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
        """Build byte-equivalent Cloud Run templates through the existing projector."""
        if profile_id != "gcp":
            raise ValueError("Cloud Run compatibility projection requires profile_id='gcp'")
        return build_gcp_execution_templates(
            pipelines,
            image=image,
            project=project,
            cpu=cpu,
            memory=memory,
            deadline_seconds=deadline_seconds,
            launcher_retry_count=launcher_retry_count,
            batch_rows=batch_rows,
            require_guarded_free_tier=require_guarded_free_tier,
            alert_target=alert_target,
        )


def build_cloud_run_launcher(
    config: BaseModel,
    context: Mapping[str, object],
) -> LauncherRuntime:
    """Build Cloud Run projection behavior only after launcher selection."""
    del context
    if not isinstance(config, CloudRunLauncherConfig):
        raise TypeError("Cloud Run launcher factory received the wrong configuration")
    return LauncherRuntime(
        provider_id="cloud_run",
        region=config.region,
        templates=CloudRunTemplateFactory(),
        capabilities=CLOUD_RUN_CAPABILITIES,
    )


CLOUD_RUN_LAUNCHER_FACTORY: ProviderFactory[LauncherRuntime] = ProviderFactory(
    kind=ProviderKind.LAUNCHER,
    provider_id="cloud_run",
    api_version=PROVIDER_API_VERSION,
    build=build_cloud_run_launcher,
)

__all__ = ["CLOUD_RUN_LAUNCHER_FACTORY"]
