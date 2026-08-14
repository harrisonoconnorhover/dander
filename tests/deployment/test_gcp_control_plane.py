"""GCP hosted Control projection and verifier tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

import dander.deployment.gcp_control_plane as gcp_control_plane
from dander.control.auth import HostedOIDCDeploymentInput
from dander.deployment.gcp_control_plane import (
    GCP_CONTROL_PLANE_SCHEMA,
    GCPControlPlaneError,
    GCPControlPlaneInput,
    preflight_gcp_control_plane,
    project_gcp_control_service,
    render_gcp_control_plane,
    verify_live_gcp_control_plane,
    write_gcp_control_plane,
)

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "dander-unit-project"
NUMBER = "123456789012"
REGION = "us-central1"
CONTROL_URL = f"https://dander-control-d7-{NUMBER}.{REGION}.run.app"
DRUFF_URL = f"https://druff-control-d7-{NUMBER}.{REGION}.run.app"
_DANDER = f"{REGION}-docker.pkg.dev/{PROJECT}/dander/control@sha256:" + "a" * 64
_DANDER_ROLLBACK = f"{REGION}-docker.pkg.dev/{PROJECT}/dander/control@sha256:" + "b" * 64
_DRUFF = f"{REGION}-docker.pkg.dev/{PROJECT}/dander/druff@sha256:" + "c" * 64
_DRUFF_ROLLBACK = f"{REGION}-docker.pkg.dev/{PROJECT}/dander/druff@sha256:" + "d" * 64


def _source(**updates: object) -> GCPControlPlaneInput:
    values: dict[str, object] = {
        "project_id": PROJECT,
        "project_number": NUMBER,
        "region": REGION,
        "bootstrap_service_account": f"dander-bootstrap@{PROJECT}.iam.gserviceaccount.com",
        "state_bucket": "dander-unit-state",
        "state_prefix": "dander/control-plane/gcp-d7/attempt-1/state",
        "graph_bucket": "dander-unit-control-graphs",
        "dander_image": _DANDER,
        "dander_rollback_image": _DANDER_ROLLBACK,
        "druff_image": _DRUFF,
        "druff_rollback_image": _DRUFF_ROLLBACK,
        "oidc": HostedOIDCDeploymentInput(
            api_url=CONTROL_URL,
            issuer="https://issuer.example.test/default",
            jwks_uri="https://issuer.example.test/default/.well-known/jwks.json",
            public_client_id="druff-gcp-spa",
            api_audience="dander-gcp-control",
            redirect_uri=f"{DRUFF_URL}/auth/callback",
            logout_uri=f"{DRUFF_URL}/signed-out",
            allowed_origins=(DRUFF_URL,),
        ),
    }
    values.update(updates)
    return GCPControlPlaneInput.model_validate(values)


def test_input_is_closed_immutable_and_requires_exact_cloud_run_topology() -> None:
    source = _source()
    with pytest.raises(ValidationError, match="frozen"):
        source.region = "us-east1"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="immutable sha256"):
        _source(dander_image=f"{REGION}-docker.pkg.dev/{PROJECT}/dander/control:latest")
    with pytest.raises(ValidationError, match="same repository"):
        _source(
            dander_rollback_image=(
                f"{REGION}-docker.pkg.dev/{PROJECT}/other/control@sha256:" + "b" * 64
            )
        )
    with pytest.raises(ValidationError, match="retained root prefix"):
        _source(state_prefix="dander/state")
    with pytest.raises(ValidationError, match="exact Cloud Run origins"):
        _source(
            oidc=HostedOIDCDeploymentInput(
                **{
                    **source.oidc.model_dump(),
                    "redirect_uri": f"{CONTROL_URL}/auth/callback",
                    "logout_uri": f"{CONTROL_URL}/signed-out",
                    "allowed_origins": (CONTROL_URL,),
                }
            )
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        GCPControlPlaneInput.model_validate({**source.model_dump(), "extensions": {}})


def test_packaged_example_is_a_valid_closed_input() -> None:
    example = GCPControlPlaneInput.model_validate_json(
        (ROOT / "infra" / "gcp-control" / "gcp-control-plane.example.json").read_text(
            encoding="utf-8"
        )
    )

    assert example.control_url == ("https://dander-control-d7-123456789012.us-central1.run.app")
    assert set(render_gcp_control_plane(example)) == {
        "Caddyfile",
        "active.tfvars.json",
        "bootstrap.json",
        "control-graph-store.json",
        "control-oidc.json",
        "deployment.json",
        "public-client.json",
        "rollback.tfvars.json",
    }


def test_projection_reuses_d6_and_renders_closed_deterministic_files() -> None:
    source = _source()
    service = project_gcp_control_service(source)
    rendered = render_gcp_control_plane(source)

    assert rendered == render_gcp_control_plane(source)
    assert set(rendered) == {
        "Caddyfile",
        "active.tfvars.json",
        "bootstrap.json",
        "control-graph-store.json",
        "control-oidc.json",
        "deployment.json",
        "public-client.json",
        "rollback.tfvars.json",
    }
    assert service.workload_identity == (
        f"gcp-service-account://dander-control-d7@{PROJECT}.iam.gserviceaccount.com"
    )
    assert service.command[-4:] == (
        "--oidc-config",
        "/etc/dander/oidc/control-oidc.json",
        "--graph-store-config",
        "/etc/dander/graph-store/control-graph-store.json",
    )
    assert json.loads(rendered["control-graph-store.json"]) == {
        "kind": "gcs",
        "bucket": "dander-unit-control-graphs",
        "prefix": "dander-control/v1",
    }
    active = json.loads(rendered["active.tfvars.json"])
    rollback = json.loads(rendered["rollback.tfvars.json"])
    manifest = json.loads(rendered["deployment.json"])
    assert active["dander_image"] == _DANDER
    assert rollback["dander_image"] == _DANDER_ROLLBACK
    assert active["control_args"] == list(service.command)
    assert rollback["control_args"] == list(service.command)
    assert active["control_oidc_json"] == rendered["control-oidc.json"]
    assert manifest["schema"] == GCP_CONTROL_PLANE_SCHEMA
    assert manifest["graph_soft_delete_retention_seconds"] == 0
    assert manifest["control_url"] == CONTROL_URL
    assert manifest["druff_url"] == DRUFF_URL
    assert "root * /app" in rendered["Caddyfile"]
    assert "root * /etc/dander/bootstrap" in rendered["Caddyfile"]
    assert 'respond `{"status":"ok"}` 200' in rendered["Caddyfile"]
    assert 'respond `{"status":"ready"}` 200' in rendered["Caddyfile"]
    assert "log" not in {line.strip() for line in rendered["Caddyfile"].splitlines()}
    combined = "".join(rendered.values()).casefold()
    assert "client_secret" not in combined
    assert "refresh_token" not in combined
    assert "private_key" not in combined


def test_write_and_preflight_are_mode_bounded_and_backend_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source()
    output = tmp_path / "gcp"
    commands: list[tuple[str, ...]] = []

    def fake_run(
        command: tuple[str, ...], *, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert environment is not None
        assert str(output / "terraform-data") == environment["TF_DATA_DIR"]
        assert (
            "-backend=false" in command
            or command[-2:] == ("fmt", "-check")
            or command[-1] == "validate"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(gcp_control_plane, "_run", fake_run)
    written = write_gcp_control_plane(source, output_directory=output)
    result = preflight_gcp_control_plane(
        source,
        output_directory=output,
        terraform_root=ROOT / "infra" / "gcp-control",
    )

    assert result["status"] == "passed"
    assert {path.name for path in written} == set(render_gcp_control_plane(source))
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o444 for path in written)
    assert len(commands) == 3


@pytest.mark.parametrize("environment", ["active", "rollback"])
def test_live_verifier_is_read_only_and_checks_exact_provider_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: Literal["active", "rollback"],
) -> None:
    source = _source()
    output = tmp_path / "gcp"
    write_gcp_control_plane(source, output_directory=output)
    rendered = render_gcp_control_plane(source)
    images = (
        {"control": _DANDER, "druff": _DRUFF}
        if environment == "active"
        else {"control": _DANDER_ROLLBACK, "druff": _DRUFF_ROLLBACK}
    )
    calls: list[tuple[str, ...]] = []

    def fake_gcloud(
        _source_value: GCPControlPlaneInput,
        *arguments: str,
        regional: bool = True,
    ) -> object:
        calls.append(arguments)
        if arguments[:3] == ("run", "services", "describe"):
            workload: Literal["control", "druff"] = (
                "control" if arguments[3] == source.control_service_name else "druff"
            )
            return _service(source, workload, images[workload])
        if arguments[:3] == ("storage", "buckets", "describe"):
            assert regional is False
            return _bucket(source)
        if arguments[:3] == ("storage", "buckets", "get-iam-policy"):
            return {
                "bindings": [
                    {
                        "role": "roles/storage.objectUser",
                        "members": [f"serviceAccount:{source.control_service_account}"],
                    }
                ]
            }
        if arguments[:3] == ("projects", "get-iam-policy", PROJECT):
            return {"bindings": [{"role": "roles/viewer", "members": ["user:test@example.test"]}]}
        if arguments[:4] == ("iam", "service-accounts", "keys", "list"):
            return [{"keyType": "SYSTEM_MANAGED"}]
        raise AssertionError(arguments)

    secret_contents = {
        "control-oidc": rendered["control-oidc.json"],
        "graph-store": rendered["control-graph-store.json"],
        "druff-bootstrap": rendered["bootstrap.json"],
        "druff-caddy": rendered["Caddyfile"],
    }

    def fake_run(
        command: tuple[str, ...], *, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert environment is None
        assert command[4] == "1"
        secret = next(value.split("=", 1)[1] for value in command if value.startswith("--secret="))
        suffix = secret.removeprefix(f"{source.control_service_name}-")
        return subprocess.CompletedProcess(command, 0, secret_contents[suffix], "")

    def fake_http(url: str) -> tuple[bytes, dict[str, str]]:
        if url == source.control_url + "/healthz":
            return b'{"status":"ok"}', {}
        if url == source.control_url + "/readyz":
            return b'{"status":"ready"}', {}
        if url == source.druff_url + "/healthz":
            return b'{"status":"ok"}', {}
        if url == source.druff_url + "/readyz":
            return b'{"status":"ready"}', {}
        if url == source.druff_url + "/bootstrap.json":
            return rendered["bootstrap.json"].encode(), {
                "cache-control": "no-store",
                "content-security-policy": "default-src 'self'",
                "strict-transport-security": "max-age=31536000",
            }
        if url == source.druff_url + "/auth/callback":
            return b"<html></html>", {"content-security-policy": "default-src 'self'"}
        if url == source.druff_url + "/signed-out":
            return b"<html></html>", {"content-security-policy": "default-src 'self'"}
        raise AssertionError(url)

    monkeypatch.setattr(gcp_control_plane, "_gcloud_json", fake_gcloud)
    monkeypatch.setattr(gcp_control_plane, "_run", fake_run)
    monkeypatch.setattr(gcp_control_plane, "_http", fake_http)
    monkeypatch.setattr(
        gcp_control_plane,
        "_http_error",
        lambda _url: (b'{"error":{"code":"unauthorized"}}', {}, 401),
    )

    result = verify_live_gcp_control_plane(
        source,
        output_directory=output,
        environment=environment,
    )

    assert result["status"] == "passed"
    assert result["environment"] == environment
    assert all(call[0] not in {"create", "update", "delete", "apply"} for call in calls)


def test_live_verifier_rejects_recoverable_graph_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source()
    output = tmp_path / "gcp"
    write_gcp_control_plane(source, output_directory=output)

    def fake_gcloud(
        _source_value: GCPControlPlaneInput,
        *arguments: str,
        regional: bool = True,
    ) -> object:
        if arguments[:3] == ("run", "services", "describe"):
            workload: Literal["control", "druff"] = (
                "control" if arguments[3] == source.control_service_name else "druff"
            )
            image = _DANDER if workload == "control" else _DRUFF
            return _service(source, workload, image)
        if arguments[:3] == ("storage", "buckets", "describe"):
            bucket = _bucket(source)
            policy = bucket["softDeletePolicy"]
            assert isinstance(policy, dict)
            policy["retentionDurationSeconds"] = "604800"
            return bucket
        raise AssertionError(arguments)

    monkeypatch.setattr(gcp_control_plane, "_gcloud_json", fake_gcloud)
    with pytest.raises(GCPControlPlaneError, match="soft delete is not disabled"):
        verify_live_gcp_control_plane(source, output_directory=output)


def test_live_verifier_rejects_misdirected_startup_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source()
    output = tmp_path / "gcp"
    write_gcp_control_plane(source, output_directory=output)
    control = _service(source, "control", _DANDER)
    spec = control["spec"]
    assert isinstance(spec, dict)
    template = spec["template"]
    assert isinstance(template, dict)
    revision = template["spec"]
    assert isinstance(revision, dict)
    volumes = revision["volumes"]
    assert isinstance(volumes, list)
    volume = volumes[0]
    assert isinstance(volume, dict)
    secret = volume["secret"]
    assert isinstance(secret, dict)
    secret["secretName"] = "wrong-startup-config"

    monkeypatch.setattr(
        gcp_control_plane,
        "_gcloud_json",
        lambda *_arguments, **_keywords: control,
    )
    with pytest.raises(GCPControlPlaneError, match="startup config volume differs"):
        verify_live_gcp_control_plane(source, output_directory=output)


def test_secret_hashes_use_the_exact_mounted_numeric_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    rendered = render_gcp_control_plane(source)
    mounted_versions = {
        "control-oidc": "2",
        "graph-store": "1",
        "druff-bootstrap": "1",
        "druff-caddy": "1",
    }
    observed: list[tuple[str, str]] = []

    def fake_run(
        command: tuple[str, ...], *, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert environment is None
        version = command[4]
        secret = next(value.split("=", 1)[1] for value in command if value.startswith("--secret="))
        suffix = secret.removeprefix(f"{source.control_service_name}-")
        observed.append((suffix, version))
        content = (
            "stale-config\n"
            if (suffix, version) == ("control-oidc", "2")
            else rendered[
                {
                    "graph-store": "control-graph-store.json",
                    "druff-bootstrap": "bootstrap.json",
                    "druff-caddy": "Caddyfile",
                }[suffix]
            ]
        )
        return subprocess.CompletedProcess(command, 0, content, "")

    monkeypatch.setattr(gcp_control_plane, "_run", fake_run)
    with pytest.raises(GCPControlPlaneError, match="startup config payload differs"):
        gcp_control_plane._verify_secret_hashes(source, rendered, mounted_versions)

    assert observed == [("control-oidc", "2")]


@pytest.mark.parametrize("failure", ["stale-generation", "split-traffic"])
def test_live_verifier_rejects_a_nonexclusive_or_stale_serving_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    source = _source()
    output = tmp_path / "gcp"
    write_gcp_control_plane(source, output_directory=output)
    control = _service(source, "control", _DANDER)
    status = control["status"]
    assert isinstance(status, dict)
    if failure == "stale-generation":
        status["observedGeneration"] = 2
    else:
        revision = status["latestReadyRevisionName"]
        status["traffic"] = [
            {"latestRevision": True, "percent": 50, "revisionName": revision},
            {"latestRevision": False, "percent": 50, "revisionName": "older-revision"},
        ]

    monkeypatch.setattr(
        gcp_control_plane,
        "_gcloud_json",
        lambda *_arguments, **_keywords: control,
    )
    with pytest.raises(GCPControlPlaneError, match="serving revision|traffic percent"):
        verify_live_gcp_control_plane(source, output_directory=output)


def test_terraform_root_disables_soft_delete_only_on_disposable_graph_bucket() -> None:
    root = ROOT / "infra" / "gcp-control"
    main = (root / "main.tf").read_text(encoding="utf-8")
    versions = (root / "versions.tf").read_text(encoding="utf-8")
    assert 'backend "gcs" {}' in versions
    assert main.count('resource "google_storage_bucket"') == 1
    assert "soft_delete_policy" in main
    assert "retention_duration_seconds = 0" in main
    assert 'role   = "roles/storage.objectUser"' in main
    assert "roles/storage.admin" not in main
    assert "client_secret" not in main


def _service(
    source: GCPControlPlaneInput,
    workload: Literal["control", "druff"],
    image: str,
) -> dict[str, object]:
    service_name = (
        source.control_service_name if workload == "control" else source.druff_service_name
    )
    url = source.control_url if workload == "control" else source.druff_url
    identity = (
        source.control_service_account if workload == "control" else source.druff_service_account
    )
    container: dict[str, object] = {"image": image}
    if workload == "control":
        container["args"] = list(project_gcp_control_service(source).command)
        volume_values = (
            (
                "control-oidc",
                "control-oidc",
                f"{source.control_service_name}-control-oidc",
                "control-oidc.json",
                "/etc/dander/oidc",
            ),
            (
                "graph-store",
                "graph-store",
                f"{source.control_service_name}-graph-store",
                "control-graph-store.json",
                "/etc/dander/graph-store",
            ),
        )
    else:
        container["command"] = ["/usr/bin/caddy"]
        container["args"] = [
            "run",
            "--config",
            "/etc/dander/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
        ]
        volume_values = (
            (
                "druff-bootstrap",
                "druff-bootstrap",
                f"{source.control_service_name}-druff-bootstrap",
                "bootstrap.json",
                "/etc/dander/bootstrap",
            ),
            (
                "druff-caddy",
                "druff-caddy",
                f"{source.control_service_name}-druff-caddy",
                "Caddyfile",
                "/etc/dander/caddy",
            ),
        )
    container["volumeMounts"] = [
        {"name": name, "mountPath": mount_path}
        for name, _suffix, _secret, _path, mount_path in volume_values
    ]
    secret_aliases = {
        f"sm-{index}": f"projects/{source.project_id}/secrets/{secret}"
        for index, (_name, _suffix, secret, _path, _mount_path) in enumerate(volume_values, start=1)
    }
    revision_name = f"{service_name}-00003-unit"
    return {
        "metadata": {
            "name": service_name,
            "generation": 3,
            "annotations": {
                "run.googleapis.com/ingress": "all",
                "run.googleapis.com/invoker-iam-disabled": "true",
                "run.googleapis.com/urls": json.dumps([url, "https://hash.run.app"]),
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "name": revision_name,
                    "annotations": {
                        "run.googleapis.com/secrets": ",".join(
                            f"{alias}:{resource}" for alias, resource in secret_aliases.items()
                        )
                    },
                },
                "spec": {
                    "serviceAccountName": identity,
                    "containers": [container],
                    "volumes": [
                        {
                            "name": name,
                            "secret": {
                                "secretName": f"sm-{index}",
                                "items": [{"key": "1", "path": path, "mode": 292}],
                            },
                        }
                        for index, (name, _suffix, _secret, path, _mount_path) in enumerate(
                            volume_values, start=1
                        )
                    ],
                },
            }
        },
        "status": {
            "observedGeneration": 3,
            "latestCreatedRevisionName": revision_name,
            "latestReadyRevisionName": revision_name,
            "conditions": [{"type": "Ready", "status": "True"}],
            "traffic": [
                {
                    "latestRevision": True,
                    "percent": 100,
                    "revisionName": revision_name,
                }
            ],
        },
    }


def _bucket(source: GCPControlPlaneInput) -> dict[str, object]:
    return {
        "name": source.graph_bucket,
        "location": source.region.upper(),
        "iamConfiguration": {
            "uniformBucketLevelAccess": {"enabled": True},
            "publicAccessPrevention": "enforced",
        },
        "versioning": {"enabled": True},
        "softDeletePolicy": {"retentionDurationSeconds": "0"},
    }
