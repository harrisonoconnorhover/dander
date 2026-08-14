"""Deterministic local Compose projection for the hosted Dander control plane."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import urllib.request
from collections.abc import Mapping
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
    IngressVisibility,
    LocalGraphStoreBinding,
    ResolvedControlServiceRequest,
)

LOCAL_CONTROL_PLANE_SCHEMA: Final = "io.dander.local-control-plane/v1"
LOCAL_ORIGIN: Final = "https://localhost:8443"
LOCAL_CONFIG_ROOT: Final = "/etc/dander"
LOCAL_GRAPH_ROOT: Final = "/var/lib/dander/control"
LOCAL_CONTROL_PORT: Final = 8770
LOCAL_COMPOSE_PROJECT: Final = "dander-local-control-plane"

_IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9.-]+(?::[0-9]{1,5})?/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_FILES: Final = frozenset(
    {
        "active.env",
        "bootstrap.json",
        "control-graph-store.json",
        "control-oidc.json",
        "deployment.json",
        "public-client.json",
        "rollback.env",
    }
)


class LocalControlPlaneError(ValueError):
    """The local deployment input, projection, or running stack is invalid."""


class LocalControlPlaneInput(BaseModel):
    """One immutable, non-secret input for the local hosted profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dander_image: str = Field(min_length=1, max_length=2048)
    dander_rollback_image: str = Field(min_length=1, max_length=2048)
    druff_image: str = Field(min_length=1, max_length=2048)
    druff_rollback_image: str = Field(min_length=1, max_length=2048)
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
            raise ValueError("Local control-plane images must use immutable sha256 references.")
        return value

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        _validate_rollback_pair(self.dander_image, self.dander_rollback_image, "Dander")
        _validate_rollback_pair(self.druff_image, self.druff_rollback_image, "Druff")
        expected = {
            "api_url": LOCAL_ORIGIN,
            "redirect_uri": f"{LOCAL_ORIGIN}/auth/callback",
            "logout_uri": f"{LOCAL_ORIGIN}/signed-out",
            "allowed_origins": (LOCAL_ORIGIN,),
        }
        observed = {
            "api_url": self.oidc.api_url,
            "redirect_uri": self.oidc.redirect_uri,
            "logout_uri": self.oidc.logout_uri,
            "allowed_origins": self.oidc.allowed_origins,
        }
        if observed != expected:
            raise ValueError("Local hosted OIDC routes must use the exact localhost profile.")
        return self


def project_local_control_service(source: LocalControlPlaneInput) -> ResolvedControlServiceRequest:
    """Project the D6 service contract for the fixed local Compose profile."""
    return ResolvedControlServiceRequest(
        service_id="dander_control",
        profile_id="local_compose",
        image=source.dander_image,
        port=LOCAL_CONTROL_PORT,
        probes=ControlServiceProbes(),
        resources=ControlServiceResources(cpu_millis=500, memory_mib=512),
        scaling=ControlServiceScaling(
            minimum_instances=1,
            maximum_instances=1,
            shutdown_grace_seconds=30,
        ),
        environment=(),
        secret_bindings=(),
        workload_identity="local-container-user-65532",
        ingress=ControlServiceIngress(visibility=IngressVisibility.PUBLIC),
        oidc=source.oidc,
        oidc_config_path=f"{LOCAL_CONFIG_ROOT}/control-oidc.json",
        graph_store_config_path=f"{LOCAL_CONFIG_ROOT}/control-graph-store.json",
        graph_store=LocalGraphStoreBinding(root=LOCAL_GRAPH_ROOT),
        observability=ControlServiceObservability(
            log_destination="container-stdout",
            alert_target=None,
            retention_days=1,
        ),
        rollback_digest=_digest(source.dander_rollback_image),
    )


def render_local_control_plane(
    source: LocalControlPlaneInput,
    *,
    output_directory: Path,
) -> dict[str, str]:
    """Return the exact generated files without touching Docker or a provider."""
    config_directory = output_directory.expanduser().resolve(strict=False)
    if any(character in str(config_directory) for character in ("\n", "\r", "\x00")):
        raise LocalControlPlaneError("Local config directory contains an unsafe character.")
    oidc = project_hosted_oidc(source.oidc)
    service = project_local_control_service(source)
    manifest = {
        "schema": LOCAL_CONTROL_PLANE_SCHEMA,
        "dander_image": source.dander_image,
        "dander_rollback_image": source.dander_rollback_image,
        "druff_image": source.druff_image,
        "druff_rollback_image": source.druff_rollback_image,
        "service": service.as_dict(),
    }
    common = f"DANDER_LOCAL_CONFIG_DIR={config_directory}\n"
    return {
        "active.env": (
            common
            + f"DANDER_CONTROL_IMAGE={source.dander_image}\n"
            + f"DRUFF_IMAGE={source.druff_image}\n"
        ),
        "rollback.env": (
            common
            + f"DANDER_CONTROL_IMAGE={source.dander_rollback_image}\n"
            + f"DRUFF_IMAGE={source.druff_rollback_image}\n"
        ),
        "control-oidc.json": _json(source.oidc.model_dump(mode="json")),
        "control-graph-store.json": _json(service.graph_store.as_dict()),
        "bootstrap.json": _json(oidc.bootstrap.model_dump(mode="json")),
        "public-client.json": _json(oidc.public_client.model_dump(mode="json")),
        "deployment.json": _json(manifest),
    }


