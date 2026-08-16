"""Azure Container Apps Jobs execution projection."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander import __version__
from dander.deployment.projection import (
    AZURE_CONTAINER_APPS_CAPABILITIES,
    EXECUTION_PROJECTION_SCHEMA,
    ExecutionProjectionError,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
    SecretReference,
    validate_launcher_projection,
)
from dander.deployment.runtime import LauncherRuntime, ResolvedTemplateRequest
from dander.providers.azure_container_apps.config import AzureContainerAppsLauncherConfig
from dander.providers.gcp_launcher import GcpLauncherContext, optional_gcp_launcher_context
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.runtime_contract import RUNTIME_CONTRACT

if TYPE_CHECKING:
    from pydantic import BaseModel

_IMAGE_PATH = re.compile(r"^[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_CRON_PART = re.compile(r"^[A-Za-z0-9*/,-]+$")
_KEY_VAULT_SECRET = re.compile(r"^[A-Za-z0-9-]{1,127}$")
_GCP_SERVICE_ACCOUNT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


@dataclass(frozen=True, slots=True)
class AzureContainerAppsTemplateFactory:
    """Project one selected profile into Azure Container Apps Jobs."""

    config: AzureContainerAppsLauncherConfig
    gcp: GcpLauncherContext | None = None

    def build(self, request: ResolvedTemplateRequest) -> dict[str, ExecutionTemplate]:
        """Build immutable Azure templates without contacting Azure."""
        prefix = f"{self.config.acr_login_server}/"
        image_path = request.image.removeprefix(prefix)
        if not request.image.startswith(prefix) or _IMAGE_PATH.fullmatch(image_path) is None:
            raise ExecutionProjectionError(
                "Azure Container Apps requires an immutable image in the selected ACR"
            )
        memory_mib = _memory_mib(request.memory)
        cpu_millis = request.cpu * 1_000
        _validate_azure_size(cpu_millis=cpu_millis, memory_mib=memory_mib)
        if self.gcp is not None:
            if self.gcp.require_guarded_free_tier:
                raise ExecutionProjectionError(
                    "Azure cannot run the GCP guarded-free-tier preflight"
                )
            if (
                self.config.google_workload_identity_audience is None
                or self.config.google_application_id_uri is None
            ):
                raise ExecutionProjectionError(
                    "Azure BigQuery projection requires Google workload federation"
                )
        elif self.config.google_workload_identity_audience is not None:
            raise ExecutionProjectionError(
                "Azure Google workload federation requires a GCP platform profile"
            )
        templates: dict[str, ExecutionTemplate] = {}
        for pipeline_id, pipeline in sorted(request.pipelines.items()):
            secret_env = pipeline["secret_env"]
            if not isinstance(secret_env, Mapping):
                raise ExecutionProjectionError("pipeline secret bindings are invalid")
            if self.gcp is None:
                if any(
                    not isinstance(secret_id, str) or _KEY_VAULT_SECRET.fullmatch(secret_id) is None
                    for secret_id in secret_env.values()
                ):
                    raise ExecutionProjectionError(
                        "Azure Key Vault secret ids must use letters, numbers, or hyphens"
                    )
            else:
                role_name = str(pipeline["runtime_service_account_id"])
                if _GCP_SERVICE_ACCOUNT_ID.fullmatch(role_name) is None:
                    raise ExecutionProjectionError(
                        "Azure BigQuery projection requires a valid GCP service-account id"
                    )
            schedule = _azure_schedule(
                expression=str(pipeline["schedule"]),
                time_zone=str(pipeline["time_zone"]),
            )
            deployment_id = request.deployment_id or request.profile_id
            command: tuple[str, ...] = (
                "runtime",
                "execute",
                "--contract",
                RUNTIME_CONTRACT,
                "--pipeline",
                pipeline_id,
                "--platform",
                deployment_id,
                "--config",
                "/app/dander.yaml",
                "--models-dir",
                "/app/models",
                "--batch-rows",
                str(request.batch_rows),
            )
            if bool(pipeline["build_models"]):
                command = (*command, "--catalog-output", "/tmp/dander-catalog.json")
            digest = request.image.rsplit("@", maxsplit=1)[-1]
            labels = {
                "dander_version": __version__,
                "image_digest": digest,
                "pipeline": pipeline_id,
                "profile": request.profile_id,
            }
            environment = {
                "AZURE_CLIENT_ID": str(self.config.managed_identity_client_id),
                "AZURE_RESOURCE_GROUP": self.config.resource_group_name,
                "AZURE_SUBSCRIPTION_ID": str(self.config.subscription_id),
                "DANDER_IMAGE_DIGEST": digest,
                "DANDER_LAUNCHER": "azure_container_apps",
                "DANDER_PRINCIPAL": self.config.managed_identity_resource_id,
                "HOME": "/tmp",
                "TMPDIR": "/tmp",
            }
            if request.platforms_config_json is not None:
                environment["DANDER_PLATFORMS_CONFIG_JSON"] = request.platforms_config_json
            if self.gcp is not None:
                assert self.config.google_application_id_uri is not None
                assert self.config.google_workload_identity_audience is not None
                environment.update(
                    {
                        "BQ_DATASET_METADATA": "dander_meta",
                        "BQ_DATASET_RAW": "raw",
                        "DANDER_AZURE_GCP_APPLICATION_ID_URI": (
                            self.config.google_application_id_uri
                        ),
                        "DANDER_GCP_SERVICE_ACCOUNT": (
                            f"{role_name}@{self.gcp.project}.iam.gserviceaccount.com"
                        ),
                        "DANDER_GCP_WIF_AUDIENCE": (self.config.google_workload_identity_audience),
                        "GCP_PROJECT_ID": self.gcp.project,
                    }
                )
            template = ExecutionTemplate(
                schema=EXECUTION_PROJECTION_SCHEMA,
                contract=RUNTIME_CONTRACT,
                pipeline_id=pipeline_id,
                profile_id=deployment_id,
                launcher="azure_container_apps",
                image=request.image,
                command=command,
                configuration_reference="/app/dander.yaml",
                environment=tuple(sorted(environment.items())),
                secret_bindings=tuple(
                    (
                        str(environment_name),
                        SecretReference(
                            provider=(
                                "gcp_secret_manager" if self.gcp is not None else "azure_key_vault"
                            ),
                            reference=(
                                f"gcp-sm://projects/{self.gcp.project}/secrets/"
                                f"{secret_id}/versions/latest"
                                if self.gcp is not None
                                else f"azure-kv://{self.config.key_vault_uri}/secrets/{secret_id}"
                            ),
                        ),
                    )
                    for environment_name, secret_id in sorted(secret_env.items())
                ),
                workload_identity=self.config.managed_identity_resource_id,
                resources=ResourceProjection(
                    cpu_millis=cpu_millis,
                    memory_mib=memory_mib,
                    ephemeral_storage_mib=None,
                    deadline_seconds=request.deadline_seconds,
                    runtime_retry_count=0,
                    launcher_retry_count=request.launcher_retry_count,
                ),
                schedule=ScheduleProjection(
                    task_count=1,
                    maximum_parallelism=1,
                    expression=schedule,
                    time_zone="UTC",
                    paused=bool(pipeline["paused"]),
                ),
                network=NetworkPlacement(
                    placement=self.config.container_app_environment_resource_id,
                ),
                labels=tuple(sorted(labels.items())),
                observability=ObservabilityProjection(
                    log_destination="log_analytics",
                    metric_namespace="Microsoft.App/jobs",
                    alert_target=request.alert_target,
                    retention_days=30,
                ),
                extensions=(
                    ("azure_acr_login_server", self.config.acr_login_server),
                    ("azure_key_vault_uri", self.config.key_vault_uri),
                    (
                        "azure_managed_identity_client_id",
                        str(self.config.managed_identity_client_id),
                    ),
                ),
            )
            validate_launcher_projection(template, AZURE_CONTAINER_APPS_CAPABILITIES)
            templates[pipeline_id] = template
        return templates


def build_azure_container_apps_launcher(
    config: BaseModel,
    context: Mapping[str, object],
) -> LauncherRuntime:
    """Build Azure projection behavior only after explicit launcher selection."""
    if not isinstance(config, AzureContainerAppsLauncherConfig):
        raise TypeError("Azure Container Apps factory received the wrong configuration")
    gcp = optional_gcp_launcher_context(context)
    return LauncherRuntime(
        provider_id="azure_container_apps",
        region=config.region,
        templates=AzureContainerAppsTemplateFactory(config, gcp),
        capabilities=AZURE_CONTAINER_APPS_CAPABILITIES,
    )


def _memory_mib(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(Mi|Gi)", value)
    if match is None:
        raise ExecutionProjectionError("runtime memory must use Mi or Gi")
    quantity = int(match.group(1))
    return quantity if match.group(2) == "Mi" else quantity * 1_024


def _validate_azure_size(*, cpu_millis: int, memory_mib: int) -> None:
    supported = {1_000: 2_048, 2_000: 4_096}
    if supported.get(cpu_millis) != memory_mib:
        raise ExecutionProjectionError(
            "Azure Container Apps does not support the requested CPU/memory pair"
        )


def _azure_schedule(*, expression: str, time_zone: str) -> str:
    if time_zone != "UTC":
        raise ExecutionProjectionError("Azure Container Apps scheduled jobs require UTC")
    parts = expression.split()
    if len(parts) != 5 or any(_CRON_PART.fullmatch(part) is None for part in parts):
        raise ExecutionProjectionError(
            "Azure Container Apps requires a valid five-field cron schedule"
        )
    return " ".join(parts)


AZURE_CONTAINER_APPS_LAUNCHER_FACTORY: ProviderFactory[LauncherRuntime] = ProviderFactory(
    kind=ProviderKind.LAUNCHER,
    provider_id="azure_container_apps",
    api_version=PROVIDER_API_VERSION,
    build=build_azure_container_apps_launcher,
)

__all__ = ["AZURE_CONTAINER_APPS_LAUNCHER_FACTORY"]
