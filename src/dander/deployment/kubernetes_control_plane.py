"""Deterministic existing-cluster Kubernetes projection for hosted Dander Control."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import subprocess
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Final, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dander.control.auth import HostedOIDCDeploymentInput, project_hosted_oidc
from dander.deployment.service import (
    ControlServiceIngress,
    ControlServiceObservability,
    ControlServiceProbes,
    ControlServiceResources,
    ControlServiceScaling,
    IngressVisibility,
    LocalGraphStoreBinding,
    ResolvedControlServiceRequest,
)

KUBERNETES_CONTROL_PLANE_SCHEMA: Final = "io.dander.kubernetes-control-plane/v1"
KUBERNETES_CONFIG_ROOT: Final = "/etc/dander"
KUBERNETES_GRAPH_ROOT: Final = "/var/lib/dander/control"
KUBERNETES_CONTROL_PORT: Final = 8770
KUBERNETES_DRUFF_PORT: Final = 8080
KUBERNETES_INGRESS_CLASS: Final = "nginx"

_IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9.-]+(?::[0-9]{1,5})?/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DNS_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_ANNOTATION_NAME = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?/)?"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?$"
)
_FILES: Final = frozenset(
    {
        "active-values.yaml",
        "bootstrap.json",
        "control-graph-store.json",
        "control-oidc.json",
        "deployment.json",
        "public-client.json",
        "rollback-values.yaml",
    }
)


class KubernetesControlPlaneError(ValueError):
    """The Kubernetes profile, projection, or live release is invalid."""


class KubernetesControlPlaneInput(BaseModel):
    """One closed immutable non-secret input for an existing Kubernetes cluster."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dander_image: str = Field(min_length=1, max_length=2048)
    dander_rollback_image: str = Field(min_length=1, max_length=2048)
    druff_image: str = Field(min_length=1, max_length=2048)
    druff_rollback_image: str = Field(min_length=1, max_length=2048)
    context: str = Field(min_length=1, max_length=253)
    namespace: str = Field(min_length=1, max_length=63)
    release_name: str = Field(min_length=1, max_length=51)
    service_account_name: str = Field(min_length=1, max_length=63)
    service_account_annotations: tuple[tuple[str, str], ...] = ()
    existing_tls_secret_name: str = Field(min_length=1, max_length=253)
    oidc: HostedOIDCDeploymentInput

    @field_validator(
        "dander_image",
        "dander_rollback_image",
        "druff_image",
        "druff_rollback_image",
    )
    @classmethod
    def validate_image(cls, value: str) -> str:
        if _IMMUTABLE_IMAGE.fullmatch(value) is None:
            raise ValueError(
                "Kubernetes control-plane images must use immutable sha256 references."
            )
        return value

    @field_validator("namespace", "release_name", "service_account_name")
    @classmethod
    def validate_dns_label(cls, value: str) -> str:
        if _DNS_LABEL.fullmatch(value) is None:
            raise ValueError("Kubernetes names must be valid DNS labels.")
        return value

    @field_validator("existing_tls_secret_name")
    @classmethod
    def validate_dns_subdomain(cls, value: str) -> str:
        if _DNS_SUBDOMAIN.fullmatch(value) is None:
            raise ValueError("The Kubernetes TLS Secret name must be a valid DNS subdomain.")
        return value

    @field_validator("context")
    @classmethod
    def validate_context(cls, value: str) -> str:
        if any(character.isspace() for character in value) or any(
            character in value for character in ("\x00", "\n", "\r")
        ):
            raise ValueError("Kubernetes context must not contain whitespace.")
        return value

    @field_validator("service_account_annotations")
    @classmethod
    def validate_annotations(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        observed: dict[str, str] = {}
        for name, annotation_value in value:
            if _ANNOTATION_NAME.fullmatch(name) is None:
                raise ValueError("Kubernetes annotation names are invalid.")
            if not isinstance(annotation_value, str) or len(annotation_value) > 1024:
                raise ValueError("Kubernetes annotation values are invalid.")
            if name in observed:
                raise ValueError("Kubernetes annotation names must be unique.")
            observed[name] = annotation_value
        return tuple(sorted(observed.items()))

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        _validate_rollback_pair(self.dander_image, self.dander_rollback_image, "Dander")
        _validate_rollback_pair(self.druff_image, self.druff_rollback_image, "Druff")
        origin = _browser_origin(self.oidc.api_url)
        expected = {
            "api_url": origin,
            "redirect_uri": f"{origin}/auth/callback",
            "logout_uri": f"{origin}/signed-out",
            "allowed_origins": (origin,),
        }
        observed = {
            "api_url": self.oidc.api_url,
            "redirect_uri": self.oidc.redirect_uri,
            "logout_uri": self.oidc.logout_uri,
            "allowed_origins": self.oidc.allowed_origins,
        }
        if observed != expected:
            raise ValueError("Kubernetes hosted OIDC routes must use the exact Ingress origin.")
        return self

    @property
    def ingress_host(self) -> str:
        """Return the exact hostname shared by Ingress and the browser origin."""
        hostname = urlsplit(self.oidc.api_url).hostname
        if hostname is None:  # pragma: no cover - guarded by model validation
            raise KubernetesControlPlaneError("Kubernetes browser origin has no hostname.")
        return hostname


def project_kubernetes_control_service(
    source: KubernetesControlPlaneInput,
) -> ResolvedControlServiceRequest:
    """Project the D6 service contract for the single-writer Kubernetes profile."""
    return ResolvedControlServiceRequest(
        service_id="dander_control",
        profile_id="kubernetes_helm",
        image=source.dander_image,
        port=KUBERNETES_CONTROL_PORT,
        probes=ControlServiceProbes(),
        resources=ControlServiceResources(cpu_millis=500, memory_mib=512),
        scaling=ControlServiceScaling(
            minimum_instances=1,
            maximum_instances=1,
            shutdown_grace_seconds=30,
        ),
        environment=(),
        secret_bindings=(),
        workload_identity=(
            f"kubernetes://{source.namespace}/serviceaccounts/{source.service_account_name}"
        ),
        ingress=ControlServiceIngress(visibility=IngressVisibility.PUBLIC),
        oidc=source.oidc,
        oidc_config_path=f"{KUBERNETES_CONFIG_ROOT}/control-oidc.json",
        graph_store_config_path=f"{KUBERNETES_CONFIG_ROOT}/control-graph-store.json",
        graph_store=LocalGraphStoreBinding(root=KUBERNETES_GRAPH_ROOT),
        observability=ControlServiceObservability(
            log_destination="kubernetes-stdout",
            alert_target=None,
            retention_days=1,
        ),
        rollback_digest=_digest(source.dander_rollback_image),
    )


def render_kubernetes_control_plane(source: KubernetesControlPlaneInput) -> dict[str, str]:
    """Return the exact Kubernetes files without invoking Helm or a cluster."""
    oidc = project_hosted_oidc(source.oidc)
    service = project_kubernetes_control_service(source)
    oidc_json = _json(source.oidc.model_dump(mode="json"))
    graph_store_json = _json(service.graph_store.as_dict())
    bootstrap_json = _json(oidc.bootstrap.model_dump(mode="json"))
    public_client_json = _json(oidc.public_client.model_dump(mode="json"))
    identity = {
        "name": source.service_account_name,
        "annotations": dict(source.service_account_annotations),
    }
    control_rollout_digest = _sha256(oidc_json + graph_store_json + _json(identity))
    druff_rollout_digest = _sha256(bootstrap_json)
    common = {
        "schema": KUBERNETES_CONTROL_PLANE_SCHEMA,
        "profile": "kubernetes_helm",
        "serviceAccount": {
            "name": source.service_account_name,
            "annotations": dict(source.service_account_annotations),
            "identityDigest": _sha256(_json(identity)),
        },
        "control": {
            "port": KUBERNETES_CONTROL_PORT,
            "command": list(service.command),
            "oidcConfig": oidc_json.rstrip("\n"),
            "graphStoreConfig": graph_store_json.rstrip("\n"),
            "rolloutDigest": control_rollout_digest,
            "resources": _resources(service),
            "probes": {
                "livenessPath": service.probes.liveness_path,
                "readinessPath": service.probes.readiness_path,
            },
            "terminationGracePeriodSeconds": service.scaling.shutdown_grace_seconds,
        },
        "druff": {
            "port": KUBERNETES_DRUFF_PORT,
            "bootstrap": bootstrap_json.rstrip("\n"),
            "rolloutDigest": druff_rollout_digest,
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi", "ephemeral-storage": "64Mi"},
                "limits": {"cpu": "500m", "memory": "256Mi", "ephemeral-storage": "128Mi"},
            },
        },
        "graphStore": {"accessMode": "ReadWriteOnce", "size": "1Gi"},
        "ingress": {
            "className": KUBERNETES_INGRESS_CLASS,
            "host": source.ingress_host,
            "tlsSecretName": source.existing_tls_secret_name,
            "accessLogEnabled": False,
        },
    }
    active = _values(
        common,
        dander_image=source.dander_image,
        druff_image=source.druff_image,
    )
    rollback = _values(
        common,
        dander_image=source.dander_rollback_image,
        druff_image=source.druff_rollback_image,
    )
    manifest = {
        "schema": KUBERNETES_CONTROL_PLANE_SCHEMA,
        "context": source.context,
        "namespace": source.namespace,
        "release_name": source.release_name,
        "ingress_origin": source.oidc.api_url,
        "ingress_class": KUBERNETES_INGRESS_CLASS,
        "dander_image": source.dander_image,
        "dander_rollback_image": source.dander_rollback_image,
        "druff_image": source.druff_image,
        "druff_rollback_image": source.druff_rollback_image,
        "service": service.as_dict(),
        "control_rollout_digest": control_rollout_digest,
        "druff_rollout_digest": druff_rollout_digest,
    }
    return {
        "active-values.yaml": yaml.safe_dump(active, sort_keys=False, width=100),
        "rollback-values.yaml": yaml.safe_dump(rollback, sort_keys=False, width=100),
        "control-oidc.json": oidc_json,
        "control-graph-store.json": graph_store_json,
        "bootstrap.json": bootstrap_json,
        "public-client.json": public_client_json,
        "deployment.json": _json(manifest),
    }