def write_local_control_plane(
    source: LocalControlPlaneInput,
    *,
    output_directory: Path,
) -> tuple[Path, ...]:
    """Atomically write only the closed non-secret local projection."""
    destination = output_directory.expanduser().resolve(strict=False)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    tls_directory = destination / "tls"
    tls_directory.mkdir(mode=0o700, exist_ok=True)
    tls_directory.chmod(0o700)
    rendered = render_local_control_plane(source, output_directory=destination)
    if frozenset(rendered) != _FILES:  # pragma: no cover - closed projection invariant
        raise LocalControlPlaneError("Local projection produced an unexpected file set.")
    written: list[Path] = []
    for name, content in sorted(rendered.items()):
        path = destination / name
        _atomic_write(path, content)
        written.append(path)
    return tuple(written)


def preflight_local_control_plane(
    source: LocalControlPlaneInput,
    *,
    output_directory: Path,
    compose_file: Path,
) -> tuple[str, ...]:
    """Verify exact projection, static Compose safety, and readable local TLS material."""
    destination = output_directory.expanduser().resolve(strict=True)
    expected = render_local_control_plane(source, output_directory=destination)
    if _permission_bits(destination) != 0o700:
        raise LocalControlPlaneError("Local config directory must use mode 0700.")
    for name, content in expected.items():
        path = destination / name
        if (
            path.is_symlink()
            or not path.is_file()
            or _permission_bits(path) != 0o444
            or path.read_text(encoding="utf-8") != content
        ):
            raise LocalControlPlaneError(f"Generated local file is missing or stale: {name}")
    tls_directory = destination / "tls"
    if tls_directory.is_symlink() or _permission_bits(tls_directory) != 0o700:
        raise LocalControlPlaneError("Local TLS directory must be a real directory with mode 0700.")
    certificate = tls_directory / "localhost.crt"
    private_key = tls_directory / "localhost.key"
    _verify_tls_file(certificate, marker="BEGIN CERTIFICATE")
    _verify_tls_file(private_key, marker="PRIVATE KEY")
    compose = compose_file.expanduser().resolve(strict=True).read_text(encoding="utf-8")
    required_markers = {
        "DANDER_CONTROL_IMAGE",
        "DRUFF_IMAGE",
        "condition: service_completed_successfully",
        "127.0.0.1:8443:8443",
        "cap_add:\n      - CHOWN\n      - FOWNER",
    }
    if "build:" in compose or any(marker not in compose for marker in required_markers):
        raise LocalControlPlaneError("Local Compose asset is incomplete or permits a source build.")
    return (
        "projection-current",
        "images-immutable",
        "tls-readable-read-only",
        "compose-no-build",
        "volume-init-owner-mode-only",
        "loopback-edge-only",
    )


