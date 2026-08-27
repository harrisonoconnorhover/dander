"""Immutable execution-plan bindings for Managed Service for Apache Spark batches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dander.deployment.projection import ExecutionTemplate

_GCP_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_GCP_REGION = re.compile(r"^[a-z]+(?:-[a-z0-9]+)+[0-9]$")
_GCS_DRIVER = re.compile(
    r"^gs://(?P<bucket>[a-z0-9][a-z0-9._-]{1,220})/"
    r"[A-Za-z0-9._/-]*[0-9a-f]{64}\.py$"
)
_GCS_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}$")
_RUNTIME_VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+$")
_PROFILE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_PIPELINE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@"
    r"(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])\.iam\.gserviceaccount\.com$"
)
_SUBNETWORK = re.compile(
    r"^projects/(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])/regions/"
    r"(?P<region>[a-z]+(?:-[a-z0-9]+)+[0-9])/subnetworks/"
    r"[a-z][a-z0-9-]{0,61}[a-z0-9]$"
)


class DataprocServerlessOperationError(RuntimeError):
    """A serverless Spark plan binding is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class DataprocServerlessBinding:
    """One immutable plan revision's fixed Managed Spark batch coordinates."""

    project_id: str
    region: str
    profile_id: str
    pipeline_id: str
    runtime_service_account: str
    main_python_file_uri: str
    runtime_version: str
    staging_bucket: str
    subnetwork_uri: str | None = None

    def __post_init__(self) -> None:
        service_account = _SERVICE_ACCOUNT.fullmatch(self.runtime_service_account)
        driver = _GCS_DRIVER.fullmatch(self.main_python_file_uri)
        if (
            _GCP_PROJECT.fullmatch(self.project_id) is None
            or _GCP_REGION.fullmatch(self.region) is None
            or _PROFILE.fullmatch(self.profile_id) is None
            or _PIPELINE_ID.fullmatch(self.pipeline_id) is None
            or service_account is None
            or service_account.group("project") != self.project_id
            or driver is None
            or driver.group("bucket") != self.staging_bucket
            or _GCS_BUCKET.fullmatch(self.staging_bucket) is None
            or _RUNTIME_VERSION.fullmatch(self.runtime_version) is None
        ):
            raise DataprocServerlessOperationError("Managed Spark binding coordinates are invalid")
        if self.subnetwork_uri is not None:
            subnetwork = _SUBNETWORK.fullmatch(self.subnetwork_uri)
            if (
                subnetwork is None
                or subnetwork.group("project") != self.project_id
                or subnetwork.group("region") != self.region
            ):
                raise DataprocServerlessOperationError(
                    "Managed Spark subnetwork must match its exact project and region"
                )

    @classmethod
    def from_execution_template(
        cls,
        template: ExecutionTemplate,
        *,
        project_id: str,
    ) -> DataprocServerlessBinding:
        """Resolve a fixed batch binding from revision-covered template extensions."""
        if _GCP_PROJECT.fullmatch(project_id) is None:
            raise DataprocServerlessOperationError("Managed Spark project id is invalid")
        extensions = dict(template.extensions)
        expected = {
            "spark.main_python_file_uri",
            "spark.runtime_version",
            "spark.staging_bucket",
        }
        spark_extensions = {key for key in extensions if key.startswith("spark.")}
        if spark_extensions != expected:
            raise DataprocServerlessOperationError(
                "Managed Spark requires exactly the immutable driver, runtime, and "
                "staging extensions"
            )
        main_python_file_uri = extensions["spark.main_python_file_uri"]
        runtime_version = extensions["spark.runtime_version"]
        staging_bucket = extensions["spark.staging_bucket"]
        driver = _GCS_DRIVER.fullmatch(main_python_file_uri)
        if driver is None:
            raise DataprocServerlessOperationError(
                "Managed Spark driver must be a content-addressed Cloud Storage Python file"
            )
        if _RUNTIME_VERSION.fullmatch(runtime_version) is None:
            raise DataprocServerlessOperationError("Managed Spark runtime version is invalid")
        if _GCS_BUCKET.fullmatch(staging_bucket) is None:
            raise DataprocServerlessOperationError("Managed Spark staging bucket is invalid")

        region = _artifact_region(template.image)
        subnetwork_uri = template.network.placement
        if subnetwork_uri is not None:
            subnetwork = _SUBNETWORK.fullmatch(subnetwork_uri)
            if (
                subnetwork is None
                or subnetwork.group("project") != project_id
                or subnetwork.group("region") != region
            ):
                raise DataprocServerlessOperationError(
                    "Managed Spark subnetwork must match its exact project and region"
                )
        if driver.group("bucket") != staging_bucket:
            raise DataprocServerlessOperationError(
                "Managed Spark driver and staging data must use the same bounded bucket"
            )
        return cls(
            project_id=project_id,
            region=region,
            profile_id=template.profile_id,
            pipeline_id=template.pipeline_id,
            runtime_service_account=template.workload_identity,
            main_python_file_uri=main_python_file_uri,
            runtime_version=runtime_version,
            staging_bucket=staging_bucket,
            subnetwork_uri=subnetwork_uri,
        )

    @property
    def parent_resource(self) -> str:
        return f"projects/{self.project_id}/locations/{self.region}"

    def batch_resource(self, batch_id: str) -> str:
        if not batch_id or len(batch_id) > 63 or not batch_id.replace("-", "").isalnum():
            raise DataprocServerlessOperationError("Managed Spark batch id is invalid")
        return f"{self.parent_resource}/batches/{batch_id}"


def _artifact_region(image: str) -> str:
    host = image.partition("/")[0]
    suffix = "-docker.pkg.dev"
    if not host.endswith(suffix):
        raise DataprocServerlessOperationError(
            "Managed Spark image must use regional Artifact Registry"
        )
    region = host.removesuffix(suffix)
    if _GCP_REGION.fullmatch(region) is None:
        raise DataprocServerlessOperationError("Managed Spark image region is invalid")
    return region


__all__ = ["DataprocServerlessBinding", "DataprocServerlessOperationError"]