def write_kubernetes_control_plane(
    source: KubernetesControlPlaneInput,
    *,
    output_directory: Path,
) -> tuple[Path, ...]:
    """Atomically write only the closed non-secret Kubernetes projection."""
    destination = output_directory.expanduser().resolve(strict=False)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    rendered = render_kubernetes_control_plane(source)
    if frozenset(rendered) != _FILES:  # pragma: no cover - closed projection invariant
        raise KubernetesControlPlaneError("Kubernetes projection produced an unexpected file set.")
    written: list[Path] = []
    for name, content in sorted(rendered.items()):
        path = destination / name
        _atomic_write(path, content)
        written.append(path)
    return tuple(written)


def preflight_kubernetes_control_plane(
    source: KubernetesControlPlaneInput,
    *,
    output_directory: Path,
    chart: Path,
) -> dict[str, object]:
    """Verify exact files and save deterministic Helm renders without cluster mutation."""
    destination = output_directory.expanduser().resolve(strict=True)
    chart_path = chart.expanduser().resolve(strict=True)
    expected = render_kubernetes_control_plane(source)
    _verify_projection_files(destination, expected)
    if not (chart_path / "Chart.yaml").is_file():
        raise KubernetesControlPlaneError("Packaged Kubernetes Control chart is missing.")
    render_digests: dict[str, str] = {}
    for environment in ("active", "rollback"):
        values = destination / f"{environment}-values.yaml"
        _run(("helm", "lint", str(chart_path), "--values", str(values)))
        command = (
            "helm",
            "template",
            source.release_name,
            str(chart_path),
            "--namespace",
            source.namespace,
            "--values",
            str(values),
        )
        first = _run(command).stdout
        second = _run(command).stdout
        if first != second:
            raise KubernetesControlPlaneError("Repeated Helm rendering is not byte-equal.")
        _atomic_write(destination / f"{environment}-manifests.yaml", first)
        render_digests[environment] = _sha256(first)
    return {
        "schema": KUBERNETES_CONTROL_PLANE_SCHEMA,
        "status": "passed",
        "checks": [
            "projection-current",
            "images-immutable",
            "ingress-nginx-access-log-disabled",
            "helm-lint",
            "repeated-render-equal",
        ],
        "render_sha256": render_digests,
    }


