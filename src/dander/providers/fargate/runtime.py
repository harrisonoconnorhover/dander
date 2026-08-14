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
from dander.deployment.runtime import LauncherRuntime, ResolvedTemplateRequest
from dander.providers.bigquery.config import BigQueryStateConfig, BigQueryWarehouseConfig
from dander.providers.dataplex.config import DataplexCatalogConfig
from dander.providers.fargate.config import FargateLauncherConfig
from dander.providers.fargate.context import (
    FargateProfileContext,
    fargate_profile_factory_context,
    optional_fargate_profile_context,
    require_fargate_profile_context,
)
from dander.providers.gcp_launcher import optional_gcp_launcher_context
from dander.providers.gcp_secret_manager.config import GcpSecretManagerConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.runtime_contract import RUNTIME_CONTRACT

if TYPE_CHECKING:
    from pydantic import BaseModel

_ROLE_NAME = re.compile(r"^[A-Za-z0-9+=,.@_-]{1,64}$")
_GCP_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_AWS_SECRET_REFERENCE = re.compile(
    r"^aws-sm://arn:(?P<partition>aws|aws-us-gov):secretsmanager:"
    r"(?P<region>[a-z0-9-]+):(?P<account>[0-9]{12}):"
    r"secret:[A-Za-z0-9/_+=.@-]{1,512}$"
)


