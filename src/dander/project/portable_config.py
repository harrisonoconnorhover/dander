"""Version 2 logical projects and named deployment configuration."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import yaml
from packaging.utils import canonicalize_name
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from dander.project.config import (
    DanderProject,
    PipelineResourceNames,
    PipelineSpec,
    PlatformRuntimeSpec,
    PlatformSafetySpec,
    PlatformSpec,
    PluginSpec,
    ProjectConfigError,
    _load_yaml_mapping,
)
from dander.providers.aws_secrets_manager import AwsSecretsManagerConfig  # noqa: TC001
from dander.providers.azure_container_apps import (  # noqa: TC001
    AzureContainerAppsLauncherConfig,
)
from dander.providers.azure_key_vault import AzureKeyVaultConfig  # noqa: TC001
from dander.providers.bigquery import BigQueryStateConfig, BigQueryWarehouseConfig  # noqa: TC001
from dander.providers.cloud_run import CloudRunLauncherConfig  # noqa: TC001
from dander.providers.dataplex import DataplexCatalogConfig  # noqa: TC001
from dander.providers.environment_secrets import EnvironmentSecretConfig  # noqa: TC001
from dander.providers.fargate import FargateLauncherConfig  # noqa: TC001
from dander.providers.gcp_secret_manager import GcpSecretManagerConfig  # noqa: TC001
from dander.providers.glue import GlueCatalogConfig  # noqa: TC001
from dander.providers.kubernetes import KubernetesLauncherConfig  # noqa: TC001
from dander.providers.no_catalog import NoCatalogConfig  # noqa: TC001
from dander.providers.oci_container_instances import (  # noqa: TC001
    OciContainerInstancesLauncherConfig,
)
from dander.providers.oci_vault import OciVaultConfig  # noqa: TC001
from dander.providers.postgresql import (  # noqa: TC001
    PostgreSQLStateConfig,
    PostgreSQLWarehouseConfig,
)
from dander.providers.redshift import RedshiftWarehouseConfig  # noqa: TC001
from dander.providers.snowflake import SnowflakeWarehouseConfig  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Mapping

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_PIPELINE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRET_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,254}$")
_AWS_SECRET_REFERENCE = re.compile(
    r"^aws-sm://arn:(?P<partition>aws|aws-us-gov):secretsmanager:"
    r"(?P<region>[a-z0-9-]+):(?P<account>[0-9]{12}):"
    r"secret:[A-Za-z0-9/_+=.@-]{1,512}$"
)


class LogicalPipelineSpec(BaseModel):
    """Provider-neutral pipeline intent authored in a version 2 project."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(pattern=_IDENTIFIER.pattern)
    graph: str | None = None
    models: list[str] = Field(default_factory=list)
    build_models: bool = True
    publish_catalog: bool = False

    @field_validator("models")
    @classmethod
    def validate_models(cls, values: list[str]) -> list[str]:
        if any(not _IDENTIFIER.fullmatch(value) for value in values):
            raise ValueError("models must contain valid Dander identifiers")
        if len(values) != len(set(values)):
            raise ValueError("models must not contain duplicates")
        return values

    @field_validator("graph")
    @classmethod
    def validate_graph_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in value
            or path.suffix.lower() not in {".yaml", ".yml", ".json"}
        ):
            raise ValueError("graph must be a safe relative YAML or JSON path")
        return path.as_posix()

    @model_validator(mode="after")
    def validate_execution_shape(self) -> LogicalPipelineSpec:
        if self.graph is None:
            if self.build_models and not self.models:
                raise ValueError("models must not be empty when build_models is enabled")
            return self
        if self.models or self.build_models or self.publish_catalog:
            raise ValueError(
                "graph pipelines require models=[], build_models=false, and publish_catalog=false"
            )
        return self