def verify_live_kubernetes_control_plane(
    source: KubernetesControlPlaneInput,
    *,
    output_directory: Path,
    certificate: Path,
    environment: Literal["active", "rollback"] = "active",
) -> dict[str, object]:
    """Read and verify one live exact-digest Kubernetes release without returning secrets."""
    rendered = render_kubernetes_control_plane(source)
    destination = output_directory.expanduser().resolve(strict=True)
    _verify_projection_files(destination, rendered)
    values = yaml.safe_load(rendered[f"{environment}-values.yaml"])
    if not isinstance(values, Mapping):  # pragma: no cover - internal invariant
        raise KubernetesControlPlaneError("Rendered Kubernetes values are invalid.")
    prefix = (
        "kubectl",
        "--context",
        source.context,
        "--namespace",
        source.namespace,
        "get",
    )
    names = {
        "control": f"{source.release_name}-control",
        "druff": f"{source.release_name}-druff",
        "config": f"{source.release_name}-config",
        "graph_store": f"{source.release_name}-graph-store",
        "ingress": source.release_name,
    }
    service_account = _kubectl_json((*prefix, "serviceaccount", source.service_account_name))
    _verify_service_account(service_account, source)
    config = _kubectl_json((*prefix, "configmap", names["config"]))
    _verify_config(config, rendered)
    expected_images = _expected_images(source, environment)
    for workload in ("control", "druff"):
        deployment = _kubectl_json((*prefix, "deployment", names[workload]))
        _verify_deployment(
            deployment,
            workload=workload,
            image=expected_images[workload],
            values=values,
            source=source,
        )
        service = _kubectl_json((*prefix, "service", names[workload]))
        _verify_service(service, workload=workload, values=values)
    pvc = _kubectl_json((*prefix, "persistentvolumeclaim", names["graph_store"]))
    if _nested(pvc, "status", "phase") != "Bound":
        raise KubernetesControlPlaneError("Kubernetes GraphStore PVC is not bound.")
    ingress = _kubectl_json((*prefix, "ingress", names["ingress"]))
    _verify_ingress(ingress, source, names)
    context = ssl.create_default_context(cafile=str(certificate.expanduser().resolve(strict=True)))
    health_body, _health_headers = _https_get(f"{source.oidc.api_url}/healthz", context)
    ready_body, _ready_headers = _https_get(f"{source.oidc.api_url}/readyz", context)
    bootstrap_body, bootstrap_headers = _https_get(f"{source.oidc.api_url}/bootstrap.json", context)
    if json.loads(health_body) != {"status": "ok"}:
        raise KubernetesControlPlaneError("Kubernetes Control liveness response is invalid.")
    if json.loads(ready_body) != {"status": "ready"}:
        raise KubernetesControlPlaneError("Kubernetes Control readiness response is invalid.")
    if bootstrap_body != rendered["bootstrap.json"].rstrip("\n").encode():
        raise KubernetesControlPlaneError("Served bootstrap differs from the projection.")
    if bootstrap_headers.get("cache-control") != "no-store" or any(
        not bootstrap_headers.get(name)
        for name in ("content-security-policy", "strict-transport-security")
    ):
        raise KubernetesControlPlaneError("Kubernetes security or cache headers are incomplete.")
    return {
        "schema": KUBERNETES_CONTROL_PLANE_SCHEMA,
        "status": "passed",
        "environment": environment,
        "checks": [
            "service-account-exact",
            "configmaps-exact",
            "deployments-exact-ready",
            "services-exact",
            "graph-store-pvc-bound",
            "ingress-tls-routes-exact-no-access-log",
            "control-ready",
            "bootstrap-exact",
            "headers-current",
        ],
    }