def verify_live_local_control_plane(
    source: LocalControlPlaneInput,
    *,
    output_directory: Path,
    compose_file: Path,
    environment: Literal["active", "rollback"] = "active",
) -> dict[str, object]:
    """Verify one running exact-digest local stack without returning logs or credentials."""
    checks = list(
        preflight_local_control_plane(
            source,
            output_directory=output_directory,
            compose_file=compose_file,
        )
    )
    destination = output_directory.expanduser().resolve(strict=True)
    base = (
        "docker",
        "compose",
        "--project-name",
        LOCAL_COMPOSE_PROJECT,
        "--env-file",
        str(destination / f"{environment}.env"),
        "--file",
        str(compose_file.expanduser().resolve(strict=True)),
    )
    containers: dict[str, str] = {}
    for service in ("volume-init", "control", "druff", "edge"):
        identifier = _run((*base, "ps", "--all", "--quiet", service)).stdout.strip()
        if not identifier or "\n" in identifier:
            raise LocalControlPlaneError(f"Local Compose service is missing: {service}")
        payload = _load_inspect(identifier)
        _verify_container(service, payload, source, environment=environment)
        containers[service] = identifier
    certificate = destination / "tls" / "localhost.crt"
    context = _create_ssl_context(certificate)
    health_body, _health_headers = _https_get(f"{LOCAL_ORIGIN}/healthz", context)
    ready_body, _ready_headers = _https_get(f"{LOCAL_ORIGIN}/readyz", context)
    bootstrap_body, bootstrap_headers = _https_get(f"{LOCAL_ORIGIN}/bootstrap.json", context)
    if json.loads(health_body) != {"status": "ok"}:
        raise LocalControlPlaneError("Local Control liveness response is invalid.")
    if json.loads(ready_body) != {"status": "ready"}:
        raise LocalControlPlaneError("Local Control readiness response is invalid.")
    if bootstrap_body != (destination / "bootstrap.json").read_bytes():
        raise LocalControlPlaneError("Served bootstrap differs from the projected descriptor.")
    required_headers = ("content-security-policy", "strict-transport-security")
    if bootstrap_headers.get("cache-control") != "no-store" or any(
        not bootstrap_headers.get(name) for name in required_headers
    ):
        raise LocalControlPlaneError("Druff security or cache headers are incomplete.")
    checks.extend(("containers-exact", "control-ready", "bootstrap-exact", "headers-current"))
    return {
        "schema": LOCAL_CONTROL_PLANE_SCHEMA,
        "status": "passed",
        "environment": environment,
        "checks": checks,
        "containers": containers,
    }


def _verify_container(
    service: str,
    payload: Mapping[str, Any],
    source: LocalControlPlaneInput,
    *,
    environment: Literal["active", "rollback"],
) -> None:
    config = _mapping(payload.get("Config"), f"{service} config")
    host = _mapping(payload.get("HostConfig"), f"{service} host config")
    state = _mapping(payload.get("State"), f"{service} state")
    if host.get("ReadonlyRootfs") is not True or set(host.get("CapDrop") or ()) != {"ALL"}:
        raise LocalControlPlaneError(f"Local Compose service has excess privilege: {service}")
    security_options = host.get("SecurityOpt") or ()
    if not isinstance(security_options, list) or not any(
        str(option).startswith("no-new-privileges") for option in security_options
    ):
        raise LocalControlPlaneError(
            f"Local Compose service permits privilege escalation: {service}"
        )
    if environment == "active":
        dander_image, druff_image = source.dander_image, source.druff_image
    else:
        dander_image, druff_image = source.dander_rollback_image, source.druff_rollback_image
    expected_image = dander_image if service in {"volume-init", "control"} else druff_image
    if config.get("Image") != expected_image:
        raise LocalControlPlaneError(f"{service} is not using the accepted immutable image.")
    if service == "volume-init":
        if state.get("Status") != "exited" or state.get("ExitCode") != 0:
            raise LocalControlPlaneError("Local GraphStore volume initializer did not complete.")
        if config.get("User") != "0:0" or host.get("NetworkMode") != "none":
            raise LocalControlPlaneError("Local volume initializer has excess runtime access.")
        if set(host.get("CapAdd") or ()) != {"CAP_CHOWN", "CAP_FOWNER"}:
            raise LocalControlPlaneError(
                "Local volume initializer must receive only CHOWN and FOWNER."
            )
        if config.get("Entrypoint") != ["/bin/sh", "-c"] or config.get("Cmd") != [
            "chown 65532:65532 /var/lib/dander/control && chmod 0700 /var/lib/dander/control"
        ]:
            raise LocalControlPlaneError("Local volume initializer command is not exact.")
        _verify_mounts(service, payload)
        return
    if state.get("Status") != "running":
        raise LocalControlPlaneError(f"Local Compose service is not running: {service}")
    if config.get("User") != "65532:65532":
        raise LocalControlPlaneError(f"Local Compose service is not non-root/read-only: {service}")
    _verify_mounts(service, payload)
    ports = _mapping(
        _mapping(payload.get("NetworkSettings"), f"{service} network settings").get("Ports") or {},
        f"{service} ports",
    )
    published = [binding for bindings in ports.values() if bindings for binding in bindings]
    if service != "edge" and published:
        raise LocalControlPlaneError(
            f"Local internal service unexpectedly publishes a port: {service}"
        )
    if service == "edge" and published != [{"HostIp": "127.0.0.1", "HostPort": "8443"}]:
        raise LocalControlPlaneError("Local TLS edge must publish only loopback port 8443.")


