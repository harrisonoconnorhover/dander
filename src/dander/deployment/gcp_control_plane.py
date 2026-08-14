"""Deterministic GCP Cloud Run projection and read-only D7 verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dander.control.auth import HostedOIDCDeploymentInput, project_hosted_oidc
from dander.deployment.service import (
    ControlServiceIngress,
    ControlServiceObservability,
    ControlServiceProbes,
    ControlServiceResources,
    ControlServiceScaling,
    GCSGraphStoreBinding,
    IngressVisibility,
    ResolvedControlServiceRequest,
)

GCP_CONTROL_PLANE_SCHEMA: Final = "io.dander.gcp-control-plane/v1"
GCP_CONTROL_PORT: Final = 8770
GCP_DRUFF_PORT: Final = 8080
GCP_CONFIG_ROOT: Final = "/etc/dander"

_IMAGE = re.compile(r"^[a-z0-9.-]+/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_PROJECT_NUMBER = re.compile(r"^[0-9]{6,20}$")
_REGION = re.compile(r"^[a-z]+-[a-z]+[0-9]$")
_SERVICE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]"
    r"\.iam\.gserviceaccount\.com$"
)
_SECRET_ALIAS = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SECRET_RESOURCE = re.compile(
    r"^projects/(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9]|[0-9]{6,20})/"
    r"secrets/(?P<secret>[A-Za-z0-9_-]{1,255})$"
)
_FILES: Final = frozenset(
    {
        "Caddyfile",
        "active.tfvars.json",
        "bootstrap.json",
        "control-graph-store.json",
        "control-oidc.json",
        "deployment.json",
        "public-client.json",
        "rollback.tfvars.json",
    }
)

_CSP: Final = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
    "form-action 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'self' https:; worker-src 'self' blob:; "
    "frame-src 'none'; media-src 'none'; manifest-src 'self'"
)

_CADDYFILE = """{
  admin off
  auto_https off
  persist_config off
}