def _values(
    common: Mapping[str, object], *, dander_image: str, druff_image: str
) -> dict[str, object]:
    values: dict[str, object] = json.loads(json.dumps(common))
    control = values.get("control")
    druff = values.get("druff")
    if not isinstance(control, dict) or not isinstance(druff, dict):  # pragma: no cover
        raise KubernetesControlPlaneError("Kubernetes workload values are invalid.")
    control["image"] = dander_image
    druff["image"] = druff_image
    return values


def _resources(service: ResolvedControlServiceRequest) -> dict[str, object]:
    resource_values = {
        "cpu": f"{service.resources.cpu_millis}m",
        "memory": f"{service.resources.memory_mib}Mi",
        "ephemeral-storage": "128Mi",
    }
    return {"requests": resource_values, "limits": dict(resource_values)}


def _browser_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Kubernetes API URL must be a credential-free HTTPS origin.")
    return value.removesuffix("/")


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


def _permission_bits(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)  # noqa: S603
    except (OSError, subprocess.CalledProcessError) as error:
        executable = command[0] if command else "command"
        raise KubernetesControlPlaneError(f"Kubernetes command failed: {executable}") from error


def _kubectl_json(command: Sequence[str]) -> Mapping[str, Any]:
    try:
        payload = json.loads(_run((*command, "-o", "json")).stdout)
    except ValueError as error:
        raise KubernetesControlPlaneError("kubectl returned invalid JSON.") from error
    if not isinstance(payload, Mapping):
        raise KubernetesControlPlaneError("kubectl returned an invalid resource.")
    return payload