def _verify_mounts(service: str, payload: Mapping[str, Any]) -> None:
    raw_mounts = payload.get("Mounts")
    if not isinstance(raw_mounts, list):
        raise LocalControlPlaneError(f"Docker returned invalid mounts for {service}.")
    mounts: dict[str, bool] = {}
    for value in raw_mounts:
        mount = _mapping(value, f"{service} mount")
        destination = mount.get("Destination")
        read_write = mount.get("RW")
        if not isinstance(destination, str) or not isinstance(read_write, bool):
            raise LocalControlPlaneError(f"Docker returned invalid mounts for {service}.")
        mounts[destination] = read_write
    expected = {
        "volume-init": {LOCAL_GRAPH_ROOT: True},
        "control": {
            f"{LOCAL_CONFIG_ROOT}/control-oidc.json": False,
            f"{LOCAL_CONFIG_ROOT}/control-graph-store.json": False,
            LOCAL_GRAPH_ROOT: True,
        },
        "druff": {"/app/bootstrap.json": False},
        "edge": {
            "/etc/caddy/local.Caddyfile": False,
            "/etc/caddy/tls/localhost.crt": False,
            "/etc/caddy/tls/localhost.key": False,
        },
    }
    if mounts != expected[service]:
        raise LocalControlPlaneError(
            f"Local Compose mounts differ from the accepted profile: {service}"
        )


def _https_get(url: str, context: ssl.SSLContext) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5.0, context=context) as response:  # noqa: S310
        body = response.read(1024 * 1024 + 1)
        headers = {name.casefold(): value for name, value in response.headers.items()}
    if len(body) > 1024 * 1024:
        raise LocalControlPlaneError("Local verification response exceeds 1 MiB.")
    return body, headers


def _create_ssl_context(certificate: Path) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(certificate))


def _load_inspect(identifier: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(_run(("docker", "inspect", identifier)).stdout)
    except ValueError as error:
        raise LocalControlPlaneError(
            "Docker returned invalid container inspection JSON."
        ) from error
    if not isinstance(payload, list) or len(payload) != 1:
        raise LocalControlPlaneError("Docker returned an unexpected container inspection result.")
    return _mapping(payload[0], "container inspection")


def _run(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)  # noqa: S603
    except (OSError, subprocess.CalledProcessError) as error:
        raise LocalControlPlaneError(f"Local verification command failed: {command[0]}") from error


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LocalControlPlaneError(f"Docker returned an invalid {label}.")
    return value


def _verify_tls_file(path: Path, *, marker: str) -> None:
    if path.is_symlink() or not path.is_file() or _permission_bits(path) != 0o444:
        raise LocalControlPlaneError(f"Local TLS file must be read-only mode 0444: {path.name}")
    content = path.read_bytes()
    if not content or len(content) > 1024 * 1024 or marker.encode() not in content:
        raise LocalControlPlaneError(f"Local TLS file is missing or invalid: {path.name}")


def _validate_rollback_pair(active: str, rollback: str, label: str) -> None:
    active_repository, active_digest = active.rsplit("@", 1)
    rollback_repository, rollback_digest = rollback.rsplit("@", 1)
    if active_repository != rollback_repository or active_digest == rollback_digest:
        raise ValueError(f"{label} rollback must use the same repository and a distinct digest.")


def _digest(image: str) -> str:
    return image.rsplit("@", 1)[1]


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _permission_bits(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _atomic_write(path: Path, content: str) -> None:
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, 0o444)
    temporary_path.replace(path)


def _load_input(path: Path) -> LocalControlPlaneInput:
    try:
        return LocalControlPlaneInput.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LocalControlPlaneError("Local control-plane input is invalid.") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "preflight", "verify"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, default=Path("infra/local/compose.yaml"))
    parser.add_argument("--environment", choices=("active", "rollback"), default="active")
    arguments = parser.parse_args()
    source = _load_input(arguments.input)
    if arguments.action == "render":
        written = write_local_control_plane(source, output_directory=arguments.output)
        print(json.dumps({"status": "rendered", "files": [path.name for path in written]}))
    elif arguments.action == "preflight":
        checks = preflight_local_control_plane(
            source,
            output_directory=arguments.output,
            compose_file=arguments.compose_file,
        )
        print(json.dumps({"status": "passed", "checks": checks}))
    else:
        result = verify_live_local_control_plane(
            source,
            output_directory=arguments.output,
            compose_file=arguments.compose_file,
            environment=arguments.environment,
        )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "LOCAL_COMPOSE_PROJECT",
    "LOCAL_CONTROL_PLANE_SCHEMA",
    "LOCAL_ORIGIN",
    "LocalControlPlaneError",
    "LocalControlPlaneInput",
    "preflight_local_control_plane",
    "project_local_control_service",
    "render_local_control_plane",
    "verify_live_local_control_plane",
    "write_local_control_plane",
]
