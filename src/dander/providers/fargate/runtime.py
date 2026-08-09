"""ECS/Fargate execution projection selected through the provider registry."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander import __version__
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    FARGATE_CAPABILITIES,
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
from dander.providers.fargate.config import FargateLauncherConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.runtime_contract import RUNTIME_CONTRACT

if TYPE_CHECKING:
    from pydantic import BaseModel

_ROLE_NAME = re.compile(r"^[A-Za-z0-9+=,.@_-]{1,64}$")
_GCP_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


@dataclass(frozen=True, slots=True)
class FargateTemplateFactory:
    """Build the GCP data-plane profile for an AWS Fargate launcher."""

    config: FargateLauncherConfig

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
        """Build fail-closed Fargate templates for the portable BigQuery proof."""
        if profile_id != "gcp":
            raise ExecutionProjectionError("Fargate compatibility projection requires gcp")
        if require_guarded_free_tier:
            raise ExecutionProjectionError("Fargate cannot run the GCP guarded-free-tier preflight")
        if _GCP_PROJECT.fullmatch(project) is None:
            raise ExecutionProjectionError("invalid GCP project identifier")
        memory_mib = _memory_mib(memory)
        cpu_millis = cpu * 1_000
        _validate_fargate_size(cpu_millis=cpu_millis, memory_mib=memory_mib)
        templates: dict[str, ExecutionTemplate] = {}
        for pipeline_id, pipeline in sorted(pipelines.items()):
            role_name = str(pipeline["runtime_service_account_id"])
            if _ROLE_NAME.fullmatch(role_name) is None:
                raise ExecutionProjectionError("invalid Fargate task role name")
            identity = f"arn:aws:iam::{self.config.aws_account_id}:role/{role_name}"
            secret_env = pipeline["secret_env"]
            if not isinstance(secret_env, Mapping):
                raise ExecutionProjectionError("pipeline secret bindings are invalid")
            command: tuple[str, ...] = (
                "runtime",
                "execute",
                "--contract",
                RUNTIME_CONTRACT,
                "--pipeline",
                pipeline_id,
                "--platform",
                "gcp",
                "--config",
                "/app/dander.yaml",
                "--models-dir",
                "/app/models",
                "--batch-rows",
                str(batch_rows),
            )
            if bool(pipeline["build_models"]):
                command = (*command, "--catalog-output", "/tmp/dander-catalog.json")
            template = ExecutionTemplate(
                schema=EXECUTION_PROJECTION_SCHEMA,
                contract=RUNTIME_CONTRACT,
                pipeline_id=pipeline_id,
                profile_id="gcp",
                launcher="fargate",
                image=image,
                command=command,
                configuration_reference="/app/dander.yaml",
                environment=tuple(
                    sorted(
                        {
                            "AWS_DEFAULT_REGION": self.config.region,
                            "AWS_REGION": self.config.region,
                            "BQ_DATASET_METADATA": "dander_meta",
                            "BQ_DATASET_RAW": "raw",
                            "DANDER_IMAGE_DIGEST": image.rsplit("@", maxsplit=1)[-1],
                            "DANDER_GCP_SERVICE_ACCOUNT": (
                                f"{role_name}@{project}.iam.gserviceaccount.com"
                            ),
                            "DANDER_GCP_WIF_AUDIENCE": (
                                self.config.google_workload_identity_audience
                            ),
                            "DANDER_LAUNCHER": "fargate",
                            "DANDER_PRINCIPAL": identity,
                            "GCP_PROJECT_ID": project,
                            "HOME": "/tmp",
                            "TMPDIR": "/tmp",
                        }.items()
                    )
                ),
                secret_bindings=tuple(
                    (
                        str(environment_name),
                        SecretReference(
                            provider="gcp_secret_manager",
                            reference=(
                                f"gcp-sm://projects/{project}/secrets/{secret_id}/versions/latest"
                            ),
                        ),
                    )
                    for environment_name, secret_id in sorted(secret_env.items())
                ),
                workload_identity=identity,
                resources=ResourceProjection(
                    cpu_millis=cpu_millis,
                    memory_mib=memory_mib,
                    ephemeral_storage_mib=self.config.ephemeral_storage_mib,
                    deadline_seconds=deadline_seconds,
                    runtime_retry_count=0,
                    launcher_retry_count=launcher_retry_count,
                ),
                schedule=ScheduleProjection(
                    task_count=1,
                    maximum_parallelism=1,
                    expression=_aws_schedule(str(pipeline["schedule"])),
                    time_zone=str(pipeline["time_zone"]),
                    paused=bool(pipeline["paused"]),
                ),
                network=NetworkPlacement(
                    placement="awsvpc",
                    extensions=(
                        ("fargate_security_group_ids", ",".join(self.config.security_group_ids)),
                        ("fargate_subnet_ids", ",".join(self.config.subnet_ids)),
                    ),
                ),
                labels=(
                    ("dander_version", __version__),
                    ("image_digest", image.rsplit("@", maxsplit=1)[-1]),
                    ("pipeline", pipeline_id),
                    ("profile", "gcp"),
                ),
                observability=ObservabilityProjection(
                    log_destination="cloudwatch_logs",
                    metric_namespace="Dander",
                    alert_target=alert_target,
                    retention_days=30,
                ),
                extensions=(
                    ("fargate_architecture", self.config.architecture),
                    (
                        "fargate_assign_public_ip",
                        "enabled" if self.config.assign_public_ip else "disabled",
                    ),
                    ("fargate_stop_timeout_seconds", str(self.config.stop_timeout_seconds)),
                ),
            )
            validate_launcher_projection(template, FARGATE_CAPABILITIES)
            templates[pipeline_id] = template
        return templates


def build_fargate_launcher(
    config: BaseModel,
    context: Mapping[str, object],
) -> LauncherRuntime:
    """Build Fargate projection behavior only after launcher selection."""
    del context
    if not isinstance(config, FargateLauncherConfig):
        raise TypeError("Fargate launcher factory received the wrong configuration")
    return LauncherRuntime(
        provider_id="fargate",
        region=config.region,
        templates=FargateTemplateFactory(config),
        capabilities=FARGATE_CAPABILITIES,
    )


def _memory_mib(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(Mi|Gi)", value)
    if match is None:
        raise ExecutionProjectionError("runtime memory must use Mi or Gi")
    quantity = int(match.group(1))
    return quantity if match.group(2) == "Mi" else quantity * 1_024


def _validate_fargate_size(*, cpu_millis: int, memory_mib: int) -> None:
    ranges = {
        1_000: range(2_048, 8_193, 1_024),
        2_000: range(4_096, 16_385, 1_024),
        4_000: range(8_192, 30_721, 1_024),
        8_000: range(16_384, 61_441, 4_096),
        16_000: range(32_768, 122_881, 8_192),
    }
    if memory_mib not in ranges.get(cpu_millis, range(0)):
        raise ExecutionProjectionError("Fargate does not support the requested CPU/memory pair")


def _aws_schedule(value: str) -> str:
    """Translate the compatible five-field cron subset to EventBridge Scheduler."""
    parts = value.split()
    if len(parts) != 5 or any(re.fullmatch(r"[A-Za-z0-9*/,\-]+", part) is None for part in parts):
        raise ExecutionProjectionError("Fargate requires a valid five-field cron schedule")
    minute, hour, day_of_month, month, day_of_week = parts
    if day_of_month != "*" and day_of_week != "*":
        raise ExecutionProjectionError(
            "Fargate cannot preserve a cron schedule that constrains both day fields"
        )
    if day_of_week == "*":
        day_of_week = "?"
    else:
        day_of_month = "?"
    return f"cron({minute} {hour} {day_of_month} {month} {day_of_week} *)"


FARGATE_LAUNCHER_FACTORY: ProviderFactory[LauncherRuntime] = ProviderFactory(
    kind=ProviderKind.LAUNCHER,
    provider_id="fargate",
    api_version=PROVIDER_API_VERSION,
    build=build_fargate_launcher,
)

__all__ = ["FARGATE_LAUNCHER_FACTORY"]
