"""Immutable, cloud-neutral execution templates and launcher limit validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal

from dander import __version__
from dander.runtime_contract import RUNTIME_CONTRACT, LauncherContext

if TYPE_CHECKING:
    from dander.project import DanderProject

EXECUTION_PROJECTION_SCHEMA = "io.dander.execution/v1"
_IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9.-]+(?::[0-9]{1,5})?/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROFILE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_GCP_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]"
    r"\.iam\.gserviceaccount\.com$"
)
_GCP_SECRET_REFERENCE = re.compile(
    r"^gcp-sm://projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/secrets/"
    r"[A-Za-z][A-Za-z0-9_-]{0,254}/versions/(?:latest|[1-9][0-9]*)$"
)
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+=-]{0,1023}$")
_SECRET_SCHEMES = {
    "environment": "env://",
    "gcp_secret_manager": "gcp-sm://",
    "aws_secret_manager": "aws-sm://",
    "azure_key_vault": "azure-kv://",
    "oci_vault": "oci-vault://",
}


class ExecutionProjectionError(ValueError):
    """A projection is unsafe, incomplete, or unsupported by its launcher."""


@dataclass(frozen=True, slots=True)
class SecretReference:
    """One launcher-projected secret reference; never a resolved value."""

    provider: Literal[
        "environment",
        "gcp_secret_manager",
        "aws_secret_manager",
        "azure_key_vault",
        "oci_vault",
    ]
    reference: str

    def __post_init__(self) -> None:
        scheme = _SECRET_SCHEMES.get(self.provider)
        if (
            scheme is None
            or not self.reference.startswith(scheme)
            or not _REFERENCE.fullmatch(self.reference)
        ):
            raise ExecutionProjectionError("invalid secret reference")
        if self.provider == "gcp_secret_manager" and not _GCP_SECRET_REFERENCE.fullmatch(
            self.reference
        ):
            raise ExecutionProjectionError("invalid GCP Secret Manager reference")


@dataclass(frozen=True, slots=True)
class ResourceProjection:
    """Portable resource and retry intent for one launcher task."""

    cpu_millis: int
    memory_mib: int
    ephemeral_storage_mib: int | None
    deadline_seconds: int
    runtime_retry_count: int
    launcher_retry_count: int

    def __post_init__(self) -> None:
        values = (
            self.cpu_millis,
            self.memory_mib,
            self.deadline_seconds,
            self.runtime_retry_count,
            self.launcher_retry_count,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ExecutionProjectionError("resource and retry values must be integers")
        if self.ephemeral_storage_mib is not None and (
            isinstance(self.ephemeral_storage_mib, bool)
            or not isinstance(self.ephemeral_storage_mib, int)
            or self.ephemeral_storage_mib < 1
        ):
            raise ExecutionProjectionError("ephemeral storage must be a positive MiB quantity")
        if min(self.cpu_millis, self.memory_mib, self.deadline_seconds) < 1:
            raise ExecutionProjectionError("CPU, memory, and deadline must be positive")
        if min(self.runtime_retry_count, self.launcher_retry_count) < 0:
            raise ExecutionProjectionError("retry counts must not be negative")


@dataclass(frozen=True, slots=True)
class ScheduleProjection:
    """Scheduling and task-parallelism intent independent of a scheduler API."""

    task_count: int
    maximum_parallelism: int
    expression: str | None
    time_zone: str | None
    paused: bool

    def __post_init__(self) -> None:
        if (
            isinstance(self.task_count, bool)
            or isinstance(self.maximum_parallelism, bool)
            or self.task_count < 1
            or self.maximum_parallelism < 1
            or self.maximum_parallelism > self.task_count
        ):
            raise ExecutionProjectionError("task count and parallelism are inconsistent")
        if (self.expression is None) != (self.time_zone is None):
            raise ExecutionProjectionError("schedule and time zone must be supplied together")
        if self.expression is not None and (
            not self.expression.strip() or not self.time_zone or not self.time_zone.strip()
        ):
            raise ExecutionProjectionError("schedule and time zone must not be blank")


@dataclass(frozen=True, slots=True)
class NetworkPlacement:
    """Portable network request plus explicitly named provider extensions."""

    placement: str | None = None
    extensions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _validate_pairs(self.extensions, label="network extension")


@dataclass(frozen=True, slots=True)
class ObservabilityProjection:
    """Launcher-neutral log, metric, alert, and retention destinations."""

    log_destination: str
    metric_namespace: str
    alert_target: str | None
    retention_days: int | None

    def __post_init__(self) -> None:
        if not self.log_destination.strip() or not self.metric_namespace.strip():
            raise ExecutionProjectionError("log and metric destinations must not be blank")
        if self.retention_days is not None and self.retention_days < 1:
            raise ExecutionProjectionError("log retention must be a positive day count")


@dataclass(frozen=True, slots=True)
class ExecutionTemplate:
    """One immutable launcher input before run-specific correlation exists."""

    schema: str
    contract: str
    pipeline_id: str
    profile_id: str
    launcher: str
    image: str
    command: tuple[str, ...]
    configuration_reference: str
    environment: tuple[tuple[str, str], ...]
    secret_bindings: tuple[tuple[str, SecretReference], ...]
    workload_identity: str
    resources: ResourceProjection
    schedule: ScheduleProjection
    network: NetworkPlacement
    labels: tuple[tuple[str, str], ...]
    observability: ObservabilityProjection
    extensions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.schema != EXECUTION_PROJECTION_SCHEMA or self.contract != RUNTIME_CONTRACT:
            raise ExecutionProjectionError("unsupported execution projection contract")
        if not _IDENTIFIER.fullmatch(self.pipeline_id) or not _PROFILE.fullmatch(self.profile_id):
            raise ExecutionProjectionError("invalid pipeline or profile identifier")
        if not _PROFILE.fullmatch(self.launcher):
            raise ExecutionProjectionError("invalid launcher identifier")
        if not _IMMUTABLE_IMAGE.fullmatch(self.image):
            raise ExecutionProjectionError("execution image must use an immutable sha256 digest")
        if not self.command or any(not part for part in self.command):
            raise ExecutionProjectionError("execution command must not be empty")
        expected_command = (
            "runtime",
            "execute",
            "--contract",
            self.contract,
            "--pipeline",
            self.pipeline_id,
            "--platform",
            self.profile_id,
        )
        if self.command[: len(expected_command)] != expected_command:
            raise ExecutionProjectionError("execution command does not match projection identity")
        if not (
            self.configuration_reference.startswith("/")
            or self.configuration_reference.startswith(("gs://", "s3://", "az://", "oci://"))
        ):
            raise ExecutionProjectionError("configuration reference must be a path or object URI")
        if not self.workload_identity.strip() or len(self.workload_identity) > 512:
            raise ExecutionProjectionError("workload identity must not be blank")
        environment_names = _validate_pairs(self.environment, label="environment")
        secret_names = _validate_pairs(self.secret_bindings, label="secret binding")
        if any(not _ENVIRONMENT_NAME.fullmatch(name) for name in environment_names | secret_names):
            raise ExecutionProjectionError("environment names must use uppercase shell syntax")
        if environment_names & secret_names:
            raise ExecutionProjectionError("secret and non-secret environment names overlap")
        _validate_pairs(self.labels, label="label")
        _validate_pairs(self.extensions, label="extension")

    @property
    def digest(self) -> str:
        """Return the immutable OCI index digest without the repository prefix."""
        return self.image.rsplit("@", maxsplit=1)[1]

    def bind(
        self,
        *,
        run_id: str,
        launcher_execution_id: str | None,
        attempt: int,
        shard_index: int = 0,
        shard_count: int = 1,
        deadline_at: str | None = None,
    ) -> ExecutionRequest:
        """Bind one validated run-specific context without changing deployment intent."""
        values = {
            "DANDER_RUN_ID": run_id,
            "DANDER_LAUNCHER": self.launcher,
            "DANDER_ATTEMPT": str(attempt),
            "DANDER_SHARD_INDEX": str(shard_index),
            "DANDER_SHARD_COUNT": str(shard_count),
            "DANDER_PRINCIPAL": self.workload_identity,
        }
        if launcher_execution_id is not None:
            values["DANDER_LAUNCHER_EXECUTION_ID"] = launcher_execution_id
        if deadline_at is not None:
            values["DANDER_DEADLINE_AT"] = deadline_at
        return ExecutionRequest(
            template=self,
            context=LauncherContext.from_environment(values),
        )

    def as_dict(self) -> dict[str, object]:
        """Render a deterministic provider-neutral mapping for launchers and tests."""
        return {
            "schema": self.schema,
            "contract": self.contract,
            "pipeline_id": self.pipeline_id,
            "profile_id": self.profile_id,
            "launcher": self.launcher,
            "image": self.image,
            "command": list(self.command),
            "configuration_reference": self.configuration_reference,
            "environment": dict(self.environment),
            "secret_bindings": {
                name: asdict(reference) for name, reference in self.secret_bindings
            },
            "workload_identity": self.workload_identity,
            "resources": asdict(self.resources),
            "schedule": asdict(self.schedule),
            "network": {
                "placement": self.network.placement,
                "extensions": dict(self.network.extensions),
            },
            "labels": dict(self.labels),
            "observability": asdict(self.observability),
            "extensions": dict(self.extensions),
        }


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """A template bound to one validated launcher execution attempt."""

    template: ExecutionTemplate
    context: LauncherContext

    def environment(self) -> dict[str, str]:
        """Project correlation variables without exposing any secret reference or value."""
        values = dict(self.template.environment)
        values.update(
            {
                "DANDER_RUN_ID": self.context.run_id,
                "DANDER_LAUNCHER": self.context.launcher,
                "DANDER_ATTEMPT": str(self.context.attempt),
                "DANDER_SHARD_INDEX": str(self.context.shard_index),
                "DANDER_SHARD_COUNT": str(self.context.shard_count),
                "DANDER_PRINCIPAL": self.template.workload_identity,
            }
        )
        if self.context.execution_id is not None:
            values["DANDER_LAUNCHER_EXECUTION_ID"] = self.context.execution_id
        if self.context.deadline_at is not None:
            values["DANDER_DEADLINE_AT"] = self.context.deadline_at
        return dict(sorted(values.items()))


@dataclass(frozen=True, slots=True)
class LauncherCapabilities:
    """Explicit projection limits for one launcher implementation."""

    launcher: str
    cpu_millis: frozenset[int]
    minimum_memory_mib: int
    maximum_memory_mib: int
    maximum_deadline_seconds: int
    maximum_launcher_retries: int
    maximum_task_count: int
    maximum_parallelism: int
    supports_ephemeral_storage: bool
    supports_schedules: bool
    supports_time_zones: bool
    supports_network_placement: bool
    extension_names: frozenset[str] = frozenset()


CLOUD_RUN_CAPABILITIES = LauncherCapabilities(
    launcher="cloud_run",
    cpu_millis=frozenset({1_000, 2_000, 4_000, 6_000, 8_000}),
    minimum_memory_mib=128,
    maximum_memory_mib=32_768,
    maximum_deadline_seconds=86_400,
    maximum_launcher_retries=10,
    maximum_task_count=1,
    maximum_parallelism=1,
    supports_ephemeral_storage=False,
    supports_schedules=True,
    supports_time_zones=True,
    supports_network_placement=False,
)


FARGATE_CAPABILITIES = LauncherCapabilities(
    launcher="fargate",
    cpu_millis=frozenset({1_000, 2_000, 4_000, 8_000, 16_000}),
    minimum_memory_mib=512,
    maximum_memory_mib=122_880,
    maximum_deadline_seconds=86_400,
    maximum_launcher_retries=10,
    maximum_task_count=1,
    maximum_parallelism=1,
    supports_ephemeral_storage=True,
    supports_schedules=True,
    supports_time_zones=True,
    supports_network_placement=True,
    extension_names=frozenset(
        {
            "fargate_architecture",
            "fargate_assign_public_ip",
            "fargate_security_group_ids",
            "fargate_stop_timeout_seconds",
            "fargate_subnet_ids",
        }
    ),
)


KUBERNETES_CAPABILITIES = LauncherCapabilities(
    launcher="kubernetes",
    cpu_millis=frozenset({1_000, 2_000, 4_000, 6_000, 8_000}),
    minimum_memory_mib=128,
    maximum_memory_mib=1_048_576,
    maximum_deadline_seconds=86_400,
    maximum_launcher_retries=10,
    maximum_task_count=1,
    maximum_parallelism=1,
    supports_ephemeral_storage=True,
    supports_schedules=True,
    supports_time_zones=True,
    supports_network_placement=False,
)


AZURE_CONTAINER_APPS_CAPABILITIES = LauncherCapabilities(
    launcher="azure_container_apps",
    cpu_millis=frozenset({1_000, 2_000}),
    minimum_memory_mib=2_048,
    maximum_memory_mib=4_096,
    maximum_deadline_seconds=86_400,
    maximum_launcher_retries=10,
    maximum_task_count=1,
    maximum_parallelism=1,
    supports_ephemeral_storage=False,
    supports_schedules=True,
    supports_time_zones=True,
    supports_network_placement=True,
    extension_names=frozenset(
        {
            "azure_acr_login_server",
            "azure_key_vault_uri",
            "azure_managed_identity_client_id",
        }
    ),
)


OCI_CONTAINER_INSTANCES_CAPABILITIES = LauncherCapabilities(
    launcher="oci_container_instances",
    cpu_millis=frozenset({1_000, 2_000, 4_000, 6_000, 8_000}),
    minimum_memory_mib=1_024,
    maximum_memory_mib=1_540_096,
    # The detached lifecycle Function owns interruption and whole-task retries. OCI permits
    # detached Functions to run for at most one hour; reserve five minutes for cleanup.
    maximum_deadline_seconds=3_300,
    maximum_launcher_retries=10,
    maximum_task_count=1,
    maximum_parallelism=1,
    supports_ephemeral_storage=False,
    supports_schedules=True,
    supports_time_zones=True,
    supports_network_placement=True,
    extension_names=frozenset(
        {
            "oci_assign_public_ip",
            "oci_availability_domain",
            "oci_compartment_id",
            "oci_graceful_shutdown_seconds",
            "oci_registry_endpoint",
            "oci_restart_policy",
            "oci_shape",
            "oci_tenancy_id",
            "oci_vault_id",
        }
    ),
)


def validate_launcher_projection(
    template: ExecutionTemplate,
    capabilities: LauncherCapabilities,
) -> None:
    """Fail before planning when a launcher cannot honor the exact projection."""
    if template.launcher != capabilities.launcher:
        raise ExecutionProjectionError("projection launcher does not match its capabilities")
    resources = template.resources
    schedule = template.schedule
    if resources.cpu_millis not in capabilities.cpu_millis:
        raise ExecutionProjectionError("launcher does not support the requested CPU")
    if (
        not capabilities.minimum_memory_mib
        <= resources.memory_mib
        <= (capabilities.maximum_memory_mib)
    ):
        raise ExecutionProjectionError("launcher does not support the requested memory")
    if resources.deadline_seconds > capabilities.maximum_deadline_seconds:
        raise ExecutionProjectionError("launcher does not support the requested deadline")
    if resources.launcher_retry_count > capabilities.maximum_launcher_retries:
        raise ExecutionProjectionError("launcher does not support the requested retry count")
    if resources.ephemeral_storage_mib is not None and not (
        capabilities.supports_ephemeral_storage
    ):
        raise ExecutionProjectionError("launcher cannot configure ephemeral storage")
    if schedule.task_count > capabilities.maximum_task_count:
        raise ExecutionProjectionError("launcher does not support the requested task count")
    if schedule.maximum_parallelism > capabilities.maximum_parallelism:
        raise ExecutionProjectionError("launcher does not support the requested parallelism")
    if schedule.expression is not None and not capabilities.supports_schedules:
        raise ExecutionProjectionError("launcher does not support schedules")
    if schedule.time_zone is not None and not capabilities.supports_time_zones:
        raise ExecutionProjectionError("launcher does not support schedule time zones")
    if template.network.placement is not None and not capabilities.supports_network_placement:
        raise ExecutionProjectionError("launcher cannot configure network placement")
    extension_names = {name for name, _value in template.extensions}
    extension_names.update(name for name, _value in template.network.extensions)
    if unknown := extension_names - capabilities.extension_names:
        raise ExecutionProjectionError(
            f"launcher does not support projection extension {sorted(unknown)[0]!r}"
        )


def build_gcp_v1_execution_templates(
    manifest: DanderProject,
    *,
    image: str,
    project: str,
    dataset: str = "raw",
    metadata_dataset: str = "dander_meta",
    alert_target: str | None = None,
) -> dict[str, ExecutionTemplate]:
    """Project the legacy manifest into exact current GCP/Cloud Run execution intent."""
    return build_gcp_execution_templates(
        manifest.terraform_pipelines(),
        image=image,
        project=project,
        cpu=manifest.platform.runtime.cpu,
        memory=manifest.platform.runtime.memory,
        deadline_seconds=manifest.platform.runtime.timeout_seconds,
        launcher_retry_count=manifest.platform.runtime.max_retries,
        batch_rows=manifest.platform.runtime.batch_rows,
        require_guarded_free_tier=manifest.platform.safety.require_guarded_free_tier,
        dataset=dataset,
        metadata_dataset=metadata_dataset,
        alert_target=alert_target,
    )


def build_gcp_execution_templates(
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
    dataset: str = "raw",
    metadata_dataset: str = "dander_meta",
    alert_target: str | None = None,
) -> dict[str, ExecutionTemplate]:
    """Build current GCP templates from already validated deployment inputs."""
    if not _GCP_PROJECT.fullmatch(project):
        raise ExecutionProjectionError("invalid GCP project identifier")
    templates: dict[str, ExecutionTemplate] = {}
    for pipeline_id, pipeline in sorted(pipelines.items()):
        account_id = str(pipeline["runtime_service_account_id"])
        identity = f"{account_id}@{project}.iam.gserviceaccount.com"
        if not _SERVICE_ACCOUNT.fullmatch(identity):
            raise ExecutionProjectionError("invalid Cloud Run workload identity")
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
        if require_guarded_free_tier:
            command = (*command, "--guarded-free-tier")
        secret_env = pipeline["secret_env"]
        if not isinstance(secret_env, Mapping):
            raise ExecutionProjectionError("pipeline secret bindings are invalid")
        template = ExecutionTemplate(
            schema=EXECUTION_PROJECTION_SCHEMA,
            contract=RUNTIME_CONTRACT,
            pipeline_id=pipeline_id,
            profile_id="gcp",
            launcher="cloud_run",
            image=image,
            command=command,
            configuration_reference="/app/dander.yaml",
            environment=tuple(
                sorted(
                    {
                        "BQ_DATASET_METADATA": metadata_dataset,
                        "BQ_DATASET_RAW": dataset,
                        "DANDER_IMAGE_DIGEST": image.rsplit("@", maxsplit=1)[-1],
                        "DANDER_LAUNCHER": "cloud_run",
                        "DANDER_PRINCIPAL": identity,
                        "GCP_PROJECT_ID": project,
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
                cpu_millis=cpu * 1_000,
                memory_mib=_memory_mib(memory),
                ephemeral_storage_mib=None,
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
            labels=(
                ("dander_version", __version__),
                ("image_digest", image.rsplit("@", maxsplit=1)[-1]),
                ("pipeline", pipeline_id),
                ("profile", "gcp"),
            ),
            observability=ObservabilityProjection(
                log_destination="cloud_logging",
                metric_namespace="run.googleapis.com",
                alert_target=alert_target,
                retention_days=None,
            ),
        )
        validate_launcher_projection(template, CLOUD_RUN_CAPABILITIES)
        templates[pipeline_id] = template
    return templates


def _validate_pairs(values: tuple[tuple[str, object], ...], *, label: str) -> set[str]:
    keys = [key for key, _value in values]
    if keys != sorted(set(keys)) or any(not isinstance(key, str) or not key for key in keys):
        raise ExecutionProjectionError(f"{label} names must be unique and sorted")
    return set(keys)


def _memory_mib(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(Mi|Gi)", value)
    if match is None:
        raise ExecutionProjectionError("runtime memory must use Mi or Gi")
    quantity = int(match.group(1))
    return quantity if match.group(2) == "Mi" else quantity * 1_024
