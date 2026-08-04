"""Validated ``dander.yaml`` project and hosted-pipeline configuration."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_PIPELINE_ID = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRET_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,254}$")
_GCP_RESOURCE_ID = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")
_SERVICE_ACCOUNT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_RUNTIME_MEMORY = re.compile(r"^[1-9][0-9]*(?:Mi|Gi)$")
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_DISTRIBUTION = re.compile(r"^[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*$")

if TYPE_CHECKING:
    from dander.plugins import ConnectorPluginRegistry


class ProjectConfigError(ValueError):
    """Raised when project configuration is missing, invalid, or unresolved."""


class PluginSpec(BaseModel):
    """One exact independently distributed connector-plugin pin."""

    model_config = ConfigDict(extra="forbid")

    distribution: str
    version: str

    @field_validator("distribution")
    @classmethod
    def validate_distribution(cls, value: str) -> str:
        if not _DISTRIBUTION.fullmatch(value):
            raise ValueError("plugin distribution must be a package name, not a requirement")
        return value

    @field_validator("version")
    @classmethod
    def validate_exact_version(cls, value: str) -> str:
        try:
            Version(value)
        except InvalidVersion as error:
            raise ValueError("plugin version must be one exact PEP 440 version") from error
        return value


class PipelineResourceNames(BaseModel):
    """Optional stable GCP names, primarily for importing an existing deployment."""

    model_config = ConfigDict(extra="forbid")

    job: str | None = None
    runtime_service_account: str | None = None
    scheduler_service_account: str | None = None

    @field_validator("job")
    @classmethod
    def validate_job_name(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > 63 or not _GCP_RESOURCE_ID.fullmatch(value)):
            raise ValueError("job must be a valid Cloud Run resource id")
        return value

    @field_validator("runtime_service_account", "scheduler_service_account")
    @classmethod
    def validate_service_account(cls, value: str | None) -> str | None:
        if value is not None and not _SERVICE_ACCOUNT_ID.fullmatch(value):
            raise ValueError("service-account ids must satisfy the Google account-id contract")
        return value


class PipelineSpec(BaseModel):
    """One independently deployable ingestion, transform, and metadata pipeline."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(pattern=_IDENTIFIER.pattern)
    graph: str | None = None
    models: list[str] = Field(default_factory=list)
    schedule: str = Field(default="0 9 * * *", min_length=1)
    time_zone: str = Field(default="America/New_York", min_length=1)
    paused: bool = True
    build_models: bool = True
    publish_dataplex: bool = False
    secrets: dict[str, str] = Field(default_factory=dict)
    resources: PipelineResourceNames = Field(default_factory=PipelineResourceNames)

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
    def validate_execution_shape(self) -> PipelineSpec:
        if self.graph is None:
            if self.build_models and not self.models:
                raise ValueError("models must not be empty when build_models is enabled")
            return self
        if self.models or self.build_models or self.publish_dataplex:
            raise ValueError(
                "graph pipelines require models=[], build_models=false, and publish_dataplex=false"
            )
        return self

    @field_validator("schedule", "time_zone")
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("schedule and time_zone must not be blank")
        return value

    @field_validator("secrets")
    @classmethod
    def validate_secrets(cls, values: dict[str, str]) -> dict[str, str]:
        if any(
            not _ENV_NAME.fullmatch(env_name) or not _SECRET_ID.fullmatch(secret_id)
            for env_name, secret_id in values.items()
        ):
            raise ValueError("secrets must map uppercase environment names to safe secret ids")
        return values


class PlatformRuntimeSpec(BaseModel):
    """Shared Cloud Run and writer-request limits for every hosted pipeline."""

    model_config = ConfigDict(extra="forbid")

    cpu: int = 1
    memory: str = Field(default="512Mi", pattern=_RUNTIME_MEMORY.pattern)
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    max_retries: int = Field(default=1, ge=0, le=10)
    batch_rows: int = Field(default=10_000, ge=1, le=100_000)

    @field_validator("cpu")
    @classmethod
    def validate_cpu(cls, value: int) -> int:
        if value not in {1, 2, 4, 6, 8}:
            raise ValueError("cpu must be one of 1, 2, 4, 6, or 8")
        return value


class PlatformSafetySpec(BaseModel):
    """Hosted execution safeguards that must agree with provisioned infrastructure."""

    model_config = ConfigDict(extra="forbid")

    require_guarded_free_tier: bool = True


class PlatformSpec(BaseModel):
    """Repository-owned GCP defaults that affect every hosted pipeline."""

    model_config = ConfigDict(extra="forbid")

    region: str = Field(default="us-central1", min_length=1)
    bigquery_location: str = Field(default="US", min_length=1)
    runtime: PlatformRuntimeSpec = Field(default_factory=PlatformRuntimeSpec)
    safety: PlatformSafetySpec = Field(default_factory=PlatformSafetySpec)