def _verify_service_account(
    payload: Mapping[str, Any], source: KubernetesControlPlaneInput
) -> None:
    metadata = _mapping(payload.get("metadata"), "ServiceAccount metadata")
    annotations = metadata.get("annotations") or {}
    if not isinstance(annotations, Mapping):
        raise KubernetesControlPlaneError("Kubernetes ServiceAccount annotations differ.")
    identity_annotations = {
        name: value
        for name, value in annotations.items()
        if isinstance(name, str) and not name.startswith("meta.helm.sh/")
    }
    if identity_annotations != dict(source.service_account_annotations):
        raise KubernetesControlPlaneError("Kubernetes ServiceAccount annotations differ.")
    if payload.get("automountServiceAccountToken") is not False:
        raise KubernetesControlPlaneError("Kubernetes ServiceAccount unexpectedly mounts tokens.")


def _verify_config(payload: Mapping[str, Any], rendered: Mapping[str, str]) -> None:
    data = _mapping(payload.get("data"), "ConfigMap data")
    expected = {
        "bootstrap.json": rendered["bootstrap.json"].rstrip("\n"),
        "control-graph-store.json": rendered["control-graph-store.json"].rstrip("\n"),
        "control-oidc.json": rendered["control-oidc.json"].rstrip("\n"),
    }
    if dict(data) != expected:
        raise KubernetesControlPlaneError("Kubernetes generated ConfigMap differs.")


