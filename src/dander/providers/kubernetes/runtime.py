"""Existing-cluster Kubernetes execution projection."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander import __version__
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    KUBERNETES_CAPABILITIES,
    ExecutionProjectionError,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
    SecretReference,
    validate_launcher_projection,
)
from dander.deployment.runtime import LauncherRuntime
from dander.providers.kubernetes.config import KubernetesLauncherConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.runtime_contract import RUNTIME_CONTRACT

if TYPE_CHECKING:
    from pydantic import BaseModel

_PROFILE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


@dataclass(frozen=True, slots=True)
class KubernetesTemplateFactory:
    """Project one selected profile into Kubernetes Jobs and CronJobs."""

    config: KubernetesLauncherConfig

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
        """Build immutable templates without contacting the selected cluster."""
        del project
        if _PROFILE.fullmatch(profile_id) is None:
            raise ExecutionProjectionError("invalid Kubernetes deployment selector")
        if require_guarded_free_tier:
            raise ExecutionProjectionError(
                "Kubernetes cannot run the GCP guarded-free-tier preflight"
            )
        if alert_target is not None:
            raise ExecutionProjectionError(
                "Kubernetes uses cluster observability and cannot provision an alert target"
            )
        memory_mib = _memory_mib(memory)
        templates: dict[str, ExecutionTemplate] = {}
        for pipeline_id, pipeline in sorted(pipelines.items()):
            secret_env = pipeline["secret_env"]
            if not isinstance(secret_env, Mapping):
                raise ExecutionProjectionError("pipeline secret bindings are invalid")
            if secret_env and self.config.existing_secret_name is None:
                raise ExecutionProjectionError(
                    "Kubernetes secret bindings require launcher.existing_secret_name"
                )
            command: tuple[str, ...] = (
                "runtime",
                "execute",
                "--contract",
                RUNTIME_CONTRACT,
                "--pipeline",
                pipeline_id,
                "--platform",
                profile_id,
                "--config",
                "/app/dander.yaml",
                "--models-dir",
                "/app/models",
                "--batch-rows",
                str(batch_rows),
            )
            if bool(pipeline["build_models"]):
                command = (*command, "--catalog-output", "/tmp/dander-catalog.json")
            identity = (
                f"kubernetes://{self.config.namespace}/"
                f"serviceaccounts/{self.config.service_account_name}"
            )
            labels = {
                "dander_version": __version__,
                "image_digest": image.rsplit("@", maxsplit=1)[-1],
                "pipeline": pipeline_id,
                "profile": profile_id,
            }
            template = ExecutionTemplate(
                schema=EXECUTION_PROJECTION_SCHEMA,
                contract=RUNTIME_CONTRACT,
                pipeline_id=pipeline_id,
                profile_id=profile_id,
                launcher="kubernetes",
                image=image,
                command=command,
                configuration_reference="/app/dander.yaml",
                environment=tuple(
                    sorted(
                        {
                            "DANDER_IMAGE_DIGEST": image.rsplit("@", maxsplit=1)[-1],
                            "DANDER_LAUNCHER": "kubernetes",
                            "DANDER_PRINCIPAL": identity,
                        }.items()
                    )
                ),
                secret_bindings=tuple(
                    (
                        str(environment_name),
                        SecretReference(provider="environment", reference=f"env://{secret_id}"),
                    )
                    for environment_name, secret_id in sorted(secret_env.items())
                ),
                workload_identity=identity,
                resources=ResourceProjection(
                    cpu_millis=cpu * 1_000,
                    memory_mib=memory_mib,
                    ephemeral_storage_mib=self.config.ephemeral_storage_mib,
                    deadline_seconds=deadline_seconds,
                    runtime_retry_count=0,
                    launcher_retry_count=launcher_retry_count,
                ),
                schedule=ScheduleProjection(
                    task_count=1,
                    maximum_parallelism=1,
                    expression=str(pipeline["schedule"]),
                    time_zone=str(pipeline["time_zone"]),
                    paused=bool(pipeline["paused"]),
                ),
                network=NetworkPlacement(),
                labels=tuple(sorted(labels.items())),
                observability=ObservabilityProjection(
                    log_destination="stdout",
                    metric_namespace="kubernetes.io",
                    alert_target=None,
                    retention_days=None,
                ),
            )
            validate_launcher_projection(template, KUBERNETES_CAPABILITIES)
            templates[pipeline_id] = template
        return templates


def build_kubernetes_launcher(
    config: BaseModel,
    context: Mapping[str, object],
) -> LauncherRuntime:
    """Build the Kubernetes launcher only after explicit selection."""
    del context
    if not isinstance(config, KubernetesLauncherConfig):
        raise TypeError("Kubernetes launcher factory received the wrong configuration")
    return LauncherRuntime(
        provider_id="kubernetes",
        region=config.region,
        templates=KubernetesTemplateFactory(config),
        capabilities=KUBERNETES_CAPABILITIES,
    )


def _memory_mib(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(Mi|Gi)", value)
    if match is None:
        raise ExecutionProjectionError("runtime memory must use Mi or Gi")
    quantity = int(match.group(1))
    return quantity if match.group(2) == "Mi" else quantity * 1_024


KUBERNETES_LAUNCHER_FACTORY: ProviderFactory[LauncherRuntime] = ProviderFactory(
    kind=ProviderKind.LAUNCHER,
    provider_id="kubernetes",
    api_version=PROVIDER_API_VERSION,
    build=build_kubernetes_launcher,
)

__all__ = ["KUBERNETES_LAUNCHER_FACTORY"]