class DanderProject(BaseModel):
    """The versioned, repository-owned Dander project manifest."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    platform: PlatformSpec = Field(default_factory=PlatformSpec)
    plugins: dict[str, PluginSpec] = Field(default_factory=dict)
    pipelines: dict[str, PipelineSpec] = Field(min_length=1)

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
    def validate_pipeline_ids(cls, values: dict[str, PipelineSpec]) -> dict[str, PipelineSpec]:
        if any(not _PIPELINE_ID.fullmatch(pipeline_id) for pipeline_id in values):
            raise ValueError("pipeline ids must use lowercase letters, numbers, and underscores")
        return values

    def terraform_pipelines(self) -> dict[str, dict[str, object]]:
        """Expand human pipeline definitions into the literal Terraform module contract."""
        return {
            pipeline_id: _terraform_pipeline(pipeline_id, pipeline)
            for pipeline_id, pipeline in sorted(self.pipelines.items())
        }

    def validate_references(
        self,
        root: Path,
        *,
        connectors_dir: Path = Path("connectors"),
        models_dir: Path = Path("models"),
        plugin_registry: ConnectorPluginRegistry | None = None,
    ) -> None:
        """Require every hosted connector, raw schema, and selected model to exist."""
        from dander.ingestion import ConnectorConfigError, load_source_config
        from dander.pipeline.runtime import (
            GraphRuntimeError,
            load_graph_for_execution,
            plan_graph_execution,
        )
        from dander.plugins import ConnectorPluginError, load_connector_plugins

        try:
            registry = plugin_registry or load_connector_plugins(self.plugins)
        except ConnectorPluginError as error:
            raise ProjectConfigError(str(error)) from error

        connectors = (root / connectors_dir).resolve()
        models = (root / models_dir).resolve()
        available_models = {path.stem for path in models.rglob("*.sql")}
        for pipeline_id, pipeline in self.pipelines.items():
            connector = connectors / f"{pipeline.source}.yaml"
            if not connector.is_file():
                raise ProjectConfigError(
                    f"Pipeline {pipeline_id!r} references missing connector {pipeline.source!r}"
                )
            try:
                source = load_source_config(connector)
            except ConnectorConfigError as error:
                raise ProjectConfigError(
                    f"Pipeline {pipeline_id!r} references invalid connector {pipeline.source!r}"
                ) from error
            try:
                registry.require_engine(source.engine)
            except ConnectorPluginError as error:
                raise ProjectConfigError(
                    f"Pipeline {pipeline_id!r} cannot use connector engine {source.engine!s}: "
                    f"{error}"
                ) from error
            for endpoint in source.endpoints:
                if not endpoint.raw_schema:
                    raise ProjectConfigError(
                        f"Pipeline {pipeline_id!r} endpoint {endpoint.name!r} "
                        "must declare raw_schema"
                    )
            if pipeline.graph is not None:
                graph_path = root / pipeline.graph
                try:
                    resolved_graph = graph_path.resolve(strict=True)
                    if not resolved_graph.is_relative_to(root.resolve()):
                        raise ProjectConfigError(
                            f"Pipeline {pipeline_id!r} graph must stay inside the project"
                        )
                    graph = load_graph_for_execution(resolved_graph)
                    plan_graph_execution(
                        graph,
                        source,
                        project="dander-project",
                        dataset="raw",
                    )
                except (OSError, GraphRuntimeError) as error:
                    raise ProjectConfigError(
                        f"Pipeline {pipeline_id!r} references an invalid executable graph"
                    ) from error
            elif missing := sorted(set(pipeline.models) - available_models):
                raise ProjectConfigError(
                    f"Pipeline {pipeline_id!r} references missing model {missing[0]!r}"
                )


def load_project_config(path: Path) -> DanderProject:
    """Load and validate one project manifest without reflecting authored values in errors."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ProjectConfigError(f"Cannot read Dander project configuration: {path}") from error
    if not isinstance(raw, dict):
        raise ProjectConfigError(f"Dander project configuration must be a mapping: {path}")
    try:
        return DanderProject.model_validate(raw)
    except ValidationError as error:
        locations = sorted(
            {".".join(str(part) for part in issue["loc"]) or "<root>" for issue in error.errors()}
        )
        raise ProjectConfigError(
            f"Invalid Dander project configuration at {path}; check: {', '.join(locations)}"
        ) from error


def _terraform_pipeline(pipeline_id: str, pipeline: PipelineSpec) -> dict[str, object]:
    slug = pipeline_id.replace("_", "-")
    resources = pipeline.resources
    return {
        "job_name": resources.job or _stable_resource_name("dander", slug, max_length=63),
        "runtime_service_account_id": resources.runtime_service_account
        or _stable_resource_name("dander", f"{slug}-run", max_length=30),
        "scheduler_service_account_id": resources.scheduler_service_account
        or _stable_resource_name("dander", f"{slug}-sched", max_length=30),
        "source": pipeline.source,
        "models": list(pipeline.models),
        "build_models": pipeline.build_models,
        "publish_dataplex": pipeline.publish_dataplex,
        "schedule": pipeline.schedule,
        "time_zone": pipeline.time_zone,
        "paused": pipeline.paused,
        "secret_env": dict(sorted(pipeline.secrets.items())),
    }


def _stable_resource_name(prefix: str, value: str, *, max_length: int) -> str:
    candidate = f"{prefix}-{value}"
    if len(candidate) <= max_length:
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:8]
    stem = candidate[: max_length - len(digest) - 1].rstrip("-")
    return f"{stem}-{digest}"