class DanderLogicalProjectV2(BaseModel):
    """The portable, provider-neutral ``dander.yaml`` version 2 contract."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    plugins: dict[str, PluginSpec] = Field(default_factory=dict)
    pipelines: dict[str, LogicalPipelineSpec] = Field(min_length=1)

    @field_validator("plugins")
    @classmethod
    def validate_plugin_ids(cls, values: dict[str, PluginSpec]) -> dict[str, PluginSpec]:
        if any(not _PLUGIN_ID.fullmatch(plugin_id) for plugin_id in values):
            raise ValueError("plugin ids must use lowercase letters, numbers, and underscores")
        distributions = [canonicalize_name(plugin.distribution) for plugin in values.values()]
        if len(distributions) != len(set(distributions)):
            raise ValueError("plugin distributions must not be declared more than once")
        return values

    @field_validator("pipelines")
    @classmethod
    def validate_pipeline_ids(
        cls, values: dict[str, LogicalPipelineSpec]
    ) -> dict[str, LogicalPipelineSpec]:
        if any(not _PIPELINE_NAME.fullmatch(pipeline_id) for pipeline_id in values):
            raise ValueError("pipeline ids must use lowercase letters, numbers, and underscores")
        return values


CatalogSpec = Annotated[
    DataplexCatalogConfig | GlueCatalogConfig | NoCatalogConfig,
    Field(discriminator="provider"),
]


SecretProviderSpec = Annotated[
    GcpSecretManagerConfig
    | EnvironmentSecretConfig
    | AzureKeyVaultConfig
    | OciVaultConfig
    | AwsSecretsManagerConfig,
    Field(discriminator="provider"),
]


LauncherSpec = Annotated[
    CloudRunLauncherConfig
    | FargateLauncherConfig
    | KubernetesLauncherConfig
    | AzureContainerAppsLauncherConfig
    | OciContainerInstancesLauncherConfig,
    Field(discriminator="provider"),
]


StateSpec = Annotated[
    BigQueryStateConfig | PostgreSQLStateConfig,
    Field(discriminator="provider"),
]


WarehouseSpec = Annotated[
    BigQueryWarehouseConfig
    | PostgreSQLWarehouseConfig
    | SnowflakeWarehouseConfig
    | RedshiftWarehouseConfig,
    Field(discriminator="provider"),
]


class PlatformProfileSpec(BaseModel):
    """One named combination of data-plane provider selections."""

    model_config = ConfigDict(extra="forbid")

    warehouse: WarehouseSpec
    state: StateSpec
    catalog: CatalogSpec
    secrets: SecretProviderSpec


class DeploymentPipelineSpec(BaseModel):
    """Environment-specific projection for one logical pipeline."""

    model_config = ConfigDict(extra="forbid")

    schedule: str = Field(default="0 9 * * *", min_length=1)
    time_zone: str = Field(default="America/New_York", min_length=1)
    paused: bool = True
    secret_bindings: dict[str, str] = Field(default_factory=dict)
    resources: PipelineResourceNames = Field(default_factory=PipelineResourceNames)

    @field_validator("schedule", "time_zone")
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("schedule and time_zone must not be blank")
        return value

    @field_validator("secret_bindings")
    @classmethod
    def validate_secrets(cls, values: dict[str, str]) -> dict[str, str]:
        if any(
            not _ENV_NAME.fullmatch(env_name)
            or (
                not _SECRET_ID.fullmatch(secret_id)
                and not _AWS_SECRET_REFERENCE.fullmatch(secret_id)
            )
            for env_name, secret_id in values.items()
        ):
            raise ValueError(
                "secret_bindings must map uppercase environment names to safe secret ids or "
                "full AWS Secrets Manager references"
            )
        return values


class DeploymentSpec(BaseModel):
    """One launcher projection over a named platform profile."""

    model_config = ConfigDict(extra="forbid")

    platform: str = Field(pattern=_NAME.pattern)
    launcher: LauncherSpec
    runtime: PlatformRuntimeSpec = Field(default_factory=PlatformRuntimeSpec)
    safety: PlatformSafetySpec = Field(default_factory=PlatformSafetySpec)
    pipelines: dict[str, DeploymentPipelineSpec] = Field(min_length=1)

    @field_validator("pipelines")
    @classmethod
    def validate_pipeline_ids(
        cls, values: dict[str, DeploymentPipelineSpec]
    ) -> dict[str, DeploymentPipelineSpec]:
        if any(not _PIPELINE_NAME.fullmatch(pipeline_id) for pipeline_id in values):
            raise ValueError("pipeline ids must use lowercase letters, numbers, and underscores")
        return values


class DanderPlatforms(BaseModel):
    """Versioned operator-owned platform and deployment configuration."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    platforms: dict[str, PlatformProfileSpec] = Field(min_length=1)
    deployments: dict[str, DeploymentSpec] = Field(min_length=1)

    @field_validator("platforms", "deployments")
    @classmethod
    def validate_names(cls, values: dict[str, object]) -> dict[str, object]:
        if any(not _NAME.fullmatch(name) for name in values):
            raise ValueError("names must use lowercase letters, numbers, and underscores")
        return values

    @model_validator(mode="after")
    def validate_platform_references(self) -> DanderPlatforms:
        unknown = sorted(
            {
                deployment.platform
                for deployment in self.deployments.values()
                if deployment.platform not in self.platforms
            }
        )
        if unknown:
            raise ValueError(f"deployments reference unknown platform {unknown[0]!r}")
        return self