@dataclass(frozen=True, slots=True)
class FargateTemplateFactory:
    """Build one explicitly qualified data-plane profile for AWS Fargate."""

    config: FargateLauncherConfig
    profile: FargateProfileContext

    def build(self, request: ResolvedTemplateRequest) -> dict[str, ExecutionTemplate]:
        """Build fail-closed templates for the selected GCP or AWS-native profile."""
        if request.profile_id != self.profile.profile_id:
            raise ExecutionProjectionError("Fargate request does not match its typed profile")
        if self.profile.is_gcp:
            assert self.profile.gcp is not None
            if self.profile.gcp.require_guarded_free_tier:
                raise ExecutionProjectionError(
                    "Fargate cannot run the GCP guarded-free-tier preflight"
                )
            if _GCP_PROJECT.fullmatch(self.profile.gcp.project) is None:
                raise ExecutionProjectionError("invalid GCP project identifier")
            if self.config.google_workload_identity_audience is None:
                raise ExecutionProjectionError(
                    "Fargate GCP profile requires Google workload identity"
                )
        elif self.config.google_workload_identity_audience is not None:
            raise ExecutionProjectionError(
                "Fargate AWS-native profile cannot use Google workload identity"
            )
        self._validate_profile_coordinates()
        memory_mib = _memory_mib(request.memory)
        cpu_millis = request.cpu * 1_000
        _validate_fargate_size(cpu_millis=cpu_millis, memory_mib=memory_mib)
        templates: dict[str, ExecutionTemplate] = {}
        for pipeline_id, pipeline in sorted(request.pipelines.items()):
            role_name = str(pipeline["runtime_service_account_id"])
            if _ROLE_NAME.fullmatch(role_name) is None:
                raise ExecutionProjectionError("invalid Fargate task role name")
            identity = f"arn:aws:iam::{self.config.aws_account_id}:role/{role_name}"
            secret_env = pipeline["secret_env"]
            if not isinstance(secret_env, Mapping):
                raise ExecutionProjectionError("pipeline secret bindings are invalid")
            if self.profile.is_aws_native:
                required_dsn = str(getattr(self.profile.state, "dsn_env", ""))
                if required_dsn not in secret_env:
                    raise ExecutionProjectionError(
                        f"Fargate AWS-native pipeline must bind {required_dsn}"
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
            environment = self._environment(
                request=request,
                role_name=role_name,
                identity=identity,
            )
            secret_bindings = self._secret_bindings(secret_env)
            template = ExecutionTemplate(
                schema=EXECUTION_PROJECTION_SCHEMA,
                contract=RUNTIME_CONTRACT,
                pipeline_id=pipeline_id,
                profile_id=request.profile_id,
                launcher="fargate",
                image=request.image,
                command=command,
                configuration_reference="/app/dander.yaml",
                environment=tuple(sorted(environment.items())),
                secret_bindings=secret_bindings,
                workload_identity=identity,
                resources=ResourceProjection(
                    cpu_millis=cpu_millis,
                    memory_mib=memory_mib,
                    ephemeral_storage_mib=self.config.ephemeral_storage_mib,
                    deadline_seconds=request.deadline_seconds,
                    runtime_retry_count=0,
                    launcher_retry_count=request.launcher_retry_count,
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
                    ("image_digest", request.image.rsplit("@", maxsplit=1)[-1]),
                    ("pipeline", pipeline_id),
                    ("profile", request.profile_id),
                ),
                observability=ObservabilityProjection(
                    log_destination="cloudwatch_logs",
                    metric_namespace="Dander",
                    alert_target=request.alert_target,
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

    def _validate_profile_coordinates(self) -> None:
        if not self.profile.is_aws_native:
            return
        warehouse = self.profile.warehouse
        catalog = self.profile.catalog
        secrets = self.profile.secrets
        if {
            str(getattr(warehouse, "region", "")),
            str(getattr(catalog, "region", "")),
            str(getattr(secrets, "region", "")),
        } != {self.config.region}:
            raise ExecutionProjectionError(
                "Fargate AWS-native provider regions must match the launcher"
            )
        if getattr(catalog, "catalog_id", None) != self.config.aws_account_id:
            raise ExecutionProjectionError(
                "Fargate AWS-native Glue catalog must match the launcher account"
            )
        copy_role = str(getattr(warehouse, "copy_role_arn", "")).split(":")
        if len(copy_role) < 6 or copy_role[4] != self.config.aws_account_id:
            raise ExecutionProjectionError(
                "Fargate AWS-native Redshift COPY role must match the launcher account"
            )

    def _environment(
        self,
        *,
        request: ResolvedTemplateRequest,
        role_name: str,
        identity: str,
    ) -> dict[str, str]:
        environment = {
            "AWS_DEFAULT_REGION": self.config.region,
            "AWS_REGION": self.config.region,
            "DANDER_IMAGE_DIGEST": request.image.rsplit("@", maxsplit=1)[-1],
            "DANDER_LAUNCHER": "fargate",
            "DANDER_PRINCIPAL": identity,
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
        }
        if self.profile.is_aws_native:
            return environment
        assert self.profile.gcp is not None
        assert self.config.google_workload_identity_audience is not None
        return environment | {
            "BQ_DATASET_METADATA": "dander_meta",
            "BQ_DATASET_RAW": "raw",
            "DANDER_GCP_SERVICE_ACCOUNT": (
                f"{role_name}@{self.profile.gcp.project}.iam.gserviceaccount.com"
            ),
            "DANDER_GCP_WIF_AUDIENCE": self.config.google_workload_identity_audience,
            "GCP_PROJECT_ID": self.profile.gcp.project,
        }

    def _secret_bindings(
        self,
        secret_env: Mapping[object, object],
    ) -> tuple[tuple[str, SecretReference], ...]:
        bindings: list[tuple[str, SecretReference]] = []
        for environment_name, secret_id in sorted(secret_env.items()):
            if not isinstance(environment_name, str) or not isinstance(secret_id, str):
                raise ExecutionProjectionError("pipeline secret bindings are invalid")
            if self.profile.is_aws_native:
                match = _AWS_SECRET_REFERENCE.fullmatch(secret_id)
                partition = "aws-us-gov" if self.config.region.startswith("us-gov-") else "aws"
                if (
                    match is None
                    or match.group("partition") != partition
                    or match.group("region") != self.config.region
                    or match.group("account") != self.config.aws_account_id
                ):
                    raise ExecutionProjectionError(
                        "AWS-native secrets must be full ARNs from the launcher account and region"
                    )
                reference = SecretReference(
                    provider="aws_secret_manager",
                    reference=secret_id,
                )
            else:
                assert self.profile.gcp is not None
                reference = SecretReference(
                    provider="gcp_secret_manager",
                    reference=(
                        f"gcp-sm://projects/{self.profile.gcp.project}/secrets/"
                        f"{secret_id}/versions/latest"
                    ),
                )
            bindings.append((environment_name, reference))
        return tuple(bindings)


def build_fargate_launcher(
    config: BaseModel,
    context: Mapping[str, object],
) -> LauncherRuntime:
    """Build Fargate projection behavior only after launcher selection."""
    if not isinstance(config, FargateLauncherConfig):
        raise TypeError("Fargate launcher factory received the wrong configuration")
    profile = optional_fargate_profile_context(context)
    if profile is None:
        gcp = optional_gcp_launcher_context(context)
        if gcp is None:
            raise TypeError("Fargate launcher requires a typed profile context")
        profile = FargateProfileContext(
            profile_id="gcp",
            warehouse=BigQueryWarehouseConfig(provider="bigquery"),
            state=BigQueryStateConfig(provider="bigquery"),
            catalog=DataplexCatalogConfig(provider="dataplex"),
            secrets=GcpSecretManagerConfig(provider="gcp_secret_manager"),
            gcp=gcp,
        )
    assert isinstance(profile, FargateProfileContext)
    return LauncherRuntime(
        provider_id="fargate",
        region=config.region,
        templates=FargateTemplateFactory(config, profile),
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

__all__ = [
    "FARGATE_LAUNCHER_FACTORY",
    "FargateProfileContext",
    "fargate_profile_factory_context",
    "require_fargate_profile_context",
]
