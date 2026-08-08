"""Saved-plan Terraform lifecycle for one manifest-defined AWS Fargate deployment."""

from __future__ import annotations

import os
import re
import subprocess
from json import dumps
from typing import TYPE_CHECKING

from dander.bootstrap.terraform import (
    TerraformBootstrapError,
    build_launcher_runtime,
    validate_runtime_settings,
    validate_terraform_pipelines,
)
from dander.deployment import ExecutionProjectionError
from dander.providers import ProviderFactoryError

if TYPE_CHECKING:
    from collections.abc import Mapping
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
        project: str,
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
        if not _GCP_PROJECT.fullmatch(project):
            raise AwsTerraformBootstrapError(f"Invalid GCP data-plane project: {project!r}")
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
            launcher = build_launcher_runtime(launcher_config=raw_launcher)
            projections = {
                pipeline_id: template.as_dict()
                for pipeline_id, template in launcher.templates.build(
                    expanded_pipelines,
                    image=container_image,
                    project=project,
                    cpu=runtime_cpu,
                    memory=runtime_memory,
                    deadline_seconds=runtime_timeout_seconds,
                    launcher_retry_count=runtime_max_retries,
                    batch_rows=runtime_batch_rows,
                    require_guarded_free_tier=require_guarded_free_tier,
                    alert_target=None,
                ).items()
            }
        except (ExecutionProjectionError, ProviderFactoryError) as error:
            raise AwsTerraformBootstrapError(str(error)) from error

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
            f"-var=name={name}",
            f"-var=aws_account_id={aws_account_id}",
            f"-var=region={region}",
            f"-var=ecr_repository_name={image_match.group('repository')}",
            "-var=execution_projections="
            f"{dumps(projections, sort_keys=True, separators=(',', ':'))}",
            "-var=tags="
            + dumps(
                {
                    "dander-deployment": deployment_name,
                    "dander-profile": "gcp",
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