def _verify_deployment(
    payload: Mapping[str, Any],
    *,
    workload: str,
    image: str,
    values: Mapping[str, Any],
    source: KubernetesControlPlaneInput,
) -> None:
    if _nested(payload, "spec", "replicas") != 1:
        raise KubernetesControlPlaneError(f"Kubernetes {workload} replica count differs.")
    if _nested(payload, "status", "readyReplicas") != 1:
        raise KubernetesControlPlaneError(f"Kubernetes {workload} is not ready.")
    template = _mapping(_nested(payload, "spec", "template"), f"{workload} pod template")
    metadata = _mapping(template.get("metadata"), f"{workload} pod metadata")
    annotations = _mapping(metadata.get("annotations"), f"{workload} pod annotations")
    expected_rollout = _nested(values, workload, "rolloutDigest")
    if annotations.get("dander.io/config-sha256") != expected_rollout:
        raise KubernetesControlPlaneError(f"Kubernetes {workload} rollout digest differs.")
    spec = _mapping(template.get("spec"), f"{workload} pod spec")
    if spec.get("automountServiceAccountToken") is not False:
        raise KubernetesControlPlaneError(f"Kubernetes {workload} mounts an API token.")
    if workload == "control" and spec.get("serviceAccountName") != source.service_account_name:
        raise KubernetesControlPlaneError("Kubernetes Control identity differs.")
    containers = spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise KubernetesControlPlaneError(f"Kubernetes {workload} container count differs.")
    container = _mapping(containers[0], f"{workload} container")
    if container.get("image") != image:
        raise KubernetesControlPlaneError(f"Kubernetes {workload} image differs.")
    _verify_container_security(container, workload)
    if workload == "control":
        if container.get("args") != _nested(values, "control", "command"):
            raise KubernetesControlPlaneError("Kubernetes Control command differs.")
        if _nested(payload, "spec", "strategy", "type") != "Recreate":
            raise KubernetesControlPlaneError("Kubernetes Control must use Recreate strategy.")
        init_containers = spec.get("initContainers")
        if not isinstance(init_containers, list) or len(init_containers) != 1:
            raise KubernetesControlPlaneError("Kubernetes volume initializer is missing.")
        initializer = _mapping(init_containers[0], "volume initializer")
        security = _mapping(initializer.get("securityContext"), "initializer security")
        capabilities = _mapping(security.get("capabilities"), "initializer capabilities")
        if (
            security.get("runAsUser") != 0
            or security.get("runAsNonRoot") is not False
            or capabilities.get("drop") != ["ALL"]
            or set(capabilities.get("add") or ()) != {"CHOWN", "FOWNER"}
        ):
            raise KubernetesControlPlaneError("Kubernetes volume initializer privilege differs.")


def _verify_container_security(container: Mapping[str, Any], workload: str) -> None:
    security = _mapping(container.get("securityContext"), f"{workload} security context")
    capabilities = _mapping(security.get("capabilities"), f"{workload} capabilities")
    if (
        security.get("runAsUser") != 65532
        or security.get("runAsGroup") != 65532
        or security.get("runAsNonRoot") is not True
        or security.get("allowPrivilegeEscalation") is not False
        or security.get("readOnlyRootFilesystem") is not True
        or capabilities.get("drop") != ["ALL"]
        or capabilities.get("add")
    ):
        raise KubernetesControlPlaneError(f"Kubernetes {workload} container privilege differs.")


def _verify_service(
    payload: Mapping[str, Any], *, workload: str, values: Mapping[str, Any]
) -> None:
    spec = _mapping(payload.get("spec"), f"{workload} Service spec")
    ports = spec.get("ports")
    if (
        spec.get("type") != "ClusterIP"
        or not isinstance(ports, list)
        or len(ports) != 1
        or _mapping(ports[0], f"{workload} Service port").get("port")
        != _nested(values, workload, "port")
    ):
        raise KubernetesControlPlaneError(f"Kubernetes {workload} Service differs.")


def _verify_ingress(
    payload: Mapping[str, Any], source: KubernetesControlPlaneInput, names: Mapping[str, str]
) -> None:
    metadata = _mapping(payload.get("metadata"), "Ingress metadata")
    annotations = _mapping(metadata.get("annotations"), "Ingress annotations")
    if annotations.get("nginx.ingress.kubernetes.io/enable-access-log") != "false":
        raise KubernetesControlPlaneError("Kubernetes Ingress access logging is not disabled.")
    if annotations.get("nginx.ingress.kubernetes.io/proxy-body-size") != "6m":
        raise KubernetesControlPlaneError("Kubernetes Ingress graph request limit differs.")
    if annotations.get("nginx.ingress.kubernetes.io/ssl-redirect") != "true":
        raise KubernetesControlPlaneError("Kubernetes Ingress HTTPS redirect is not enabled.")
    spec = _mapping(payload.get("spec"), "Ingress spec")
    if spec.get("ingressClassName") != KUBERNETES_INGRESS_CLASS:
        raise KubernetesControlPlaneError("Kubernetes Ingress class differs.")
    tls = spec.get("tls")
    rules = spec.get("rules")
    if not isinstance(tls, list) or len(tls) != 1 or not isinstance(rules, list) or len(rules) != 1:
        raise KubernetesControlPlaneError("Kubernetes Ingress topology differs.")
    tls_rule = _mapping(tls[0], "Ingress TLS")
    if tls_rule.get("secretName") != source.existing_tls_secret_name or tls_rule.get("hosts") != [
        source.ingress_host
    ]:
        raise KubernetesControlPlaneError("Kubernetes Ingress TLS Secret differs.")
    rule = _mapping(rules[0], "Ingress rule")
    if rule.get("host") != source.ingress_host:
        raise KubernetesControlPlaneError("Kubernetes Ingress host differs.")
    paths = _nested(rule, "http", "paths")
    if not isinstance(paths, list):
        raise KubernetesControlPlaneError("Kubernetes Ingress paths are invalid.")
    observed = {
        (
            _mapping(path, "Ingress path").get("path"),
            _nested(path, "backend", "service", "name"),
        )
        for path in paths
    }
    expected = {
        ("/v1", names["control"]),
        ("/healthz", names["control"]),
        ("/readyz", names["control"]),
        ("/", names["druff"]),
    }
    if observed != expected:
        raise KubernetesControlPlaneError("Kubernetes Ingress routes differ.")


