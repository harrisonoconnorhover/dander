"""Local hosted control-plane Compose projection and verification."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

import pytest
import yaml
from pydantic import ValidationError

import dander.deployment.local_compose as local_compose
from dander.control.auth import HostedOIDCDeploymentInput
from dander.deployment.local_compose import (
    LOCAL_CONTROL_PLANE_SCHEMA,
    LocalControlPlaneError,
    LocalControlPlaneInput,
    preflight_local_control_plane,
    project_local_control_service,
    render_local_control_plane,
    verify_live_local_control_plane,
    write_local_control_plane,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infra" / "local" / "compose.yaml"
_DANDER = "ghcr.io/example/dander@sha256:" + "a" * 64
_DANDER_ROLLBACK = "ghcr.io/example/dander@sha256:" + "b" * 64
_DRUFF = "ghcr.io/example/druff@sha256:" + "c" * 64
_DRUFF_ROLLBACK = "ghcr.io/example/druff@sha256:" + "d" * 64


def _source() -> LocalControlPlaneInput:
    return LocalControlPlaneInput(
        dander_image=_DANDER,
        dander_rollback_image=_DANDER_ROLLBACK,
        druff_image=_DRUFF,
        druff_rollback_image=_DRUFF_ROLLBACK,
        oidc=HostedOIDCDeploymentInput(
            api_url="https://localhost:8443",
            issuer="https://issuer.example.test",
            jwks_uri="https://issuer.example.test/.well-known/jwks.json",
            public_client_id="druff-local-spa",
            api_audience="dander-local-control",
            redirect_uri="https://localhost:8443/auth/callback",
            logout_uri="https://localhost:8443/signed-out",
            allowed_origins=("https://localhost:8443",),
        ),
    )


def _write_tls(directory: Path) -> None:
    tls = directory / "tls"
    (tls / "localhost.crt").write_text(
        "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    (tls / "localhost.key").write_text(
        "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    (tls / "localhost.crt").chmod(0o444)
    (tls / "localhost.key").chmod(0o444)


def test_local_input_is_closed_immutable_and_requires_exact_digest_rollback() -> None:
    source = _source()
    with pytest.raises(ValidationError, match="frozen"):
        source.dander_image = _DANDER_ROLLBACK  # type: ignore[misc]
    with pytest.raises(ValidationError, match="immutable sha256"):
        LocalControlPlaneInput.model_validate(
            {**source.model_dump(), "dander_image": "ghcr.io/example/dander:latest"}
        )
    with pytest.raises(ValidationError, match="same repository"):
        LocalControlPlaneInput.model_validate(
            {
                **source.model_dump(),
                "dander_rollback_image": "ghcr.io/other/dander@sha256:" + "b" * 64,
            }
        )
    with pytest.raises(ValidationError, match="exact localhost"):
        LocalControlPlaneInput.model_validate(
            {
                **source.model_dump(exclude={"oidc"}),
                "oidc": {
                    **source.oidc.model_dump(),
                    "api_url": "https://127.0.0.1:8443",
                },
            }
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        LocalControlPlaneInput.model_validate({**source.model_dump(), "extensions": {}})


def test_projection_reuses_d6_service_and_one_oidc_source(tmp_path: Path) -> None:
    source = _source()
    rendered = render_local_control_plane(source, output_directory=tmp_path / "local")
    repeated = render_local_control_plane(source, output_directory=tmp_path / "local")
    service = project_local_control_service(source)

    assert rendered == repeated
    assert service.command == (
        "control",
        "serve",
        "--host",
        "0.0.0.0",
        "--port",
        "8770",
        "--oidc-config",
        "/etc/dander/control-oidc.json",
        "--graph-store-config",
        "/etc/dander/control-graph-store.json",
    )
    assert json.loads(rendered["control-oidc.json"]) == source.oidc.model_dump(mode="json")
    assert json.loads(rendered["control-graph-store.json"]) == {
        "kind": "local",
        "root": "/var/lib/dander/control",
    }
    manifest = json.loads(rendered["deployment.json"])
    assert manifest["schema"] == LOCAL_CONTROL_PLANE_SCHEMA
    assert manifest["service"]["command"] == list(service.command)
    combined = "".join(rendered.values()).casefold()
    assert "client_secret" not in combined
    assert "password" not in combined
    assert "credential" not in combined


def test_write_and_preflight_require_exact_files_and_sealed_tls(tmp_path: Path) -> None:
    source = _source()
    output = tmp_path / "local"
    written = write_local_control_plane(source, output_directory=output)
    _write_tls(output)

    assert {path.name for path in written} == {
        "active.env",
        "bootstrap.json",
        "control-graph-store.json",
        "control-oidc.json",
        "deployment.json",
        "public-client.json",
        "rollback.env",
    }
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o444 for path in written)
    assert preflight_local_control_plane(
        source,
        output_directory=output,
        compose_file=COMPOSE_FILE,
    ) == (
        "projection-current",
        "images-immutable",
        "tls-readable-read-only",
        "compose-no-build",
        "volume-init-owner-mode-only",
        "loopback-edge-only",
    )

    (output / "bootstrap.json").chmod(0o600)
    (output / "bootstrap.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(LocalControlPlaneError, match="missing or stale"):
        preflight_local_control_plane(
            source,
            output_directory=output,
            compose_file=COMPOSE_FILE,
        )


def test_compose_has_no_build_and_only_one_narrow_root_initializer() -> None:
    payload = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    services: Mapping[str, Any] = payload["services"]

    assert set(services) == {"volume-init", "control", "druff", "edge"}
    assert all("build" not in service for service in services.values())
    assert all(
        "environment" not in service and "env_file" not in service for service in services.values()
    )
    assert services["volume-init"]["user"] == "0:0"
    assert services["volume-init"]["network_mode"] == "none"
    assert services["volume-init"]["cap_add"] == ["CHOWN", "FOWNER"]
    assert services["volume-init"]["cap_drop"] == ["ALL"]
    assert services["volume-init"]["command"] == [
        "chown 65532:65532 /var/lib/dander/control && chmod 0700 /var/lib/dander/control"
    ]
    assert services["control"]["depends_on"]["volume-init"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["control"]["command"] == list(project_local_control_service(_source()).command)
    for name in ("control", "druff", "edge"):
        assert services[name]["user"] == "65532:65532"
        assert services[name]["read_only"] is True
        assert services[name]["cap_drop"] == ["ALL"]
    assert "ports" not in services["control"]
    assert "ports" not in services["druff"]
    assert services["edge"]["ports"] == ["127.0.0.1:8443:8443"]


@pytest.mark.parametrize("environment", ["active", "rollback"])
def test_live_verifier_checks_exact_containers_and_bounded_https(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: Literal["active", "rollback"],
) -> None:
    source = _source()
    output = tmp_path / "local"
    write_local_control_plane(source, output_directory=output)
    _write_tls(output)
    identifiers = {
        "volume-init": "init-id",
        "control": "control-id",
        "druff": "druff-id",
        "edge": "edge-id",
    }
    inspections = {
        identifier: _inspection(service, source, environment=environment)
        for service, identifier in identifiers.items()
    }

    def fake_run(command: tuple[str, ...]) -> SimpleNamespace:
        if command[:2] == ("docker", "inspect"):
            return SimpleNamespace(stdout=json.dumps([inspections[command[2]]]))
        service = command[-1]
        return SimpleNamespace(stdout=identifiers[service] + "\n")

    def fake_https(url: str, _context: object) -> tuple[bytes, dict[str, str]]:
        if url.endswith("/healthz"):
            return b'{"status":"ok"}', {}
        if url.endswith("/readyz"):
            return b'{"status":"ready"}', {}
        return (output / "bootstrap.json").read_bytes(), {
            "cache-control": "no-store",
            "content-security-policy": "default-src 'self'",
            "strict-transport-security": "max-age=31536000",
        }

    monkeypatch.setattr(local_compose, "_run", fake_run)
    monkeypatch.setattr(local_compose, "_https_get", fake_https)
    monkeypatch.setattr(local_compose, "_create_ssl_context", lambda _certificate: object())

    result = verify_live_local_control_plane(
        source,
        output_directory=output,
        compose_file=COMPOSE_FILE,
        environment=environment,
    )
    assert result["status"] == "passed"
    assert result["environment"] == environment
    assert result["containers"] == identifiers
    checks = result["checks"]
    assert isinstance(checks, list)
    assert checks[-4:] == [
        "containers-exact",
        "control-ready",
        "bootstrap-exact",
        "headers-current",
    ]


def _inspection(
    service: str,
    source: LocalControlPlaneInput,
    *,
    environment: Literal["active", "rollback"],
) -> dict[str, object]:
    initializer = service == "volume-init"
    if environment == "active":
        dander_image, druff_image = source.dander_image, source.druff_image
    else:
        dander_image, druff_image = source.dander_rollback_image, source.druff_rollback_image
    image = dander_image if service in {"volume-init", "control"} else druff_image
    ports: dict[str, object] = {}
    if service == "edge":
        ports = {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8443"}]}
    return {
        "Config": {
            "Image": image,
            "User": "0:0" if initializer else "65532:65532",
            "Entrypoint": ["/bin/sh", "-c"] if initializer else None,
            "Cmd": (
                ["chown 65532:65532 /var/lib/dander/control && chmod 0700 /var/lib/dander/control"]
                if initializer
                else None
            ),
        },
        "HostConfig": {
            "NetworkMode": "none" if initializer else "dander-local-control-plane_default",
            "CapAdd": ["CAP_CHOWN", "CAP_FOWNER"] if initializer else None,
            "CapDrop": ["ALL"],
            "ReadonlyRootfs": True,
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "State": {
            "Status": "exited" if initializer else "running",
            "ExitCode": 0,
        },
        "NetworkSettings": {"Ports": ports},
        "Mounts": _mounts(service),
    }


def _mounts(service: str) -> list[dict[str, object]]:
    values = {
        "volume-init": [("/var/lib/dander/control", True)],
        "control": [
            ("/etc/dander/control-oidc.json", False),
            ("/etc/dander/control-graph-store.json", False),
            ("/var/lib/dander/control", True),
        ],
        "druff": [("/app/bootstrap.json", False)],
        "edge": [
            ("/etc/caddy/local.Caddyfile", False),
            ("/etc/caddy/tls/localhost.crt", False),
            ("/etc/caddy/tls/localhost.key", False),
        ],
    }
    return [
        {"Destination": destination, "RW": read_write}
        for destination, read_write in values[service]
    ]