@dataclass(frozen=True, slots=True)
class ProjectMigration:
    """Deterministic version 1 to version 2 migration artifacts."""

    logical_yaml: str
    platforms_yaml: str
    source_sha256: str


def resolve_version_two_project(
    project_path: Path,
    raw_project: dict[str, object],
    *,
    platforms_path: Path | None,
    deployment: str | None,
) -> DanderProject:
    """Resolve one v2 logical project against one explicit deployment."""
    logical = _validate_logical_project(project_path, raw_project)
    resolved_platforms_path = platforms_path or project_path.with_name("dander.platforms.yaml")
    raw_platforms = _load_yaml_mapping(
        resolved_platforms_path,
        label="Dander platform configuration",
    )
    platforms = _validate_platforms(resolved_platforms_path, raw_platforms)
    return _resolve(logical, platforms, deployment=deployment)


def prepare_version_one_migration(path: Path) -> ProjectMigration:
    """Render deterministic v2 files and prove resolved behavior is unchanged."""
    try:
        source = path.read_bytes()
        raw = yaml.safe_load(source.decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ProjectConfigError(f"Cannot read Dander project configuration: {path}") from error
    if not isinstance(raw, dict):
        raise ProjectConfigError(f"Dander project configuration must be a mapping: {path}")
    if raw.get("version") != 1:
        raise ProjectConfigError("Only a version 1 dander.yaml can be migrated")
    try:
        legacy = DanderProject.model_validate(raw)
    except ValidationError as error:
        raise _validation_error(path, error, label="Dander project configuration") from error

    logical_document = _logical_document(legacy)
    platforms_document = _platforms_document(legacy)
    logical_yaml = yaml.safe_dump(logical_document, sort_keys=False, width=100)
    platforms_yaml = yaml.safe_dump(platforms_document, sort_keys=False, width=100)

    logical = _validate_logical_project(path, logical_document)
    platforms = _validate_platforms(path.with_name("dander.platforms.yaml"), platforms_document)
    migrated = _resolve(logical, platforms, deployment="gcp_cloud_run")
    if not _equivalent(legacy, migrated):
        raise ProjectConfigError("Generated version 2 configuration changed resolved GCP behavior")
    return ProjectMigration(
        logical_yaml=logical_yaml,
        platforms_yaml=platforms_yaml,
        source_sha256=hashlib.sha256(source).hexdigest(),
    )


def _validate_logical_project(path: Path, raw: dict[str, object]) -> DanderLogicalProjectV2:
    try:
        return DanderLogicalProjectV2.model_validate(raw)
    except ValidationError as error:
        raise _validation_error(path, error, label="Dander project configuration") from error


def _validate_platforms(path: Path, raw: dict[str, object]) -> DanderPlatforms:
    try:
        return DanderPlatforms.model_validate(raw)
    except ValidationError as error:
        raise _validation_error(path, error, label="Dander platform configuration") from error


def _resolve(
    logical: DanderLogicalProjectV2,
    platforms: DanderPlatforms,
    *,
    deployment: str | None,
) -> DanderProject:
    selected_name = _select_deployment(platforms, deployment)
    selected = platforms.deployments[selected_name]
    profile = platforms.platforms[selected.platform]
    _validate_profile_composition(profile=profile, deployment=selected)
    if (
        profile.secrets.provider == "azure_key_vault"
        and selected.launcher.provider != "azure_container_apps"
    ):
        raise ProjectConfigError(
            "Azure Key Vault projection currently requires launcher.provider='azure_container_apps'"
        )
    if (
        profile.secrets.provider == "oci_vault"
        and selected.launcher.provider != "oci_container_instances"
    ):
        raise ProjectConfigError(
            "OCI Vault projection currently requires launcher.provider='oci_container_instances'"
        )
    if (
        selected.launcher.provider == "cloud_run"
        and profile.secrets.provider != "gcp_secret_manager"
    ):
        raise ProjectConfigError(
            "Cloud Run requires secrets.provider='gcp_secret_manager'; "
            "environment secrets are local or operator-managed Kubernetes only"
        )
    if selected.launcher.provider == "azure_container_apps" and profile.secrets.provider not in {
        "azure_key_vault",
        "gcp_secret_manager",
    }:
        raise ProjectConfigError(
            "Azure Container Apps requires Azure Key Vault or the named GCP secret profile"
        )
    if (
        selected.launcher.provider == "azure_container_apps"
        and profile.secrets.provider == "gcp_secret_manager"
        and (
            profile.warehouse.provider != "bigquery"
            or profile.state.provider != "bigquery"
            or profile.catalog.provider != "dataplex"
            or selected.launcher.google_workload_identity_audience is None
            or selected.launcher.google_application_id_uri is None
        )
    ):
        raise ProjectConfigError(
            "Azure GCP secrets require the named BigQuery/Dataplex federation profile"
        )
    if selected.launcher.provider == "oci_container_instances" and (
        profile.secrets.provider != "oci_vault"
        or profile.warehouse.provider != "postgresql"
        or profile.state.provider != "postgresql"
        or profile.catalog.provider != "none"
    ):
        raise ProjectConfigError(
            "OCI Container Instances currently requires the named "
            "PostgreSQL/PostgreSQL/no-catalog/OCI-Vault profile"
        )
    unknown_pipelines = sorted(set(selected.pipelines) - set(logical.pipelines))
    if unknown_pipelines:
        raise ProjectConfigError(
            f"Deployment {selected_name!r} references unknown pipeline {unknown_pipelines[0]!r}"
        )

    resolved_pipelines: dict[str, PipelineSpec] = {}
    for pipeline_id, pipeline in logical.pipelines.items():
        deployment_pipeline = selected.pipelines.get(pipeline_id) or DeploymentPipelineSpec()
        resolved_pipelines[pipeline_id] = PipelineSpec(
            source=pipeline.source,
            graph=pipeline.graph,
            models=pipeline.models,
            build_models=pipeline.build_models,
            # ``publish_dataplex`` is the version-1 compatibility field. In a resolved
            # version-2 project it means publication through the selected catalog provider.
            publish_dataplex=(pipeline.publish_catalog and profile.catalog.provider != "none"),
            schedule=deployment_pipeline.schedule,
            time_zone=deployment_pipeline.time_zone,
            paused=deployment_pipeline.paused,
            secrets=deployment_pipeline.secret_bindings,
            resources=deployment_pipeline.resources,
        )

    return DanderProject(
        version=2,
        platform=PlatformSpec(
            region=selected.launcher.region,
            bigquery_location=(
                profile.warehouse.location
                if isinstance(profile.warehouse, BigQueryWarehouseConfig)
                else "US"
            ),
            runtime=selected.runtime,
            safety=selected.safety,
        ),
        plugins=logical.plugins,
        pipelines=resolved_pipelines,
        platform_name=selected.platform,
        deployment_name=selected_name,
        warehouse_provider=profile.warehouse.provider,
        warehouse_config=profile.warehouse.model_dump(mode="json", exclude_none=True),
        state_provider=profile.state.provider,
        state_config=profile.state.model_dump(mode="json"),
        catalog_provider=profile.catalog.provider,
        catalog_config=profile.catalog.model_dump(mode="json", exclude_none=True),
        secret_provider=profile.secrets.provider,
        secret_config=profile.secrets.model_dump(mode="json"),
        launcher_provider=selected.launcher.provider,
        launcher_config=selected.launcher.model_dump(mode="json"),
        deployed_pipeline_ids=tuple(sorted(selected.pipelines)),
    )


def _select_deployment(platforms: DanderPlatforms, requested: str | None) -> str:
    if requested is not None:
        if requested in platforms.deployments:
            return requested
        profile_matches = tuple(
            name
            for name, deployment in platforms.deployments.items()
            if deployment.platform == requested
        )
        if len(profile_matches) == 1:
            return profile_matches[0]
        if len(profile_matches) > 1:
            choices = ", ".join(sorted(profile_matches))
            raise ProjectConfigError(
                f"Platform {requested!r} has multiple deployments ({choices}); "
                "select one by deployment name"
            )
        raise ProjectConfigError(f"Unknown deployment or platform {requested!r}")
    if len(platforms.deployments) != 1:
        choices = ", ".join(sorted(platforms.deployments))
        raise ProjectConfigError(
            f"Multiple deployments are configured ({choices}); select one explicitly"
        )
    return next(iter(platforms.deployments))


def _logical_document(project: DanderProject) -> dict[str, object]:
    document: dict[str, object] = {"version": 2}
    if project.plugins:
        document["plugins"] = {
            plugin_id: plugin.model_dump(mode="json")
            for plugin_id, plugin in sorted(project.plugins.items())
        }
    pipelines: dict[str, object] = {}
    for pipeline_id, pipeline in sorted(project.pipelines.items()):
        item: dict[str, object] = {
            "source": pipeline.source,
            "models": list(pipeline.models),
        }
        if pipeline.graph is not None:
            item["graph"] = pipeline.graph
        if not pipeline.build_models:
            item["build_models"] = False
        if pipeline.publish_dataplex:
            item["publish_catalog"] = True
        pipelines[pipeline_id] = item
    document["pipelines"] = pipelines
    return document


def _platforms_document(project: DanderProject) -> dict[str, object]:
    pipelines: dict[str, object] = {}
    for pipeline_id, pipeline in sorted(project.pipelines.items()):
        item: dict[str, object] = {
            "schedule": pipeline.schedule,
            "time_zone": pipeline.time_zone,
            "paused": pipeline.paused,
        }
        if pipeline.secrets:
            item["secret_bindings"] = dict(sorted(pipeline.secrets.items()))
        resources = pipeline.resources.model_dump(mode="json", exclude_none=True)
        if resources:
            item["resources"] = resources
        pipelines[pipeline_id] = item
    return {
        "version": 1,
        "platforms": {
            "gcp": {
                "warehouse": {
                    "provider": "bigquery",
                    "location": project.platform.bigquery_location,
                },
                "state": {"provider": "bigquery"},
                "catalog": {"provider": "dataplex"},
                "secrets": {"provider": "gcp_secret_manager"},
            }
        },
        "deployments": {
            "gcp_cloud_run": {
                "platform": "gcp",
                "launcher": {
                    "provider": "cloud_run",
                    "region": project.platform.region,
                },
                "runtime": project.platform.runtime.model_dump(mode="json"),
                "safety": project.platform.safety.model_dump(mode="json"),
                "pipelines": pipelines,
            }
        },
    }


def _equivalent(legacy: DanderProject, migrated: DanderProject) -> bool:
    return (
        legacy.platform == migrated.platform
        and legacy.warehouse_provider == migrated.warehouse_provider
        and legacy.warehouse_config == migrated.warehouse_config
        and legacy.state_provider == migrated.state_provider
        and legacy.state_config == migrated.state_config
        and legacy.catalog_provider == migrated.catalog_provider
        and legacy.catalog_config == migrated.catalog_config
        and legacy.secret_provider == migrated.secret_provider
        and legacy.secret_config == migrated.secret_config
        and legacy.launcher_provider == migrated.launcher_provider
        and legacy.resolved_launcher_config() == migrated.resolved_launcher_config()
        and legacy.plugins == migrated.plugins
        and legacy.pipelines == migrated.pipelines
        and legacy.terraform_pipelines() == migrated.terraform_pipelines()
    )


def _validation_error(
    path: Path,
    error: ValidationError,
    *,
    label: str,
) -> ProjectConfigError:
    locations = sorted({_validation_location(issue) for issue in error.errors()})
    return ProjectConfigError(f"Invalid {label} at {path}; check: {', '.join(locations)}")


def _validation_location(issue: Mapping[str, Any]) -> str:
    location = ".".join(str(part) for part in issue["loc"]) or "<root>"
    if issue.get("type") == "union_tag_invalid":
        return f"{location}.provider"
    return location


def _validate_profile_composition(
    *,
    profile: PlatformProfileSpec,
    deployment: DeploymentSpec,
) -> None:
    """Reject unqualified launcher/provider combinations before any provider I/O."""
    launcher = deployment.launcher
    if launcher.provider != "fargate":
        if profile.secrets.provider == "aws_secret_manager":
            raise ProjectConfigError(
                "AWS Secrets Manager projection currently requires launcher.provider='fargate'"
            )
        return

    providers = (
        profile.warehouse.provider,
        profile.state.provider,
        profile.catalog.provider,
        profile.secrets.provider,
    )
    gcp_profile = ("bigquery", "bigquery", "dataplex", "gcp_secret_manager")
    aws_profile = ("redshift", "postgresql", "glue", "aws_secret_manager")
    if providers == gcp_profile:
        if launcher.google_workload_identity_audience is None:
            raise ProjectConfigError(
                "Fargate GCP profile requires google_workload_identity_audience"
            )
        _reject_aws_secret_references(deployment)
        return
    if providers != aws_profile:
        raise ProjectConfigError(
            "Fargate supports only the named BigQuery/BigQuery/Dataplex/GCP-Secrets or "
            "Redshift/PostgreSQL/Glue/AWS-Secrets profiles"
        )
    if launcher.google_workload_identity_audience is not None:
        raise ProjectConfigError(
            "Fargate AWS-native profile must not configure Google workload identity"
        )
    assert isinstance(profile.warehouse, RedshiftWarehouseConfig)
    assert isinstance(profile.state, PostgreSQLStateConfig)
    assert isinstance(profile.catalog, GlueCatalogConfig)
    assert isinstance(profile.secrets, AwsSecretsManagerConfig)
    expected_region = launcher.region
    if {
        profile.warehouse.region,
        profile.catalog.region,
        profile.secrets.region,
    } != {expected_region}:
        raise ProjectConfigError(
            "Fargate AWS-native launcher, Redshift, Glue, and AWS Secrets Manager regions "
            "must match"
        )
    if profile.catalog.catalog_id != launcher.aws_account_id:
        raise ProjectConfigError(
            "Fargate AWS-native Glue catalog must belong to the launcher AWS account"
        )
    copy_role = profile.warehouse.copy_role_arn.split(":")
    if len(copy_role) < 6 or copy_role[4] != launcher.aws_account_id:
        raise ProjectConfigError(
            "Fargate AWS-native Redshift COPY role must belong to the launcher AWS account"
        )
    required_dsn = profile.state.dsn_env
    partition = "aws-us-gov" if expected_region.startswith("us-gov-") else "aws"
    prefix = (
        f"aws-sm://arn:{partition}:secretsmanager:{expected_region}:"
        f"{launcher.aws_account_id}:secret:"
    )
    for pipeline_id, pipeline in deployment.pipelines.items():
        reference = pipeline.secret_bindings.get(required_dsn)
        if reference is None:
            raise ProjectConfigError(
                f"Fargate AWS-native pipeline {pipeline_id!r} must bind {required_dsn}"
            )
        if any(not value.startswith(prefix) for value in pipeline.secret_bindings.values()):
            raise ProjectConfigError(
                "Fargate AWS-native secret bindings must use full AWS Secrets Manager ARNs "
                "from the launcher account and region"
            )


def _reject_aws_secret_references(deployment: DeploymentSpec) -> None:
    if any(
        value.startswith("aws-sm://")
        for pipeline in deployment.pipelines.values()
        for value in pipeline.secret_bindings.values()
    ):
        raise ProjectConfigError(
            "AWS Secrets Manager references require the named AWS-native Fargate profile"
        )
