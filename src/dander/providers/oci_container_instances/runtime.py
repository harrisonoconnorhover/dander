"""OCI Container Instances execution projection."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander import __version__
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    OCI_CONTAINER_INSTANCES_CAPABILITIES,
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
from dander.providers.oci_container_instances.config import (
    OciContainerInstancesLauncherConfig,
)
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.runtime_contract import RUNTIME_CONTRACT

if TYPE_CHECKING:
    from pydantic import BaseModel

_CRON_PART = re.compile(r"^[A-Za-z0-9*/,-]+$")
_VAULT_SECRET = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,254}$")


@dataclass(frozen=True, slots=True)
class OciContainerInstancesTemplateFactory:
    """Project one named PostgreSQL profile into OCI Container Instances."""

    config: OciContainerInstancesLauncherConfig

    def build(self, request: ResolvedTemplateRequest) -> dict[str, ExecutionTemplate]:
        """Build immutable OCI templates without contacting OCI."""
        expected_prefix = f"{self.config.repository}@"
        image_digest = request.image.removeprefix(expected_prefix)
        if (
            not request.image.startswith(expected_prefix)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None
        ):
            raise ExecutionProjectionError(
                "OCI Container Instances requires an immutable image "
                "in the selected OCIR repository"
            )
        memory_mib = _memory_mib(request.memory)
        cpu_millis = request.cpu * 1_000
        _validate_oci_size(
            shape=self.config.shape,
            cpu_millis=cpu_millis,
            memory_mib=memory_mib,
        )
        templates: dict[str, ExecutionTemplate] = {}
        for pipeline_id, pipeline in sorted(request.pipelines.items()):
            secret_env = pipeline["secret_env"]
            if not isinstance(secret_env, Mapping):
                raise ExecutionProjectionError("pipeline secret bindings are invalid")
            if any(
                not isinstance(secret_name, str) or _VAULT_SECRET.fullmatch(secret_name) is None
                for secret_name in secret_env.values()
            ):
                raise ExecutionProjectionError(
                    "OCI Vault secret names must use letters, numbers, underscores, or hyphens"
                )
            schedule = _oci_schedule(
                expression=str(pipeline["schedule"]),
                time_zone=str(pipeline["time_zone"]),
            )
            command: tuple[str, ...] = (
                "runtime",
                "execute",
                "--contract",
                RUNTIME_CONTRACT,
                "--pipeline",
                pipeline_id,
                "--platform",
                request.profile_id,
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
            identity = self.config.resource_principal_identity
            template = ExecutionTemplate(
                schema=EXECUTION_PROJECTION_SCHEMA,
                contract=RUNTIME_CONTRACT,
                pipeline_id=pipeline_id,
                profile_id=request.profile_id,
                launcher="oci_container_instances",
                image=request.image,
                command=command,
                configuration_reference="/app/dander.yaml",
                environment=tuple(
                    sorted(
                        {
                            "DANDER_IMAGE_DIGEST": digest,
                            "DANDER_LAUNCHER": "oci_container_instances",
                            "DANDER_OCI_REGION": self.config.region,
                            "DANDER_PRINCIPAL": identity,
                            "HOME": "/tmp",
                            "TMPDIR": "/tmp",
                        }.items()
                    )
                ),
                secret_bindings=tuple(
                    (
                        str(environment_name),
                        SecretReference(
                            provider="oci_vault",
                            reference=(f"oci-vault://{self.config.vault_id}/secrets/{secret_name}"),
                        ),
                    )
                    for environment_name, secret_name in sorted(secret_env.items())
                ),
                workload_identity=identity,
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
                    placement=self.config.subnet_id,
                    extensions=(
                        ("oci_assign_public_ip", str(self.config.assign_public_ip).lower()),
                        ("oci_availability_domain", self.config.availability_domain),
                    ),
                ),
                labels=(
                    ("dander_version", __version__),
                    ("image_digest", digest),
                    ("pipeline", pipeline_id),
                    ("profile", request.profile_id),
                ),
                observability=ObservabilityProjection(
                    log_destination="oci_logging",
                    metric_namespace="oci_computecontainerinstance",
                    alert_target=request.alert_target,
                    retention_days=30,
                ),
                extensions=(
                    ("oci_compartment_id", self.config.compartment_id),
                    (
                        "oci_graceful_shutdown_seconds",
                        str(self.config.graceful_shutdown_seconds),
                    ),
                    ("oci_registry_endpoint", self.config.registry_endpoint),
                    ("oci_restart_policy", "NEVER"),
                    ("oci_shape", self.config.shape),
                    ("oci_tenancy_id", self.config.tenancy_id),
                    ("oci_vault_id", self.config.vault_id),
                ),
            )
            validate_launcher_projection(template, OCI_CONTAINER_INSTANCES_CAPABILITIES)
            templates[pipeline_id] = template
        return templates


def build_oci_container_instances_launcher(
    config: BaseModel,
    context: Mapping[str, object],
) -> LauncherRuntime:
    """Build OCI projection behavior only after explicit launcher selection."""
    del context
    if not isinstance(config, OciContainerInstancesLauncherConfig):
        raise TypeError("OCI Container Instances factory received the wrong configuration")
    return LauncherRuntime(
        provider_id="oci_container_instances",
        region=config.region,
        templates=OciContainerInstancesTemplateFactory(config),
        capabilities=OCI_CONTAINER_INSTANCES_CAPABILITIES,
    )


def _memory_mib(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(Mi|Gi)", value)
    if match is None:
        raise ExecutionProjectionError("runtime memory must use Mi or Gi")
    quantity = int(match.group(1))
    memory_mib = quantity if match.group(2) == "Mi" else quantity * 1_024
    if memory_mib % 1_024:
        raise ExecutionProjectionError("OCI Container Instances requires whole-GiB memory")
    return memory_mib


def _validate_oci_size(*, shape: str, cpu_millis: int, memory_mib: int) -> None:
    ocpus = cpu_millis // 1_000
    memory_gib = memory_mib // 1_024
    if cpu_millis % 1_000 or not 1 <= ocpus <= 8:
        raise ExecutionProjectionError("OCI Container Instances requires 1 through 8 OCPUs")
    if shape == "CI.Standard.E5.Flex":
        valid = 1 <= memory_gib <= 1_504
    elif shape == "CI.Standard.A1.Flex":
        valid = ocpus <= memory_gib <= min(64 * ocpus, 488)
    else:
        valid = ocpus <= memory_gib <= min(64 * ocpus, 1_024)
    if not valid:
        raise ExecutionProjectionError(
            "OCI Container Instances does not support the requested shape CPU/memory pair"
        )


def _oci_schedule(*, expression: str, time_zone: str) -> str:
    if time_zone != "UTC":
        raise ExecutionProjectionError("OCI scheduled Functions require UTC")
    parts = expression.split()
    if len(parts) != 5 or any(_CRON_PART.fullmatch(part) is None for part in parts):
        raise ExecutionProjectionError(
            "OCI scheduled Functions require a valid five-field cron schedule"
        )
    return " ".join(parts)


OCI_CONTAINER_INSTANCES_LAUNCHER_FACTORY: ProviderFactory[LauncherRuntime] = ProviderFactory(
    kind=ProviderKind.LAUNCHER,
    provider_id="oci_container_instances",
    api_version=PROVIDER_API_VERSION,
    build=build_oci_container_instances_launcher,
)

__all__ = ["OCI_CONTAINER_INSTANCES_LAUNCHER_FACTORY"]