def _verify_projection_files(destination: Path, expected: Mapping[str, str]) -> None:
    if _permission_bits(destination) != 0o700:
        raise KubernetesControlPlaneError("Kubernetes config directory must use mode 0700.")
    for name, content in expected.items():
        path = destination / name
        if (
            path.is_symlink()
            or not path.is_file()
            or _permission_bits(path) != 0o444
            or path.read_text(encoding="utf-8") != content
        ):
            raise KubernetesControlPlaneError(
                f"Generated Kubernetes file is missing or stale: {name}"
            )


def _expected_images(
    source: KubernetesControlPlaneInput, environment: Literal["active", "rollback"]
) -> dict[str, str]:
    if environment == "active":
        return {"control": source.dander_image, "druff": source.druff_image}
    return {"control": source.dander_rollback_image, "druff": source.druff_rollback_image}


def _https_get(url: str, context: ssl.SSLContext) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5.0, context=context) as response:  # noqa: S310
        body = response.read(1024 * 1024 + 1)
        headers = {name.casefold(): value for name, value in response.headers.items()}
    if len(body) > 1024 * 1024:
        raise KubernetesControlPlaneError("Kubernetes verification response exceeds 1 MiB.")
    return body, headers


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise KubernetesControlPlaneError(f"Kubernetes returned invalid {label}.")
    return value


def _nested(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            raise KubernetesControlPlaneError(
                f"Kubernetes resource is missing required field: {'.'.join(keys)}"
            )
        current = current[key]
    return current


def _load_input(path: Path) -> KubernetesControlPlaneInput:
    try:
        return KubernetesControlPlaneInput.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise KubernetesControlPlaneError("Kubernetes control-plane input is invalid.") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "preflight", "verify"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--chart",
        type=Path,
        default=Path("infra/kubernetes/chart/dander-control"),
    )
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--environment", choices=("active", "rollback"), default="active")
    arguments = parser.parse_args()
    source = _load_input(arguments.input)
    if arguments.action == "render":
        written = write_kubernetes_control_plane(source, output_directory=arguments.output)
        print(json.dumps({"status": "rendered", "files": [path.name for path in written]}))
    elif arguments.action == "preflight":
        result = preflight_kubernetes_control_plane(
            source,
            output_directory=arguments.output,
            chart=arguments.chart,
        )
        print(json.dumps(result, sort_keys=True))
    else:
        if arguments.certificate is None:
            parser.error("--certificate is required for live verification")
        result = verify_live_kubernetes_control_plane(
            source,
            output_directory=arguments.output,
            certificate=arguments.certificate,
            environment=arguments.environment,
        )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "KUBERNETES_CONTROL_PLANE_SCHEMA",
    "KubernetesControlPlaneError",
    "KubernetesControlPlaneInput",
    "preflight_kubernetes_control_plane",
    "project_kubernetes_control_service",
    "render_kubernetes_control_plane",
    "verify_live_kubernetes_control_plane",
    "write_kubernetes_control_plane",
]
