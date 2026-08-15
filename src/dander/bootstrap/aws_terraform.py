"""Saved-plan Terraform lifecycle for one manifest-defined AWS Fargate deployment."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from json import dumps
from typing import TYPE_CHECKING

from dander.bootstrap.terraform import (
    TerraformBootstrapError,
    build_launcher_runtime,
    validate_runtime_settings,
    validate_terraform_pipelines,
)
from dander.deployment import ExecutionProjectionError, ResolvedTemplateRequest
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry
from dander.providers.fargate.config import FargateLauncherConfig
from dander.providers.fargate.context import FargateProfileContext
from dander.providers.gcp_launcher import GcpLauncherContext
from dander.providers.glue.config import GlueCatalogConfig
from dander.providers.redshift.config import RedshiftWarehouseConfig

if TYPE_CHECKING:
    from pathlib import Path

_AWS_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")
_AWS_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_AWS_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
_DEPLOYMENT_NAME = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
_DYNAMODB_TABLE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
_GCP_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_S3_BUCKET = re.compile(r"^(?![0-9]+(?:\.[0-9]+){3}$)[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_STATE_KEY = re.compile(r"^(?!/)(?!.*//)[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$")
_ECR_IMAGE = re.compile(
    r"^(?P<account>[0-9]{12})\.dkr\.ecr\.(?P<region>[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+)"
    r"\.amazonaws\.com/(?P<repository>[a-z0-9]+(?:[._/-][a-z0-9]+)*)"
    r"@sha256:(?P<digest>[0-9a-f]{64})$"
)


class AwsTerraformBootstrapError(RuntimeError):
    """Raised when the AWS saved-plan lifecycle cannot complete safely."""


class AwsTerraformBootstrap:
    """Plan or apply the packaged Fargate root using an existing S3 backend."""

    def __init__(self, infra_dir: Path) -> None:
        self._infra_dir = infra_dir.resolve()
        self._plan_path = self._infra_dir / "dander-aws.tfplan"

    def execute(
        self,
        *,
        project: str | None,
        profile_id: str = "gcp",
        warehouse_config: Mapping[str, object] | None = None,
        state_config: Mapping[str, object] | None = None,
        catalog_config: Mapping[str, object] | None = None,
        secret_config: Mapping[str, object] | None = None,
        deployment_name: str,
        state_bucket: str,
        state_key: str,
        state_region: str,
        lock_table: str,
        container_image: str,
        launcher_config: Mapping[str, object],
        runtime_cpu: int,
        runtime_memory: str,
        runtime_timeout_seconds: int,
        runtime_max_retries: int,
        runtime_batch_rows: int,
        require_guarded_free_tier: bool,
        pipelines: Mapping[str, Mapping[str, object]],
        apply: bool,
        aws_profile: str = "",
        name: str = "dander",
    ) -> Path:
        """Produce one immutable AWS plan and optionally apply that exact plan."""
        self._validate_backend(
            state_bucket=state_bucket,
            state_key=state_key,
            state_region=state_region,
            lock_table=lock_table,
            aws_profile=aws_profile,
        )
        if not _DEPLOYMENT_NAME.fullmatch(name):
            raise AwsTerraformBootstrapError(
                "AWS deployment name must contain 2-24 lowercase letters, numbers, or hyphens"
            )
        if not deployment_name.strip():
            raise AwsTerraformBootstrapError("Manifest deployment name must not be blank")
        expanded_pipelines = dict(pipelines)
        if not expanded_pipelines:
            raise AwsTerraformBootstrapError("Fargate planning requires at least one pipeline")
        try:
            validate_runtime_settings(
                cpu=runtime_cpu,
                memory=runtime_memory,
                timeout_seconds=runtime_timeout_seconds,
                max_retries=runtime_max_retries,
                batch_rows=runtime_batch_rows,
                require_guarded_free_tier=require_guarded_free_tier,
            )
            validate_terraform_pipelines(expanded_pipelines)
        except TerraformBootstrapError as error:
            raise AwsTerraformBootstrapError(str(error)) from error

        image_match = _ECR_IMAGE.fullmatch(container_image)
        if image_match is None:
            raise AwsTerraformBootstrapError(
                "Fargate requires an immutable ECR image in account.dkr.ecr.region.amazonaws.com/"
                "repository@sha256:digest form"
            )
        raw_launcher = dict(launcher_config)
        if raw_launcher.get("provider") != "fargate":
            raise AwsTerraformBootstrapError("AWS planning requires launcher.provider='fargate'")
        aws_account_id = raw_launcher.get("aws_account_id")
        region = raw_launcher.get("region")
        if not isinstance(aws_account_id, str) or not _AWS_ACCOUNT_ID.fullmatch(aws_account_id):
            raise AwsTerraformBootstrapError("Fargate launcher requires a valid AWS account id")
        if not isinstance(region, str) or not _AWS_REGION.fullmatch(region):
            raise AwsTerraformBootstrapError("Fargate launcher requires a valid AWS region")
        if image_match.group("account") != aws_account_id or image_match.group("region") != region:
            raise AwsTerraformBootstrapError(
                "ECR image account and region must match the selected Fargate launcher"
            )

        try:
            profile = self._profile_context(
                profile_id=profile_id,
                project=project,
                require_guarded_free_tier=require_guarded_free_tier,
                warehouse_config=warehouse_config,
                state_config=state_config,
                catalog_config=catalog_config,
                secret_config=secret_config,
            )
            launcher = build_launcher_runtime(
                launcher_config=raw_launcher,
                fargate_profile=profile,
            )
            platforms_config_json = self._runtime_platforms_config(
                profile=profile,
                deployment_name=deployment_name,
                launcher_config=raw_launcher,
                runtime_cpu=runtime_cpu,
                runtime_memory=runtime_memory,
                runtime_timeout_seconds=runtime_timeout_seconds,
                runtime_max_retries=runtime_max_retries,
                runtime_batch_rows=runtime_batch_rows,
                require_guarded_free_tier=require_guarded_free_tier,
                pipelines=expanded_pipelines,
            )
            projections = {
                pipeline_id: template.as_dict()
                for pipeline_id, template in launcher.templates.build(
                    ResolvedTemplateRequest(
                        pipelines=expanded_pipelines,
                        image=container_image,
                        profile_id=profile.profile_id,
                        cpu=runtime_cpu,
                        memory=runtime_memory,
                        deadline_seconds=runtime_timeout_seconds,
                        launcher_retry_count=runtime_max_retries,
                        batch_rows=runtime_batch_rows,
                        alert_target=None,
                        deployment_id=deployment_name,
                        platforms_config_json=platforms_config_json,
                    )
                ).items()
            }
        except (ExecutionProjectionError, ProviderFactoryError) as error:
            raise AwsTerraformBootstrapError(str(error)) from error

        plan_variables = [
            f"-var=name={name}",
            f"-var=aws_account_id={aws_account_id}",
            f"-var=region={region}",
            f"-var=ecr_repository_name={image_match.group('repository')}",
            "-var=execution_projections="
            + dumps(
                projections,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ]
        if profile.is_aws_native:
            plan_variables.append(
                "-var=aws_native_profile="
                + dumps(
                    self._aws_native_terraform_profile(profile),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )

        self._run(
            "terraform",
            "init",
            "-reconfigure",
            "-input=false",
            f"-backend-config=bucket={state_bucket}",
            f"-backend-config=key={state_key}",
            f"-backend-config=region={state_region}",
            "-backend-config=encrypt=true",
            f"-backend-config=dynamodb_table={lock_table}",
            aws_profile=aws_profile,
        )
        self._run(
            "terraform",
            "plan",
            "-input=false",
            *plan_variables,
            "-var=tags="
            + dumps(
                {
                    "dander-deployment": deployment_name,
                    "dander-profile": profile.profile_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            f"-out={self._plan_path.name}",
            aws_profile=aws_profile,
        )
        if apply:
            self._run(
                "terraform",
                "apply",
                "-input=false",
                self._plan_path.name,
                aws_profile=aws_profile,
            )
        return self._plan_path

    @staticmethod
    def _profile_context(
        *,
        profile_id: str,
        project: str | None,
        require_guarded_free_tier: bool,
        warehouse_config: Mapping[str, object] | None,
        state_config: Mapping[str, object] | None,
        catalog_config: Mapping[str, object] | None,
        secret_config: Mapping[str, object] | None,
    ) -> FargateProfileContext:
        registry = default_provider_registry()
        warehouse = registry.parse(
            ProviderKind.WAREHOUSE,
            warehouse_config or {"provider": "bigquery", "location": "US"},
        )
        state = registry.parse(
            ProviderKind.STATE,
            state_config or {"provider": "bigquery"},
        )
        catalog = registry.parse(
            ProviderKind.CATALOG,
            catalog_config or {"provider": "dataplex"},
        )
        secrets = registry.parse(
            ProviderKind.SECRETS,
            secret_config or {"provider": "gcp_secret_manager"},
        )
        is_gcp = (
            getattr(warehouse, "provider", None) == "bigquery"
            and getattr(state, "provider", None) == "bigquery"
            and getattr(catalog, "provider", None) == "dataplex"
            and getattr(secrets, "provider", None) == "gcp_secret_manager"
        )
        if is_gcp:
            if project is None or not _GCP_PROJECT.fullmatch(project):
                raise AwsTerraformBootstrapError(f"Invalid GCP data-plane project: {project!r}")
            gcp = GcpLauncherContext(
                project=project,
                require_guarded_free_tier=require_guarded_free_tier,
            )
        else:
            if project is not None:
                raise AwsTerraformBootstrapError(
                    "AWS-native Fargate planning does not accept a GCP project"
                )
            if require_guarded_free_tier:
                raise AwsTerraformBootstrapError(
                    "AWS-native Fargate cannot use the GCP guarded-free-tier preflight"
                )
            gcp = None
        try:
            return FargateProfileContext(
                profile_id=profile_id,
                warehouse=warehouse,  # type: ignore[arg-type]
                state=state,  # type: ignore[arg-type]
                catalog=catalog,  # type: ignore[arg-type]
                secrets=secrets,  # type: ignore[arg-type]
                gcp=gcp,
            )
        except ValueError as error:
            raise AwsTerraformBootstrapError(str(error)) from error

    @staticmethod
    def _aws_native_terraform_profile(
        profile: FargateProfileContext,
    ) -> dict[str, object]:
        if not profile.is_aws_native:
            raise AwsTerraformBootstrapError("AWS-native Terraform profile is not selected")
        warehouse = profile.warehouse
        catalog = profile.catalog
        assert isinstance(warehouse, RedshiftWarehouseConfig)
        assert isinstance(catalog, GlueCatalogConfig)
        return {
            "redshift_deployment": warehouse.deployment,
            "redshift_cluster_identifier": warehouse.cluster_identifier,
            "redshift_workgroup_name": warehouse.workgroup_name,
            "redshift_database": warehouse.database,
            "redshift_db_user": warehouse.db_user,
            "staging_bucket": warehouse.staging_bucket,
            "staging_prefix": warehouse.staging_prefix,
            "glue_catalog_id": catalog.catalog_id,
            "glue_database_prefix": catalog.database_prefix,
        }

    @staticmethod
    def _runtime_platforms_config(
        *,
        profile: FargateProfileContext,
        deployment_name: str,
        launcher_config: Mapping[str, object],
        runtime_cpu: int,
        runtime_memory: str,
        runtime_timeout_seconds: int,
        runtime_max_retries: int,
        runtime_batch_rows: int,
        require_guarded_free_tier: bool,
        pipelines: Mapping[str, Mapping[str, object]],
    ) -> str:
        """Render one validated deployment overlay independent of image-baked coordinates."""
        launcher = FargateLauncherConfig.model_validate(launcher_config)
        deployment_pipelines: dict[str, object] = {}
        for pipeline_id, pipeline in sorted(pipelines.items()):
            secret_env = pipeline["secret_env"]
            if not isinstance(secret_env, Mapping):  # pragma: no cover - validated upstream
                raise AwsTerraformBootstrapError("Pipeline secret bindings are invalid")
            resources = {
                "job": pipeline["job_name"],
                "runtime_service_account": pipeline["runtime_service_account_id"],
                "scheduler_service_account": pipeline["scheduler_service_account_id"],
            }
            deployment_pipelines[pipeline_id] = {
                "schedule": pipeline["schedule"],
                "time_zone": pipeline["time_zone"],
                "paused": pipeline["paused"],
                "secret_bindings": dict(sorted(secret_env.items())),
                "resources": resources,
            }
        document = {
            "version": 1,
            "platforms": {
                profile.profile_id: {
                    "warehouse": profile.warehouse.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                    "state": profile.state.model_dump(mode="json", exclude_none=True),
                    "catalog": profile.catalog.model_dump(mode="json", exclude_none=True),
                    "secrets": profile.secrets.model_dump(mode="json", exclude_none=True),
                }
            },
            "deployments": {
                deployment_name: {
                    "platform": profile.profile_id,
                    "launcher": launcher.model_dump(mode="json", exclude_none=True),
                    "runtime": {
                        "cpu": runtime_cpu,
                        "memory": runtime_memory,
                        "timeout_seconds": runtime_timeout_seconds,
                        "max_retries": runtime_max_retries,
                        "batch_rows": runtime_batch_rows,
                    },
                    "safety": {
                        "require_guarded_free_tier": require_guarded_free_tier,
                    },
                    "pipelines": deployment_pipelines,
                }
            },
        }
        from dander.project.portable_config import DanderPlatforms

        DanderPlatforms.model_validate(document)
        return dumps(document, sort_keys=True, separators=(",", ":"))

    def apply_saved_plan(
        self,
        *,
        state_bucket: str,
        state_key: str,
        state_region: str,
        lock_table: str,
        aws_profile: str = "",
    ) -> Path:
        """Apply only the saved plan produced by ``execute``."""
        self._validate_backend(
            state_bucket=state_bucket,
            state_key=state_key,
            state_region=state_region,
            lock_table=lock_table,
            aws_profile=aws_profile,
        )
        if not self._plan_path.is_file() or self._plan_path.is_symlink():
            raise AwsTerraformBootstrapError(f"Saved AWS plan is missing: {self._plan_path}")
        self._run(
            "terraform",
            "init",
            "-reconfigure",
            "-input=false",
            f"-backend-config=bucket={state_bucket}",
            f"-backend-config=key={state_key}",
            f"-backend-config=region={state_region}",
            "-backend-config=encrypt=true",
            f"-backend-config=dynamodb_table={lock_table}",
            aws_profile=aws_profile,
        )
        self._run(
            "terraform",
            "apply",
            "-input=false",
            self._plan_path.name,
            aws_profile=aws_profile,
        )
        return self._plan_path

    @staticmethod
    def _validate_backend(
        *,
        state_bucket: str,
        state_key: str,
        state_region: str,
        lock_table: str,
        aws_profile: str,
    ) -> None:
        if not _S3_BUCKET.fullmatch(state_bucket):
            raise AwsTerraformBootstrapError(f"Invalid S3 state bucket: {state_bucket!r}")
        if not _STATE_KEY.fullmatch(state_key):
            raise AwsTerraformBootstrapError(f"Invalid S3 state key: {state_key!r}")
        if not _AWS_REGION.fullmatch(state_region):
            raise AwsTerraformBootstrapError(f"Invalid S3 state region: {state_region!r}")
        if not _DYNAMODB_TABLE.fullmatch(lock_table):
            raise AwsTerraformBootstrapError(f"Invalid DynamoDB lock table: {lock_table!r}")
        if aws_profile and not _AWS_PROFILE.fullmatch(aws_profile):
            raise AwsTerraformBootstrapError(f"Invalid AWS profile: {aws_profile!r}")

    def _run(self, *args: str, aws_profile: str) -> None:
        environment = os.environ.copy()
        if aws_profile:
            environment["AWS_PROFILE"] = aws_profile
        try:
            subprocess.run(
                args,
                cwd=self._infra_dir,
                check=True,
                env=environment,
            )
        except FileNotFoundError as error:
            raise AwsTerraformBootstrapError(
                "Terraform is not installed or is not available on PATH"
            ) from error
        except subprocess.CalledProcessError as error:
            command = " ".join(args[:2])
            raise AwsTerraformBootstrapError(
                f"{command} failed with exit code {error.returncode}"
            ) from error


__all__ = ["AwsTerraformBootstrap", "AwsTerraformBootstrapError"]
