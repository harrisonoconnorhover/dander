"""Exact project-to-Cloud Run Job bindings used by hosted Control."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.project import ProjectConfigError, load_project_config

if TYPE_CHECKING:
    from pathlib import Path

_GCP_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_GCP_REGION = re.compile(r"^[a-z]+(?:-[a-z0-9]+)+[0-9]$")
_RESOURCE_ID = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")
_PIPELINE_ID = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


class CloudRunOperationError(RuntimeError):
    """A Cloud Run binding is missing or inconsistent with the selected project."""


@dataclass(frozen=True, slots=True)
class CloudRunBinding:
    """One immutable plan revision bound to one existing Cloud Run Job."""

    project_id: str
    region: str
    deployment_name: str
    profile_id: str
    pipeline_id: str
    job_name: str
    runtime_service_account: str

    @classmethod
    def from_project(
        cls,
        *,
        config: Path,
        platforms_config: Path | None = None,
        deployment: str,
        pipeline_id: str,
        project_id: str,
    ) -> CloudRunBinding:
        """Resolve one Cloud Run Job from the validated portable project configuration."""
        if _GCP_PROJECT.fullmatch(project_id) is None:
            raise CloudRunOperationError("Cloud Run project id is invalid")
        if _PIPELINE_ID.fullmatch(pipeline_id) is None:
            raise CloudRunOperationError("Cloud Run pipeline id is invalid")
        resolved_config = config.expanduser().resolve()
        try:
            manifest = load_project_config(
                resolved_config,
                platforms_path=(
                    platforms_config.expanduser().resolve()
                    if platforms_config is not None
                    else None
                ),
                deployment=deployment,
            )
            if manifest.launcher_provider != "cloud_run":
                raise ProjectConfigError(
                    f"Deployment {deployment!r} does not select launcher.provider='cloud_run'"
                )
            pipeline = manifest.terraform_pipelines()[pipeline_id]
            manifest.validate_references(resolved_config.parent)
            launcher = manifest.resolved_launcher_config()
        except KeyError as error:
            raise CloudRunOperationError(
                f"Pipeline {pipeline_id!r} is not declared in the Cloud Run deployment"
            ) from error
        except ProjectConfigError as error:
            raise CloudRunOperationError(str(error)) from error
        region = launcher.get("region")
        job_name = pipeline.get("job_name")
        service_account_id = pipeline.get("runtime_service_account_id")
        if not isinstance(region, str) or _GCP_REGION.fullmatch(region) is None:
            raise CloudRunOperationError("Cloud Run deployment region is invalid")
        if (
            not isinstance(job_name, str)
            or len(job_name) > 46
            or _RESOURCE_ID.fullmatch(job_name) is None
        ):
            raise CloudRunOperationError(
                "Cloud Run Job name must leave room for a deterministic execution suffix"
            )
        if not isinstance(service_account_id, str):
            raise CloudRunOperationError("Cloud Run runtime service account is invalid")
        service_account = f"{service_account_id}@{project_id}.iam.gserviceaccount.com"
        return cls(
            project_id=project_id,
            region=region,
            deployment_name=deployment,
            profile_id=manifest.platform_name,
            pipeline_id=pipeline_id,
            job_name=job_name,
            runtime_service_account=service_account,
        )

    @property
    def job_resource(self) -> str:
        return f"projects/{self.project_id}/locations/{self.region}/jobs/{self.job_name}"

    def execution_resource(self, token: str) -> str:
        if not token or len(f"{self.job_name}-{token}") > 63 or not token.isalnum():
            raise CloudRunOperationError("Cloud Run execution token is invalid")
        return f"{self.job_resource}/executions/{self.job_name}-{token}"


__all__ = ["CloudRunBinding", "CloudRunOperationError"]
