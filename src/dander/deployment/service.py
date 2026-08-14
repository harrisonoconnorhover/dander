"""Immutable provider-neutral deployment contracts for the hosted Control service."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from dander.control.auth import HostedOIDCDeploymentInput
from dander.deployment.projection import SecretReference

CONTROL_SERVICE_PROJECTION_SCHEMA = "io.dander.control-service/v1"
MAX_GRAPH_STORE_CONFIG_BYTES = 16 * 1024

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9.-]+(?::[0-9]{1,5})?/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")
_GCS_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_S3_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_AZURE_CONTAINER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
_OCI_BINDING = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_AWS_ACCOUNT = re.compile(r"^[0-9]{12}$")
_HEADER_NAME = re.compile(r"^[a-z][a-z0-9-]{0,127}$")


class ControlServiceProjectionError(ValueError):
    """A Control service projection is unsafe, incomplete, or inconsistent."""


class IngressVisibility(StrEnum):
    """Portable exposure intent; providers retain their native ingress implementation."""

    INTERNAL = "internal"
    PUBLIC = "public"


@dataclass(frozen=True, slots=True)
class LocalGraphStoreBinding:
    """One root-confined local GraphStore locator for explicit local deployments."""

    root: str
    kind: Literal["local"] = field(default="local", init=False)

    def __post_init__(self) -> None:
        _validate_absolute_path(self.root, label="local GraphStore root")

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "root": self.root}


@dataclass(frozen=True, slots=True)
class GCSGraphStoreBinding:
    """Credential-free GCS runtime locator."""

    bucket: str
    prefix: str = "dander-control/v1"
    kind: Literal["gcs"] = field(default="gcs", init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.bucket, str)
            or _GCS_BUCKET.fullmatch(self.bucket) is None
            or ".." in self.bucket
        ):
            raise ControlServiceProjectionError("invalid GCS GraphStore bucket")
        _validate_prefix(self.prefix)

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "bucket": self.bucket, "prefix": self.prefix}


@dataclass(frozen=True, slots=True)
class S3GraphStoreBinding:
    """Credential-free S3 runtime locator."""

    bucket: str
    prefix: str = "dander-control/v1"
    expected_bucket_owner: str | None = None
    kind: Literal["s3"] = field(default="s3", init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.bucket, str)
            or _S3_BUCKET.fullmatch(self.bucket) is None
            or ".." in self.bucket
            or ".-" in self.bucket
            or "-." in self.bucket
            or self.bucket.endswith("--x-s3")
        ):
            raise ControlServiceProjectionError("invalid S3 GraphStore bucket")
        _validate_prefix(self.prefix)
        if (
            self.expected_bucket_owner is not None
            and _AWS_ACCOUNT.fullmatch(self.expected_bucket_owner) is None
        ):
            raise ControlServiceProjectionError("invalid S3 GraphStore owner")

    def as_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "expected_bucket_owner": self.expected_bucket_owner,
        }


@dataclass(frozen=True, slots=True)
class AzureBlobGraphStoreBinding:
    """Credential-free Azure Blob runtime locator."""

    account_url: str
    container: str
    prefix: str = "dander-control/v1"
    kind: Literal["azure_blob"] = field(default="azure_blob", init=False)

    def __post_init__(self) -> None:
        parsed = urlsplit(self.account_url) if isinstance(self.account_url, str) else None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ControlServiceProjectionError("invalid Azure Blob GraphStore account URL")
        if (
            not isinstance(self.container, str)
            or _AZURE_CONTAINER.fullmatch(self.container) is None
            or "--" in self.container
        ):
            raise ControlServiceProjectionError("invalid Azure Blob GraphStore container")
        _validate_prefix(self.prefix)

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "account_url": self.account_url,
            "container": self.container,
            "prefix": self.prefix,
        }


@dataclass(frozen=True, slots=True)
class OCIObjectGraphStoreBinding:
    """Credential-free OCI Object Storage runtime locator."""

    namespace: str
    bucket: str
    prefix: str = "dander-control/v1"
    kind: Literal["oci_object"] = field(default="oci_object", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or _OCI_BINDING.fullmatch(self.namespace) is None:
            raise ControlServiceProjectionError("invalid OCI GraphStore namespace")
        if not isinstance(self.bucket, str) or _OCI_BINDING.fullmatch(self.bucket) is None:
            raise ControlServiceProjectionError("invalid OCI GraphStore bucket")
        _validate_prefix(self.prefix)

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "namespace": self.namespace,
            "bucket": self.bucket,
            "prefix": self.prefix,
        }


type GraphStoreBinding = (
    LocalGraphStoreBinding
    | GCSGraphStoreBinding
    | S3GraphStoreBinding
    | AzureBlobGraphStoreBinding
    | OCIObjectGraphStoreBinding
)


@dataclass(frozen=True, slots=True)
class ControlServiceProbes:
    """Portable liveness and readiness paths."""

    liveness_path: str = "/healthz"
    readiness_path: str = "/readyz"

    def __post_init__(self) -> None:
        _validate_route_path(self.liveness_path, label="liveness path")
        _validate_route_path(self.readiness_path, label="readiness path")
        if self.liveness_path == self.readiness_path:
            raise ControlServiceProjectionError("liveness and readiness paths must differ")


@dataclass(frozen=True, slots=True)
class ControlServiceResources:
    """Portable CPU and memory request for one service instance."""

    cpu_millis: int
    memory_mib: int

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (self.cpu_millis, self.memory_mib)
        ):
            raise ControlServiceProjectionError("service CPU and memory must be positive integers")


@dataclass(frozen=True, slots=True)
class ControlServiceScaling:
    """Portable instance bounds and graceful shutdown deadline."""

    minimum_instances: int
    maximum_instances: int
    shutdown_grace_seconds: int

    def __post_init__(self) -> None:
        values = (self.minimum_instances, self.maximum_instances, self.shutdown_grace_seconds)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ControlServiceProjectionError("service scaling values must be integers")
        if (
            self.minimum_instances < 0
            or self.maximum_instances < 1
            or self.minimum_instances > self.maximum_instances
            or self.shutdown_grace_seconds < 1
        ):
            raise ControlServiceProjectionError("service scaling or shutdown bounds are invalid")


@dataclass(frozen=True, slots=True)
class ControlServiceIngress:
    """Portable ingress visibility; exact origins come from hosted OIDC input."""

    visibility: IngressVisibility

    def __post_init__(self) -> None:
        if not isinstance(self.visibility, IngressVisibility):
            raise ControlServiceProjectionError("invalid service ingress visibility")


@dataclass(frozen=True, slots=True)
class ControlServiceObservability:
    """Portable log, alert, and retention intent."""

    log_destination: str
    alert_target: str | None
    retention_days: int

    def __post_init__(self) -> None:
        if not self.log_destination.strip() or len(self.log_destination) > 1024:
            raise ControlServiceProjectionError("service log destination must not be blank")
        if self.alert_target is not None and (
            not self.alert_target.strip() or len(self.alert_target) > 1024
        ):
            raise ControlServiceProjectionError("service alert target must not be blank")
        if (
            isinstance(self.retention_days, bool)
            or not isinstance(self.retention_days, int)
            or self.retention_days < 1
        ):
            raise ControlServiceProjectionError("service log retention must be positive")


@dataclass(frozen=True, slots=True)
class ResolvedControlServiceRequest:
    """Fully validated provider-neutral intent for one hosted Control service."""

    service_id: str
    profile_id: str
    image: str
    port: int
    probes: ControlServiceProbes
    resources: ControlServiceResources
    scaling: ControlServiceScaling
    environment: tuple[tuple[str, str], ...]
    secret_bindings: tuple[tuple[str, SecretReference], ...]
    workload_identity: str
    ingress: ControlServiceIngress
    oidc: HostedOIDCDeploymentInput
    oidc_config_path: str
    graph_store_config_path: str
    graph_store: GraphStoreBinding
    observability: ControlServiceObservability
    rollback_digest: str
    command: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        if (
            _IDENTIFIER.fullmatch(self.service_id) is None
            or _IDENTIFIER.fullmatch(self.profile_id) is None
        ):
            raise ControlServiceProjectionError("invalid service or profile identifier")
        if _IMMUTABLE_IMAGE.fullmatch(self.image) is None:
            raise ControlServiceProjectionError("service image must use an immutable sha256 digest")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise ControlServiceProjectionError("service port must be between 1 and 65535")
        if not isinstance(self.probes, ControlServiceProbes):
            raise ControlServiceProjectionError("invalid service probes")
        if not isinstance(self.resources, ControlServiceResources):
            raise ControlServiceProjectionError("invalid service resources")
        if not isinstance(self.scaling, ControlServiceScaling):
            raise ControlServiceProjectionError("invalid service scaling")
        if not isinstance(self.ingress, ControlServiceIngress):
            raise ControlServiceProjectionError("invalid service ingress")
        if not isinstance(self.oidc, HostedOIDCDeploymentInput):
            raise ControlServiceProjectionError("invalid hosted OIDC deployment input")
        if not isinstance(self.graph_store, _GRAPH_STORE_BINDING_TYPES):
            raise ControlServiceProjectionError("invalid GraphStore binding")
        if not isinstance(self.observability, ControlServiceObservability):
            raise ControlServiceProjectionError("invalid service observability")
        _validate_absolute_path(self.oidc_config_path, label="OIDC configuration path")
        if PurePosixPath(self.oidc_config_path).suffix != ".json":
            raise ControlServiceProjectionError("OIDC configuration path must name JSON")
        _validate_absolute_path(
            self.graph_store_config_path,
            label="GraphStore configuration path",
        )
        if PurePosixPath(self.graph_store_config_path).suffix != ".json":
            raise ControlServiceProjectionError("GraphStore configuration path must name JSON")
        if self.graph_store_config_path == self.oidc_config_path:
            raise ControlServiceProjectionError(
                "OIDC and GraphStore configuration paths must differ"
            )
        if not self.workload_identity.strip() or len(self.workload_identity) > 512:
            raise ControlServiceProjectionError("workload identity must not be blank")
        if _DIGEST.fullmatch(self.rollback_digest) is None:
            raise ControlServiceProjectionError(
                "rollback digest must be an immutable sha256 digest"
            )
        if self.image.endswith(f"@{self.rollback_digest}"):
            raise ControlServiceProjectionError("rollback digest must differ from the active image")

        environment = _validate_pairs(self.environment, label="environment")
        secret_bindings = _validate_secret_pairs(self.secret_bindings)
        if any(_ENVIRONMENT_NAME.fullmatch(name) is None for name, _value in environment):
            raise ControlServiceProjectionError("environment names must use uppercase shell syntax")
        if set(dict(environment)) & set(dict(secret_bindings)):
            raise ControlServiceProjectionError("secret and non-secret environment names overlap")
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "secret_bindings", secret_bindings)
        object.__setattr__(
            self,
            "command",
            (
                "control",
                "serve",
                "--host",
                "0.0.0.0",
                "--port",
                str(self.port),
                "--oidc-config",
                self.oidc_config_path,
                "--graph-store-config",
                self.graph_store_config_path,
            ),
        )

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """Return the exact CORS origins from the single hosted OIDC source."""
        return self.oidc.allowed_origins

    def as_dict(self) -> dict[str, object]:
        """Render the deterministic provider-neutral service request."""
        return {
            "service_id": self.service_id,
            "profile_id": self.profile_id,
            "image": self.image,
            "command": list(self.command),
            "port": self.port,
            "probes": {
                "liveness_path": self.probes.liveness_path,
                "readiness_path": self.probes.readiness_path,
            },
            "resources": {
                "cpu_millis": self.resources.cpu_millis,
                "memory_mib": self.resources.memory_mib,
            },
            "scaling": {
                "minimum_instances": self.scaling.minimum_instances,
                "maximum_instances": self.scaling.maximum_instances,
                "shutdown_grace_seconds": self.scaling.shutdown_grace_seconds,
            },
            "environment": dict(self.environment),
            "secret_bindings": {
                name: {"provider": reference.provider, "reference": reference.reference}
                for name, reference in self.secret_bindings
            },
            "workload_identity": self.workload_identity,
            "ingress": {
                "visibility": self.ingress.visibility.value,
                "allowed_origins": list(self.allowed_origins),
            },
            "oidc": self.oidc.model_dump(mode="json"),
            "oidc_config_path": self.oidc_config_path,
            "graph_store_config_path": self.graph_store_config_path,
            "graph_store": self.graph_store.as_dict(),
            "observability": {
                "log_destination": self.observability.log_destination,
                "alert_target": self.observability.alert_target,
                "retention_days": self.observability.retention_days,
            },
            "rollback_digest": self.rollback_digest,
        }


@dataclass(frozen=True, slots=True)
class ControlServiceTemplate:
    """One validated provider service projection before native resource rendering."""

    schema: str
    provider_id: str
    request: ResolvedControlServiceRequest

    def __post_init__(self) -> None:
        if self.schema != CONTROL_SERVICE_PROJECTION_SCHEMA:
            raise ControlServiceProjectionError("unsupported Control service projection contract")
        if _IDENTIFIER.fullmatch(self.provider_id) is None:
            raise ControlServiceProjectionError("invalid service provider identifier")
        if not isinstance(self.request, ResolvedControlServiceRequest):
            raise ControlServiceProjectionError("invalid resolved Control service request")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "provider_id": self.provider_id,
            "request": self.request.as_dict(),
        }


@runtime_checkable
class ControlServiceTemplateFactory(Protocol):
    """Build one provider-specific template from validated service intent."""

    def build(self, request: ResolvedControlServiceRequest) -> ControlServiceTemplate:
        """Return one deterministic provider service projection."""
        ...


@dataclass(frozen=True, slots=True)
class ControlServiceRuntime:
    """One selected service provider and its template factory."""

    provider_id: str
    region: str
    templates: ControlServiceTemplateFactory

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.provider_id) is None:
            raise ControlServiceProjectionError("invalid service runtime provider")
        if not self.region.strip() or len(self.region) > 128:
            raise ControlServiceProjectionError("invalid service runtime region")
        if not isinstance(self.templates, ControlServiceTemplateFactory):
            raise ControlServiceProjectionError("service template factory has the wrong type")


@dataclass(frozen=True, slots=True)
class StaticAssetBundle:
    """Druff artifact identity kept separate from the Control service contract."""

    artifact_digest: str
    entrypoint: str
    bootstrap_path: str
    bootstrap_digest: str
    security_headers: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if _DIGEST.fullmatch(self.artifact_digest) is None:
            raise ControlServiceProjectionError("static artifact digest must use sha256")
        _validate_route_path(self.entrypoint, label="static artifact entrypoint")
        _validate_route_path(self.bootstrap_path, label="bootstrap descriptor path")
        if _DIGEST.fullmatch(self.bootstrap_digest) is None:
            raise ControlServiceProjectionError("bootstrap descriptor digest must use sha256")
        headers = _validate_pairs(self.security_headers, label="security header")
        if not headers or any(_HEADER_NAME.fullmatch(name) is None for name, _value in headers):
            raise ControlServiceProjectionError("static security headers must use lowercase names")
        object.__setattr__(self, "security_headers", headers)

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_digest": self.artifact_digest,
            "entrypoint": self.entrypoint,
            "bootstrap_path": self.bootstrap_path,
            "bootstrap_digest": self.bootstrap_digest,
            "security_headers": dict(self.security_headers),
        }


_GRAPH_STORE_BINDING_TYPES = (
    LocalGraphStoreBinding,
    GCSGraphStoreBinding,
    S3GraphStoreBinding,
    AzureBlobGraphStoreBinding,
    OCIObjectGraphStoreBinding,
)


def _validate_prefix(value: str) -> None:
    if (
        not isinstance(value, str)
        or _PREFIX.fullmatch(value) is None
        or value.startswith("/")
        or value.endswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ControlServiceProjectionError("invalid GraphStore prefix")


def _validate_absolute_path(value: str, *, label: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ControlServiceProjectionError(f"{label} must be an absolute POSIX path")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ControlServiceProjectionError(f"{label} must be an absolute POSIX path")


def _validate_route_path(value: str, *, label: str) -> None:
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path != value
        or len(value) > 1024
    ):
        raise ControlServiceProjectionError(f"{label} must be a bounded absolute route")


def _validate_pairs(
    values: tuple[tuple[str, str], ...],
    *,
    label: str,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, tuple):
        raise ControlServiceProjectionError(f"{label} values must be an immutable tuple")
    normalized: list[tuple[str, str]] = []
    names: set[str] = set()
    for item in values:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            or not item[0]
            or not item[1]
            or len(item[0]) > 128
            or len(item[1]) > 4096
            or "\x00" in item[1]
            or item[0] in names
        ):
            raise ControlServiceProjectionError(f"invalid or duplicate {label}")
        names.add(item[0])
        normalized.append(item)
    return tuple(sorted(normalized))


def _validate_secret_pairs(
    values: tuple[tuple[str, SecretReference], ...],
) -> tuple[tuple[str, SecretReference], ...]:
    if not isinstance(values, tuple):
        raise ControlServiceProjectionError("secret bindings must be an immutable tuple")
    normalized: list[tuple[str, SecretReference]] = []
    names: set[str] = set()
    for item in values:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or _ENVIRONMENT_NAME.fullmatch(item[0]) is None
            or not isinstance(item[1], SecretReference)
            or item[0] in names
        ):
            raise ControlServiceProjectionError("invalid or duplicate secret binding")
        names.add(item[0])
        normalized.append(item)
    return tuple(sorted(normalized, key=lambda item: item[0]))


def graph_store_binding_from_json(value: str) -> GraphStoreBinding:
    """Parse one bounded closed GraphStore locator without importing a provider SDK."""
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_GRAPH_STORE_CONFIG_BYTES:
        raise ControlServiceProjectionError("GraphStore configuration exceeds its size bound")
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as error:
        raise ControlServiceProjectionError("GraphStore configuration is not valid JSON") from error
    if not isinstance(payload, Mapping):
        raise ControlServiceProjectionError("GraphStore configuration must be an object")
    kind = payload.get("kind")
    if not isinstance(kind, str):
        raise ControlServiceProjectionError("GraphStore configuration requires a kind")
    constructors: dict[str, tuple[type[object], frozenset[str], frozenset[str]]] = {
        "local": (LocalGraphStoreBinding, frozenset({"kind", "root"}), frozenset()),
        "gcs": (
            GCSGraphStoreBinding,
            frozenset({"kind", "bucket"}),
            frozenset({"prefix"}),
        ),
        "s3": (
            S3GraphStoreBinding,
            frozenset({"kind", "bucket"}),
            frozenset({"prefix", "expected_bucket_owner"}),
        ),
        "azure_blob": (
            AzureBlobGraphStoreBinding,
            frozenset({"kind", "account_url", "container"}),
            frozenset({"prefix"}),
        ),
        "oci_object": (
            OCIObjectGraphStoreBinding,
            frozenset({"kind", "namespace", "bucket"}),
            frozenset({"prefix"}),
        ),
    }
    selected = constructors.get(kind)
    if selected is None:
        raise ControlServiceProjectionError("GraphStore configuration kind is unsupported")
    constructor, required, optional = selected
    keys = frozenset(payload)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise ControlServiceProjectionError("GraphStore configuration fields are invalid")
    arguments = {key: payload[key] for key in keys if key != "kind"}
    try:
        binding = constructor(**arguments)
    except (TypeError, ValueError) as error:
        raise ControlServiceProjectionError("GraphStore configuration is invalid") from error
    if not isinstance(binding, _GRAPH_STORE_BINDING_TYPES):  # pragma: no cover - closed table
        raise ControlServiceProjectionError("GraphStore configuration produced an invalid binding")
    return binding