:8080 {
  encode zstd gzip

  route {
    header {
      Cache-Control "no-store"
      Content-Security-Policy "__DANDER_CSP__"
      Cross-Origin-Opener-Policy "same-origin"
      Cross-Origin-Resource-Policy "same-origin"
      Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
      Referrer-Policy "no-referrer"
      Strict-Transport-Security "max-age=31536000; includeSubDomains"
      X-Content-Type-Options "nosniff"
      X-Frame-Options "DENY"
    }

    handle /healthz {
      header Content-Type application/json
      respond `{\"status\":\"ok\"}` 200
    }

    handle /readyz {
      header Content-Type application/json
      respond `{\"status\":\"ready\"}` 200
    }

    handle /bootstrap.json {
      root * /etc/dander/bootstrap
      file_server
    }

    handle {
      root * /app
      @immutable path /_next/static/*
      header @immutable Cache-Control "public, max-age=31536000, immutable"
      try_files {path}.html {path}
      file_server
    }
  }
}
""".replace("__DANDER_CSP__", _CSP)


class GCPControlPlaneError(ValueError):
    """The GCP profile, projection, or live deployment is invalid."""


class GCPControlPlaneInput(BaseModel):
    """One closed immutable non-secret input for the GCP D7 profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=6, max_length=30)
    project_number: str = Field(min_length=6, max_length=20)
    region: str = Field(min_length=1, max_length=63)
    bootstrap_service_account: str = Field(min_length=1, max_length=128)
    state_bucket: str = Field(min_length=3, max_length=63)
    state_prefix: str = Field(min_length=1, max_length=512)
    graph_bucket: str = Field(min_length=3, max_length=63)
    control_service_name: str = "dander-control-d7"
    druff_service_name: str = "druff-control-d7"
    dander_image: str = Field(min_length=1, max_length=2048)
    dander_rollback_image: str = Field(min_length=1, max_length=2048)
    druff_image: str = Field(min_length=1, max_length=2048)
    druff_rollback_image: str = Field(min_length=1, max_length=2048)
    oidc: HostedOIDCDeploymentInput

    @field_validator("project_id")
    @classmethod
    def validate_project(cls, value: str) -> str:
        if _PROJECT.fullmatch(value) is None:
            raise ValueError("GCP project id is invalid.")
        return value

    @field_validator("project_number")
    @classmethod
    def validate_project_number(cls, value: str) -> str:
        if _PROJECT_NUMBER.fullmatch(value) is None:
            raise ValueError("GCP project number is invalid.")
        return value

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        if _REGION.fullmatch(value) is None:
            raise ValueError("GCP region is invalid.")
        return value

    @field_validator("bootstrap_service_account")
    @classmethod
    def validate_bootstrap_identity(cls, value: str) -> str:
        if _SERVICE_ACCOUNT.fullmatch(value) is None:
            raise ValueError("GCP bootstrap service account is invalid.")
        return value

    @field_validator("control_service_name", "druff_service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        if _SERVICE.fullmatch(value) is None:
            raise ValueError("GCP service names must be 6-30 character DNS labels.")
        return value

    @field_validator("state_bucket", "graph_bucket")
    @classmethod
    def validate_bucket(cls, value: str) -> str:
        if _BUCKET.fullmatch(value) is None or ".." in value:
            raise ValueError("GCP bucket name is invalid.")
        return value

    @field_validator("state_prefix")
    @classmethod
    def validate_state_prefix(cls, value: str) -> str:
        if (
            value.startswith("/")
            or value.endswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,511}", value)
        ):
            raise ValueError("GCP state prefix is invalid.")
        if value in {"dander/state", "dander/bootstrap-admin/state"}:
            raise ValueError("GCP D7 state must not share a retained root prefix.")
        return value

    @field_validator(
        "dander_image",
        "dander_rollback_image",
        "druff_image",
        "druff_rollback_image",
    )
    @classmethod
    def validate_image(cls, value: str) -> str:
        if _IMAGE.fullmatch(value) is None:
            raise ValueError("GCP control-plane images must use immutable sha256 references.")
        return value

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        _validate_rollback_pair(self.dander_image, self.dander_rollback_image, "Dander")
        _validate_rollback_pair(self.druff_image, self.druff_rollback_image, "Druff")
        if self.state_bucket == self.graph_bucket:
            raise ValueError("GCP state and graph buckets must differ.")
        expected = {
            "api_url": self.control_url,
            "redirect_uri": f"{self.druff_url}/auth/callback",
            "logout_uri": f"{self.druff_url}/signed-out",
            "allowed_origins": (self.druff_url,),
        }
        observed = {
            "api_url": self.oidc.api_url,
            "redirect_uri": self.oidc.redirect_uri,
            "logout_uri": self.oidc.logout_uri,
            "allowed_origins": self.oidc.allowed_origins,
        }
        if observed != expected:
            raise ValueError("GCP hosted OIDC routes must use the exact Cloud Run origins.")
        return self

    @property
    def control_url(self) -> str:
        """Return the predictable Cloud Run Control origin."""
        return _cloud_run_url(self.control_service_name, self.project_number, self.region)

    @property
    def druff_url(self) -> str:
        """Return the predictable Cloud Run Druff origin."""
        return _cloud_run_url(self.druff_service_name, self.project_number, self.region)

    @property
    def control_service_account(self) -> str:
        return f"{self.control_service_name}@{self.project_id}.iam.gserviceaccount.com"

    @property
    def druff_service_account(self) -> str:
        return f"{self.druff_service_name}@{self.project_id}.iam.gserviceaccount.com"


def project_gcp_control_service(source: GCPControlPlaneInput) -> ResolvedControlServiceRequest:
    """Project the D6 service contract for Cloud Run and GCS."""
    return ResolvedControlServiceRequest(
        service_id="dander_control",
        profile_id="gcp_cloud_run",
        image=source.dander_image,
        port=GCP_CONTROL_PORT,
        probes=ControlServiceProbes(),
        resources=ControlServiceResources(cpu_millis=1000, memory_mib=512),
        scaling=ControlServiceScaling(
            minimum_instances=0,
            maximum_instances=1,
            shutdown_grace_seconds=30,
        ),
        environment=(),
        secret_bindings=(),
        workload_identity=f"gcp-service-account://{source.control_service_account}",
        ingress=ControlServiceIngress(visibility=IngressVisibility.PUBLIC),
        oidc=source.oidc,
        oidc_config_path=f"{GCP_CONFIG_ROOT}/oidc/control-oidc.json",
        graph_store_config_path=(f"{GCP_CONFIG_ROOT}/graph-store/control-graph-store.json"),
        graph_store=GCSGraphStoreBinding(bucket=source.graph_bucket),
        observability=ControlServiceObservability(
            log_destination="gcp-cloud-logging",
            alert_target=None,
            retention_days=30,
        ),
        rollback_digest=_digest(source.dander_rollback_image),
    )


def render_gcp_control_plane(source: GCPControlPlaneInput) -> dict[str, str]:
    """Return the exact provider files without contacting Google Cloud or Terraform."""
    oidc = project_hosted_oidc(source.oidc)
    service = project_gcp_control_service(source)
    oidc_json = _json(source.oidc.model_dump(mode="json"))
    graph_json = _json(service.graph_store.as_dict())
    bootstrap_json = _json(oidc.bootstrap.model_dump(mode="json"))
    public_client_json = _json(oidc.public_client.model_dump(mode="json"))
    common: dict[str, object] = {
        "project_id": source.project_id,
        "project_number": source.project_number,
        "region": source.region,
        "bootstrap_service_account": source.bootstrap_service_account,
        "control_service_name": source.control_service_name,
        "druff_service_name": source.druff_service_name,
        "graph_bucket": source.graph_bucket,
        "control_args": list(service.command),
        "control_oidc_json": oidc_json,
        "graph_store_json": graph_json,
        "bootstrap_json": bootstrap_json,
        "druff_caddyfile": _CADDYFILE,
    }
    active = {**common, "dander_image": source.dander_image, "druff_image": source.druff_image}
    rollback = {
        **common,
        "dander_image": source.dander_rollback_image,
        "druff_image": source.druff_rollback_image,
    }
    manifest = {
        "schema": GCP_CONTROL_PLANE_SCHEMA,
        "project_id": source.project_id,
        "project_number": source.project_number,
        "region": source.region,
        "state_bucket": source.state_bucket,
        "state_prefix": source.state_prefix,
        "graph_bucket": source.graph_bucket,
        "control_url": source.control_url,
        "druff_url": source.druff_url,
        "control_service_account": source.control_service_account,
        "druff_service_account": source.druff_service_account,
        "service": service.as_dict(),
        "active_images": {
            "dander": source.dander_image,
            "druff": source.druff_image,
        },
        "rollback_images": {
            "dander": source.dander_rollback_image,
            "druff": source.druff_rollback_image,
        },
        "config_sha256": {
            "control_oidc": _sha256(oidc_json),
            "graph_store": _sha256(graph_json),
            "bootstrap": _sha256(bootstrap_json),
            "druff_caddy": _sha256(_CADDYFILE),
        },
        "graph_soft_delete_retention_seconds": 0,
    }
    return {
        "Caddyfile": _CADDYFILE,
        "active.tfvars.json": _json(active),
        "rollback.tfvars.json": _json(rollback),
        "bootstrap.json": bootstrap_json,
        "control-graph-store.json": graph_json,
        "control-oidc.json": oidc_json,
        "deployment.json": _json(manifest),
        "public-client.json": public_client_json,
    }


def write_gcp_control_plane(
    source: GCPControlPlaneInput, *, output_directory: Path
) -> tuple[Path, ...]:
    """Atomically write only the closed non-secret GCP projection."""
    destination = output_directory.expanduser().resolve(strict=False)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    rendered = render_gcp_control_plane(source)
    if frozenset(rendered) != _FILES:  # pragma: no cover
        raise GCPControlPlaneError("GCP projection produced an unexpected file set.")
    written: list[Path] = []
    for name, content in sorted(rendered.items()):
        path = destination / name
        _atomic_write(path, content)
        written.append(path)
    return tuple(written)


def preflight_gcp_control_plane(
    source: GCPControlPlaneInput,
    *,
    output_directory: Path,
    terraform_root: Path,
) -> dict[str, object]:
    """Verify the exact projection and Terraform root without backend or cloud mutation."""
    destination = output_directory.expanduser().resolve(strict=True)
    root = terraform_root.expanduser().resolve(strict=True)
    expected = render_gcp_control_plane(source)
    _verify_projection_files(destination, expected)
    terraform_data = destination / "terraform-data"
    terraform_data.mkdir(mode=0o700, exist_ok=True)
    terraform_data.chmod(0o700)
    environment = {**os.environ, "TF_DATA_DIR": str(terraform_data)}
    _run(("terraform", "-chdir=" + str(root), "fmt", "-check"), environment=environment)
    _run(
        ("terraform", "-chdir=" + str(root), "init", "-backend=false", "-input=false"),
        environment=environment,
    )
    _run(("terraform", "-chdir=" + str(root), "validate"), environment=environment)
    return {
        "schema": GCP_CONTROL_PLANE_SCHEMA,
        "status": "passed",
        "checks": [
            "projection-current",
            "images-immutable-distinct",
            "cloud-run-urls-deterministic",
            "graph-soft-delete-disabled",
            "terraform-fmt",
            "terraform-validate",
        ],
    }


def verify_live_gcp_control_plane(
    source: GCPControlPlaneInput,
    *,
    output_directory: Path,
    environment: Literal["active", "rollback"] = "active",
) -> dict[str, object]:
    """Read and verify one exact live GCP deployment without returning config or credentials."""
    rendered = render_gcp_control_plane(source)
    destination = output_directory.expanduser().resolve(strict=True)
    _verify_projection_files(destination, rendered)
    images = (
        {"control": source.dander_image, "druff": source.druff_image}
        if environment == "active"
        else {
            "control": source.dander_rollback_image,
            "druff": source.druff_rollback_image,
        }
    )
    control = _mapping(
        _gcloud_json(source, "run", "services", "describe", source.control_service_name),
        "Control service",
    )
    druff = _mapping(
        _gcloud_json(source, "run", "services", "describe", source.druff_service_name),
        "Druff service",
    )
    control_versions = _verify_service(
        control,
        source=source,
        workload="control",
        expected_url=source.control_url,
        image=images["control"],
    )
    druff_versions = _verify_service(
        druff,
        source=source,
        workload="druff",
        expected_url=source.druff_url,
        image=images["druff"],
    )
    bucket = _mapping(
        _gcloud_json(
            source,
            "storage",
            "buckets",
            "describe",
            f"gs://{source.graph_bucket}",
            regional=False,
        ),
        "graph bucket",
    )
    _verify_graph_bucket(bucket, source)
    bucket_policy = _mapping(
        _gcloud_json(
            source,
            "storage",
            "buckets",
            "get-iam-policy",
            f"gs://{source.graph_bucket}",
            regional=False,
        ),
        "graph bucket policy",
    )
    _verify_bucket_policy(bucket_policy, source)
    project_policy = _mapping(
        _gcloud_json(
            source,
            "projects",
            "get-iam-policy",
            source.project_id,
            regional=False,
        ),
        "project policy",
    )
    _verify_no_project_roles(project_policy, source)
    for service_account in (source.control_service_account, source.druff_service_account):
        keys = _gcloud_json(
            source,
            "iam",
            "service-accounts",
            "keys",
            "list",
            f"--iam-account={service_account}",
            regional=False,
        )
        if not isinstance(keys, list) or any(
            isinstance(key, Mapping) and key.get("keyType") == "USER_MANAGED" for key in keys
        ):
            raise GCPControlPlaneError("GCP service identity has a user-managed key.")
    mounted_versions = {**control_versions, **druff_versions}
    _verify_secret_hashes(source, rendered, mounted_versions)
    health, _ = _http(source.control_url + "/healthz")
    ready, _ = _http(source.control_url + "/readyz")
    druff_health, _ = _http(source.druff_url + "/healthz")
    druff_ready, _ = _http(source.druff_url + "/readyz")
    bootstrap, bootstrap_headers = _http(source.druff_url + "/bootstrap.json")
    callback, callback_headers = _http(source.druff_url + "/auth/callback")
    signed_out, signed_out_headers = _http(source.druff_url + "/signed-out")
    unauthorized, _unauthorized_headers, status = _http_error(
        source.control_url + "/v1/capabilities"
    )
    if json.loads(health) != {"status": "ok"} or json.loads(ready) != {"status": "ready"}:
        raise GCPControlPlaneError("GCP Control probes are invalid.")
    if json.loads(druff_health) != {"status": "ok"} or json.loads(druff_ready) != {
        "status": "ready"
    }:
        raise GCPControlPlaneError("GCP Druff probes are invalid.")
    if bootstrap != rendered["bootstrap.json"].encode():
        raise GCPControlPlaneError("GCP served bootstrap differs from the projection.")
    if status != 401 or json.loads(unauthorized).get("error", {}).get("code") != "unauthorized":
        raise GCPControlPlaneError("GCP unauthenticated Control API does not fail closed.")
    if (
        not callback
        or not signed_out
        or not callback_headers.get("content-security-policy")
        or not signed_out_headers.get("content-security-policy")
    ):
        raise GCPControlPlaneError("GCP Druff callback routes or security policy are missing.")
    if bootstrap_headers.get("cache-control") != "no-store" or any(
        not bootstrap_headers.get(name)
        for name in ("content-security-policy", "strict-transport-security")
    ):
        raise GCPControlPlaneError("GCP Druff bootstrap headers are incomplete.")
    return {
        "schema": GCP_CONTROL_PLANE_SCHEMA,
        "status": "passed",
        "environment": environment,
        "checks": [
            "cloud-run-services-exact-ready-public",
            "serving-revisions-current-exclusive",
            "images-and-identities-exact",
            "numeric-secret-versions-mounted",
            "graph-bucket-private-versioned-soft-delete-disabled",
            "bucket-iam-control-only",
            "project-iam-no-runtime-roles",
            "service-accounts-keyless",
            "startup-config-hashes-exact",
            "control-and-druff-probes-ready",
            "unauthenticated-api-rejected",
            "bootstrap-and-callback-routes-current",
        ],
    }


def _verify_service(
    payload: Mapping[str, Any],
    *,
    source: GCPControlPlaneInput,
    workload: Literal["control", "druff"],
    expected_url: str,
    image: str,
) -> dict[str, str]:
    metadata = _mapping(payload.get("metadata"), "Cloud Run metadata")
    annotations = _mapping(metadata.get("annotations"), "Cloud Run annotations")
    try:
        urls = json.loads(str(annotations.get("run.googleapis.com/urls")))
    except ValueError as error:
        raise GCPControlPlaneError("Cloud Run URL inventory is invalid.") from error
    if (
        metadata.get("name")
        != (source.control_service_name if workload == "control" else source.druff_service_name)
        or annotations.get("run.googleapis.com/ingress") != "all"
        or annotations.get("run.googleapis.com/invoker-iam-disabled") != "true"
        or not isinstance(urls, list)
        or expected_url not in urls
    ):
        raise GCPControlPlaneError(f"GCP {workload} service exposure differs.")
    status = _mapping(payload.get("status"), "Cloud Run status")
    conditions = status.get("conditions")
    ready_conditions = (
        [item for item in conditions if isinstance(item, Mapping) and item.get("type") == "Ready"]
        if isinstance(conditions, list)
        else []
    )
    generation = _positive_integer(metadata.get("generation"), "Cloud Run generation")
    observed_generation = _positive_integer(
        status.get("observedGeneration"), "Cloud Run observed generation"
    )
    created_revision = status.get("latestCreatedRevisionName")
    ready_revision = status.get("latestReadyRevisionName")
    traffic = status.get("traffic")
    traffic_target = (
        _mapping(traffic[0], "Cloud Run traffic target")
        if isinstance(traffic, list) and len(traffic) == 1
        else {}
    )
    if (
        len(ready_conditions) != 1
        or ready_conditions[0].get("status") != "True"
        or observed_generation != generation
        or not isinstance(created_revision, str)
        or not created_revision
        or created_revision != ready_revision
        or traffic_target.get("revisionName") != ready_revision
        or _positive_integer(traffic_target.get("percent"), "Cloud Run traffic percent") != 100
        or traffic_target.get("latestRevision") is not True
    ):
        raise GCPControlPlaneError(f"GCP {workload} serving revision is not current and exclusive.")
    template = _mapping(_nested(payload, "spec", "template"), "Cloud Run revision template")
    template_metadata = _mapping(template.get("metadata"), "Cloud Run revision metadata")
    if template_metadata.get("name") != ready_revision:
        raise GCPControlPlaneError(f"GCP {workload} serving revision template differs.")
    raw_template_annotations = template_metadata.get("annotations")
    template_annotations = (
        _mapping(raw_template_annotations, "Cloud Run revision annotations")
        if raw_template_annotations is not None
        else {}
    )
    spec = _mapping(template.get("spec"), "Cloud Run revision spec")
    expected_identity = (
        source.control_service_account if workload == "control" else source.druff_service_account
    )
    containers = spec.get("containers")
    if (
        spec.get("serviceAccountName") != expected_identity
        or not isinstance(containers, list)
        or len(containers) != 1
    ):
        raise GCPControlPlaneError(f"GCP {workload} identity or container count differs.")
    container = _mapping(containers[0], "Cloud Run container")
    if container.get("image") != image:
        raise GCPControlPlaneError(f"GCP {workload} image differs.")
    if workload == "control":
        expected_args = list(project_gcp_control_service(source).command)
        if container.get("args") != expected_args or container.get("command"):
            raise GCPControlPlaneError("GCP Control command differs.")
    else:
        if container.get("command") != ["/usr/bin/caddy"] or container.get("args") != [
            "run",
            "--config",
            "/etc/dander/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
        ]:
            raise GCPControlPlaneError("GCP Druff command differs.")
    expected_volumes = (
        {
            "control-oidc": (
                "control-oidc",
                f"{source.control_service_name}-control-oidc",
                "control-oidc.json",
                "/etc/dander/oidc",
            ),
            "graph-store": (
                "graph-store",
                f"{source.control_service_name}-graph-store",
                "control-graph-store.json",
                "/etc/dander/graph-store",
            ),
        }
        if workload == "control"
        else {
            "druff-bootstrap": (
                "druff-bootstrap",
                f"{source.control_service_name}-druff-bootstrap",
                "bootstrap.json",
                "/etc/dander/bootstrap",
            ),
            "druff-caddy": (
                "druff-caddy",
                f"{source.control_service_name}-druff-caddy",
                "Caddyfile",
                "/etc/dander/caddy",
            ),
        }
    )
    volumes = spec.get("volumes")
    if not isinstance(volumes, list) or len(volumes) != len(expected_volumes):
        raise GCPControlPlaneError(f"GCP {workload} startup volumes differ.")
    secret_aliases = _parse_secret_aliases(template_annotations.get("run.googleapis.com/secrets"))
    observed_volumes: set[str] = set()
    observed_aliases: set[str] = set()
    observed_versions: dict[str, str] = {}
    for volume in volumes:
        volume_data = _mapping(volume, "Cloud Run volume")
        name = str(volume_data.get("name", ""))
        expected = expected_volumes.get(name)
        secret = _mapping(volume_data.get("secret"), "secret volume")
        items = secret.get("items")
        item = (
            _mapping(items[0], "secret item") if isinstance(items, list) and len(items) == 1 else {}
        )
        version = str(item.get("key", ""))
        secret_name = str(secret.get("secretName", ""))
        if (
            expected is None
            or not isinstance(items, list)
            or len(items) != 1
            or item.get("path") != expected[2]
            or item.get("mode") != 292
            or re.fullmatch(r"[1-9][0-9]*", version) is None
            or not _secret_matches(
                secret_name,
                expected_secret=expected[1],
                source=source,
                aliases=secret_aliases,
            )
        ):
            raise GCPControlPlaneError("GCP startup config volume differs.")
        if secret_name in secret_aliases:
            observed_aliases.add(secret_name)
        observed_volumes.add(name)
        observed_versions[expected[0]] = version
    mounts = container.get("volumeMounts")
    observed_mounts = (
        {
            str(_mapping(mount, "volume mount").get("name", "")): str(
                _mapping(mount, "volume mount").get("mountPath", "")
            )
            for mount in mounts
        }
        if isinstance(mounts, list)
        else {}
    )
    if observed_volumes != set(expected_volumes) or observed_mounts != {
        name: values[3] for name, values in expected_volumes.items()
    }:
        raise GCPControlPlaneError(f"GCP {workload} startup config mounts differ.")
    if observed_aliases != set(secret_aliases) or len(observed_versions) != len(expected_volumes):
        raise GCPControlPlaneError(f"GCP {workload} secret aliases differ.")
    return observed_versions


def _verify_graph_bucket(payload: Mapping[str, Any], source: GCPControlPlaneInput) -> None:
    iam = _mapping(payload.get("iamConfiguration"), "GCS IAM configuration")
    uniform_access = _mapping(iam.get("uniformBucketLevelAccess"), "GCS uniform access")
    versioning = _mapping(payload.get("versioning"), "GCS versioning")
    soft_delete = _mapping(payload.get("softDeletePolicy"), "GCS soft-delete policy")
    retention = soft_delete.get("retentionDurationSeconds")
    if retention not in {0, "0", "0s"}:
        raise GCPControlPlaneError("Disposable GCS GraphStore soft delete is not disabled.")
    if (
        payload.get("name") != source.graph_bucket
        or str(payload.get("location", "")).casefold() != source.region.casefold()
        or uniform_access.get("enabled") is not True
        or iam.get("publicAccessPrevention") != "enforced"
        or versioning.get("enabled") is not True
    ):
        raise GCPControlPlaneError("GCS GraphStore policy differs.")


def _verify_bucket_policy(payload: Mapping[str, Any], source: GCPControlPlaneInput) -> None:
    bindings = payload.get("bindings")
    if not isinstance(bindings, list):
        raise GCPControlPlaneError("GCS GraphStore IAM policy is invalid.")
    runtime_bindings = {
        (str(binding.get("role")), member)
        for binding in bindings
        if isinstance(binding, Mapping)
        for member in binding.get("members", [])
        if member
        in {
            f"serviceAccount:{source.control_service_account}",
            f"serviceAccount:{source.druff_service_account}",
        }
    }
    if runtime_bindings != {
        ("roles/storage.objectUser", f"serviceAccount:{source.control_service_account}")
    }:
        raise GCPControlPlaneError("GCS GraphStore runtime IAM differs.")


def _verify_no_project_roles(payload: Mapping[str, Any], source: GCPControlPlaneInput) -> None:
    members = {
        f"serviceAccount:{source.control_service_account}",
        f"serviceAccount:{source.druff_service_account}",
    }
    bindings = payload.get("bindings")
    if not isinstance(bindings, list):
        raise GCPControlPlaneError("GCP project IAM policy is invalid.")
    if any(
        isinstance(binding, Mapping)
        and any(member in members for member in binding.get("members", []))
        for binding in bindings
    ):
        raise GCPControlPlaneError("GCP runtime identity has a project-level role.")


def _verify_secret_hashes(
    source: GCPControlPlaneInput,
    rendered: Mapping[str, str],
    mounted_versions: Mapping[str, str],
) -> None:
    expected = {
        "control-oidc": rendered["control-oidc.json"],
        "graph-store": rendered["control-graph-store.json"],
        "druff-bootstrap": rendered["bootstrap.json"],
        "druff-caddy": rendered["Caddyfile"],
    }
    if set(mounted_versions) != set(expected):
        raise GCPControlPlaneError("GCP mounted startup config inventory differs.")
    for suffix, content in expected.items():
        version = mounted_versions[suffix]
        result = _run(
            (
                "gcloud",
                "secrets",
                "versions",
                "access",
                version,
                f"--secret={source.control_service_name}-{suffix}",
                f"--project={source.project_id}",
                f"--impersonate-service-account={source.bootstrap_service_account}",
            )
        )
        if (
            hashlib.sha256(result.stdout.encode()).digest()
            != hashlib.sha256(content.encode()).digest()
        ):
            raise GCPControlPlaneError("GCP startup config payload differs.")


def _parse_secret_aliases(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, str) or not value:
        raise GCPControlPlaneError("Cloud Run secret alias annotation is invalid.")
    aliases: dict[str, str] = {}
    for entry in value.split(","):
        alias, separator, resource = entry.partition(":")
        if (
            separator != ":"
            or _SECRET_ALIAS.fullmatch(alias) is None
            or _SECRET_RESOURCE.fullmatch(resource) is None
            or alias in aliases
        ):
            raise GCPControlPlaneError("Cloud Run secret alias annotation is invalid.")
        aliases[alias] = resource
    return aliases


def _secret_matches(
    observed: str,
    *,
    expected_secret: str,
    source: GCPControlPlaneInput,
    aliases: Mapping[str, str],
) -> bool:
    if observed in aliases:
        return aliases[observed] in {
            f"projects/{source.project_id}/secrets/{expected_secret}",
            f"projects/{source.project_number}/secrets/{expected_secret}",
        }
    return observed == expected_secret


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise GCPControlPlaneError(f"{label} is invalid.")
    rendered = str(value)
    if re.fullmatch(r"[1-9][0-9]*", rendered) is None:
        raise GCPControlPlaneError(f"{label} is invalid.")
    return int(rendered)


def _gcloud_json(
    source: GCPControlPlaneInput,
    *arguments: str,
    regional: bool = True,
) -> object:
    command = ["gcloud", *arguments, f"--project={source.project_id}"]
    if regional:
        command.append(f"--region={source.region}")
    command.extend(
        [
            f"--impersonate-service-account={source.bootstrap_service_account}",
            "--format=json",
        ]
    )
    try:
        return json.loads(_run(tuple(command)).stdout)
    except ValueError as error:
        raise GCPControlPlaneError("gcloud returned invalid JSON.") from error


def _http(url: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10.0) as response:  # noqa: S310
            body = response.read(1024 * 1024 + 1)
            headers = {name.casefold(): value for name, value in response.headers.items()}
    except (OSError, urllib.error.URLError) as error:
        raise GCPControlPlaneError("GCP HTTPS verification failed.") from error
    if len(body) > 1024 * 1024:
        raise GCPControlPlaneError("GCP verification response exceeds 1 MiB.")
    return body, headers


def _http_error(url: str) -> tuple[bytes, dict[str, str], int]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=10.0)  # noqa: S310
    except urllib.error.HTTPError as error:
        body = error.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise GCPControlPlaneError("GCP error response exceeds 1 MiB.") from error
        return body, {name.casefold(): value for name, value in error.headers.items()}, error.code
    except (OSError, urllib.error.URLError) as error:
        raise GCPControlPlaneError("GCP HTTPS verification failed.") from error
    raise GCPControlPlaneError("GCP unauthenticated Control API unexpectedly succeeded.")


def _cloud_run_url(service: str, project_number: str, region: str) -> str:
    segment = f"{service}-{project_number}"
    if len(segment) > 63:
        raise ValueError("GCP deterministic Cloud Run URL exceeds the DNS segment bound.")
    return f"https://{segment}.{region}.run.app"


def _validate_rollback_pair(active: str, rollback: str, label: str) -> None:
    active_repository, active_digest = active.rsplit("@", 1)
    rollback_repository, rollback_digest = rollback.rsplit("@", 1)
    if active_repository != rollback_repository:
        raise ValueError(f"{label} active and rollback images must use the same repository.")
    if active_digest == rollback_digest:
        raise ValueError(f"{label} active and rollback images must use distinct digests.")


def _digest(image: str) -> str:
    return image.rsplit("@", 1)[1]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        os.chmod(temporary, 0o444)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_projection_files(destination: Path, expected: Mapping[str, str]) -> None:
    if destination.stat().st_mode & 0o777 != 0o700:
        raise GCPControlPlaneError("GCP config directory must use mode 0700.")
    for name, content in expected.items():
        path = destination / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_mode & 0o777 != 0o444
            or path.read_text(encoding="utf-8") != content
        ):
            raise GCPControlPlaneError(f"Generated GCP file is missing or stale: {name}")


def _run(
    command: Sequence[str], *, environment: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            command,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        executable = command[0] if command else "command"
        raise GCPControlPlaneError(f"GCP command failed: {executable}") from error


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GCPControlPlaneError(f"GCP returned invalid {label}.")
    return value


def _nested(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            raise GCPControlPlaneError(f"GCP resource is missing required field: {'.'.join(keys)}")
        current = current[key]
    return current


def _load_input(path: Path) -> GCPControlPlaneInput:
    try:
        return GCPControlPlaneInput.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GCPControlPlaneError("GCP control-plane input is invalid.") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "preflight", "verify"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--terraform-root", type=Path, default=Path("infra/gcp-control"))
    parser.add_argument("--environment", choices=("active", "rollback"), default="active")
    arguments = parser.parse_args()
    source = _load_input(arguments.input)
    if arguments.action == "render":
        written = write_gcp_control_plane(source, output_directory=arguments.output)
        print(json.dumps({"status": "rendered", "files": [path.name for path in written]}))
    elif arguments.action == "preflight":
        result = preflight_gcp_control_plane(
            source,
            output_directory=arguments.output,
            terraform_root=arguments.terraform_root,
        )
        print(json.dumps(result, sort_keys=True))
    else:
        result = verify_live_gcp_control_plane(
            source,
            output_directory=arguments.output,
            environment=arguments.environment,
        )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "GCP_CONTROL_PLANE_SCHEMA",
    "GCPControlPlaneError",
    "GCPControlPlaneInput",
    "preflight_gcp_control_plane",
    "project_gcp_control_service",
    "render_gcp_control_plane",
    "verify_live_gcp_control_plane",
    "write_gcp_control_plane",
]
