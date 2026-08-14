"""Existing-cluster Kubernetes hosted Control projection and verifier tests."""

from __future__ import annotations

import json
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
import yaml
from pydantic import ValidationError

import dander.deployment.kubernetes_control_plane as kubernetes_control_plane
from dander.control.auth import HostedOIDCDeploymentInput
from dander.deployment.kubernetes_control_plane import (
    KUBERNETES_CONTROL_PLANE_SCHEMA,
    KubernetesControlPlaneError,
    KubernetesControlPlaneInput,
    preflight_kubernetes_control_plane,
    project_kubernetes_control_service,
    render_kubernetes_control_plane,
    verify_live_kubernetes_control_plane,
    write_kubernetes_control_plane,
)

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "infra" / "kubernetes" / "chart" / "dander-control"
ORIGIN = "https://dander.127.0.0.1.sslip.io:8443"
_DANDER = "127.0.0.1:5001/dander@sha256:" + "a" * 64
_DANDER_ROLLBACK = "127.0.0.1:5001/dander@sha256:" + "b" * 64
_DRUFF = "127.0.0.1:5001/druff@sha256:" + "c" * 64
_DRUFF_ROLLBACK = "127.0.0.1:5001/druff@sha256:" + "d" * 64


def _source(**updates: object) -> KubernetesControlPlaneInput:
    values: dict[str, object] = {
        "dander_image": _DANDER,
        "dander_rollback_image": _DANDER_ROLLBACK,
        "druff_image": _DRUFF,
        "druff_rollback_image": _DRUFF_ROLLBACK,
        "context": "kind-dander-control",
        "namespace": "dander-control",
        "release_name": "dander-control",
        "service_account_name": "dander-control",
        "service_account_annotations": (("example.test/identity", "dander-control"),),
        "existing_tls_secret_name": "dander-control-tls",
        "oidc": HostedOIDCDeploymentInput(
            api_url=ORIGIN,
            issuer="https://issuer.127.0.0.1.sslip.io:8443/default",
            jwks_uri=("https://issuer.127.0.0.1.sslip.io:8443/default/.well-known/jwks.json"),
            public_client_id="druff-kubernetes-spa",
            api_audience="dander-kubernetes-control",
            redirect_uri=f"{ORIGIN}/auth/callback",
            logout_uri=f"{ORIGIN}/signed-out",
            allowed_origins=(ORIGIN,),
        ),
    }
    values.update(updates)
    return KubernetesControlPlaneInput.model_validate(values)


def test_input_is_closed_immutable_digest_only_and_exact_origin() -> None:
    source = _source()
    with pytest.raises(ValidationError, match="frozen"):
        source.namespace = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="immutable sha256"):
        _source(dander_image="ghcr.io/example/dander:latest")
    with pytest.raises(ValidationError, match="same repository"):
        _source(
            dander_rollback_image="ghcr.io/other/dander@sha256:" + "b" * 64,
        )
    with pytest.raises(ValidationError, match="exact Ingress origin"):
        _source(
            oidc=HostedOIDCDeploymentInput(
                **{
                    **source.oidc.model_dump(),
                    "redirect_uri": f"{ORIGIN}/wrong-callback",
                }
            )
        )
    with pytest.raises(ValidationError, match="HTTPS origin"):
        _source(
            oidc=HostedOIDCDeploymentInput(
                **{
                    **source.oidc.model_dump(),
                    "api_url": f"{ORIGIN}/path",
                    "redirect_uri": f"{ORIGIN}/path/auth/callback",
                    "logout_uri": f"{ORIGIN}/path/signed-out",
                    "allowed_origins": (f"{ORIGIN}/path",),
                }
            )
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        KubernetesControlPlaneInput.model_validate({**source.model_dump(), "extensions": {}})


def test_projection_reuses_d6_contract_and_renders_equal_closed_files() -> None:
    source = _source()
    service = project_kubernetes_control_service(source)
    rendered = render_kubernetes_control_plane(source)

    assert rendered == render_kubernetes_control_plane(source)
    assert set(rendered) == {
        "active-values.yaml",
        "bootstrap.json",
        "control-graph-store.json",
        "control-oidc.json",
        "deployment.json",
        "public-client.json",
        "rollback-values.yaml",
    }
    assert service.workload_identity == (
        "kubernetes://dander-control/serviceaccounts/dander-control"
    )
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
    assert json.loads(rendered["control-graph-store.json"]) == {
        "kind": "local",
        "root": "/var/lib/dander/control",
    }
    deployment = json.loads(rendered["deployment.json"])
    assert deployment["schema"] == KUBERNETES_CONTROL_PLANE_SCHEMA
    assert deployment["ingress_origin"] == ORIGIN
    assert deployment["ingress_class"] == "nginx"
    active = yaml.safe_load(rendered["active-values.yaml"])
    rollback = yaml.safe_load(rendered["rollback-values.yaml"])
    fixture = yaml.safe_load(
        (ROOT / "tests" / "fixtures" / "kubernetes-control-values.yaml").read_text()
    )
    assert fixture == active
    assert active["control"]["image"] == _DANDER
    assert rollback["control"]["image"] == _DANDER_ROLLBACK
    assert active["druff"]["image"] == _DRUFF
    assert rollback["druff"]["image"] == _DRUFF_ROLLBACK
    assert active["ingress"] == {
        "className": "nginx",
        "host": "dander.127.0.0.1.sslip.io",
        "tlsSecretName": "dander-control-tls",
        "accessLogEnabled": False,
    }
    combined = "".join(rendered.values()).casefold()
    assert "client_secret" not in combined
    assert "password" not in combined
    assert "credential" not in combined


def test_rollout_digests_track_only_consumed_config_and_identity() -> None:
    original = yaml.safe_load(render_kubernetes_control_plane(_source())["active-values.yaml"])
    identity_changed = yaml.safe_load(
        render_kubernetes_control_plane(
            _source(service_account_annotations=(("example.test/identity", "changed"),))
        )["active-values.yaml"]
    )
    assert identity_changed["control"]["rolloutDigest"] != (original["control"]["rolloutDigest"])
    assert identity_changed["druff"]["rolloutDigest"] == original["druff"]["rolloutDigest"]

    source = _source()
    changed_oidc = HostedOIDCDeploymentInput(
        **{
            **source.oidc.model_dump(),
            "api_audience": "dander-kubernetes-control-v2",
        }
    )
    trust_changed = yaml.safe_load(
        render_kubernetes_control_plane(_source(oidc=changed_oidc))["active-values.yaml"]
    )
    assert trust_changed["control"]["rolloutDigest"] != original["control"]["rolloutDigest"]
    assert trust_changed["druff"]["rolloutDigest"] != original["druff"]["rolloutDigest"]


def test_write_preflight_and_chart_render_are_deterministic(tmp_path: Path) -> None:
    source = _source()
    output = tmp_path / "kubernetes"
    written = write_kubernetes_control_plane(source, output_directory=output)

    assert {path.name for path in written} == set(render_kubernetes_control_plane(source))
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o444 for path in written)
    result = preflight_kubernetes_control_plane(
        source,
        output_directory=output,
        chart=CHART,
    )
    assert result["status"] == "passed"
    checks = result["checks"]
    assert isinstance(checks, list)
    assert checks[-2:] == ["helm-lint", "repeated-render-equal"]
    assert (output / "active-manifests.yaml").is_file()
    manifests = list(yaml.safe_load_all((output / "active-manifests.yaml").read_text()))
    kinds = [manifest["kind"] for manifest in manifests if manifest]
    assert kinds.count("Deployment") == 2
    assert kinds.count("Service") == 2
    assert "Ingress" in kinds
    assert "PersistentVolumeClaim" in kinds

    ingress = next(manifest for manifest in manifests if manifest and manifest["kind"] == "Ingress")
    assert (
        ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/enable-access-log"]
        == "false"
    )
    assert ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/proxy-body-size"] == "6m"
    control = next(
        manifest
        for manifest in manifests
        if manifest
        and manifest["kind"] == "Deployment"
        and manifest["metadata"]["name"] == "dander-control-control"
    )
    assert control["spec"]["strategy"]["type"] == "Recreate"
    assert control["spec"]["template"]["metadata"]["annotations"] == {
        "dander.io/config-sha256": yaml.safe_load((output / "active-values.yaml").read_text())[
            "control"
        ]["rolloutDigest"],
        "dander.io/identity-sha256": yaml.safe_load((output / "active-values.yaml").read_text())[
            "serviceAccount"
        ]["identityDigest"],
    }


def test_preflight_rejects_stale_projection(tmp_path: Path) -> None:
    source = _source()
    output = tmp_path / "kubernetes"
    write_kubernetes_control_plane(source, output_directory=output)
    path = output / "bootstrap.json"
    path.chmod(0o600)
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(KubernetesControlPlaneError, match="missing or stale"):
        preflight_kubernetes_control_plane(
            source,
            output_directory=output,
            chart=CHART,
        )


@pytest.mark.parametrize("environment", ["active", "rollback"])
def test_live_verifier_is_read_only_and_checks_exact_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: Literal["active", "rollback"],
) -> None:
    source = _source()
    output = tmp_path / "kubernetes"
    write_kubernetes_control_plane(source, output_directory=output)
    certificate = tmp_path / "tls.crt"
    certificate.write_text("test", encoding="utf-8")
    resources = _live_resources(source, environment)
    commands: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...]) -> SimpleNamespace:
        commands.append(command)
        kind, name = command[-4], command[-3]
        return SimpleNamespace(stdout=json.dumps(resources[(kind, name)]))

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

    monkeypatch.setattr(kubernetes_control_plane, "_run", fake_run)
    monkeypatch.setattr(kubernetes_control_plane, "_https_get", fake_https)
    monkeypatch.setattr(
        ssl,
        "create_default_context",
        lambda **_kwargs: object(),
    )

    result = verify_live_kubernetes_control_plane(
        source,
        output_directory=output,
        certificate=certificate,
        environment=environment,
    )
    assert result["status"] == "passed"
    assert result["environment"] == environment
    assert commands
    assert all(command[-2:] == ("-o", "json") for command in commands)
    assert all(command[5] == "get" for command in commands)


def test_live_verifier_rejects_ingress_access_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source()
    output = tmp_path / "kubernetes"
    write_kubernetes_control_plane(source, output_directory=output)
    certificate = tmp_path / "tls.crt"
    certificate.write_text("test", encoding="utf-8")
    resources = _live_resources(source, "active")
    resources[("ingress", "dander-control")]["metadata"]["annotations"][
        "nginx.ingress.kubernetes.io/enable-access-log"
    ] = "true"

    def fake_run(command: tuple[str, ...]) -> SimpleNamespace:
        return SimpleNamespace(stdout=json.dumps(resources[(command[-4], command[-3])]))

    monkeypatch.setattr(kubernetes_control_plane, "_run", fake_run)
    with pytest.raises(KubernetesControlPlaneError, match="access logging"):
        verify_live_kubernetes_control_plane(
            source,
            output_directory=output,
            certificate=certificate,
        )


def test_live_verifier_rejects_small_ingress_graph_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source()
    output = tmp_path / "kubernetes"
    write_kubernetes_control_plane(source, output_directory=output)
    certificate = tmp_path / "tls.crt"
    certificate.write_text("test", encoding="utf-8")
    resources = _live_resources(source, "active")
    resources[("ingress", "dander-control")]["metadata"]["annotations"][
        "nginx.ingress.kubernetes.io/proxy-body-size"
    ] = "1m"

    def fake_run(command: tuple[str, ...]) -> SimpleNamespace:
        return SimpleNamespace(stdout=json.dumps(resources[(command[-4], command[-3])]))

    monkeypatch.setattr(kubernetes_control_plane, "_run", fake_run)
    with pytest.raises(KubernetesControlPlaneError, match="graph request limit"):
        verify_live_kubernetes_control_plane(
            source,
            output_directory=output,
            certificate=certificate,
        )


def _live_resources(
    source: KubernetesControlPlaneInput, environment: Literal["active", "rollback"]
) -> dict[tuple[str, str], dict[str, Any]]:
    rendered = render_kubernetes_control_plane(source)
    values = yaml.safe_load(rendered[f"{environment}-values.yaml"])
    control_image = source.dander_image if environment == "active" else source.dander_rollback_image
    druff_image = source.druff_image if environment == "active" else source.druff_rollback_image
    resources: dict[tuple[str, str], dict[str, Any]] = {
        ("serviceaccount", "dander-control"): {
            "metadata": {"annotations": dict(source.service_account_annotations)},
            "automountServiceAccountToken": False,
        },
        ("configmap", "dander-control-config"): {
            "data": {
                "bootstrap.json": rendered["bootstrap.json"].rstrip("\n"),
                "control-graph-store.json": rendered["control-graph-store.json"].rstrip("\n"),
                "control-oidc.json": rendered["control-oidc.json"].rstrip("\n"),
            }
        },
        ("deployment", "dander-control-control"): _deployment(
            source,
            workload="control",
            image=control_image,
            values=values,
        ),
        ("deployment", "dander-control-druff"): _deployment(
            source,
            workload="druff",
            image=druff_image,
            values=values,
        ),
        ("service", "dander-control-control"): {
            "spec": {"type": "ClusterIP", "ports": [{"port": 8770}]}
        },
        ("service", "dander-control-druff"): {
            "spec": {"type": "ClusterIP", "ports": [{"port": 8080}]}
        },
        ("persistentvolumeclaim", "dander-control-graph-store"): {"status": {"phase": "Bound"}},
        ("ingress", "dander-control"): {
            "metadata": {
                "annotations": {
                    "nginx.ingress.kubernetes.io/enable-access-log": "false",
                    "nginx.ingress.kubernetes.io/proxy-body-size": "6m",
                    "nginx.ingress.kubernetes.io/ssl-redirect": "true",
                }
            },
            "spec": {
                "ingressClassName": "nginx",
                "tls": [
                    {
                        "secretName": "dander-control-tls",
                        "hosts": ["dander.127.0.0.1.sslip.io"],
                    }
                ],
                "rules": [
                    {
                        "host": "dander.127.0.0.1.sslip.io",
                        "http": {
                            "paths": [
                                _path("/v1", "dander-control-control"),
                                _path("/healthz", "dander-control-control"),
                                _path("/readyz", "dander-control-control"),
                                _path("/", "dander-control-druff"),
                            ]
                        },
                    }
                ],
            },
        },
    }
    return resources


def _deployment(
    source: KubernetesControlPlaneInput,
    *,
    workload: Literal["control", "druff"],
    image: str,
    values: dict[str, Any],
) -> dict[str, object]:
    container = {
        "image": image,
        "securityContext": {
            "runAsUser": 65532,
            "runAsGroup": 65532,
            "runAsNonRoot": True,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }
    spec: dict[str, object] = {
        "automountServiceAccountToken": False,
        "serviceAccountName": (
            source.service_account_name if workload == "control" else "dander-control-druff"
        ),
        "containers": [container],
    }
    deployment_spec: dict[str, object] = {
        "replicas": 1,
        "template": {
            "metadata": {
                "annotations": {"dander.io/config-sha256": values[workload]["rolloutDigest"]}
            },
            "spec": spec,
        },
    }
    if workload == "control":
        container["args"] = values["control"]["command"]
        deployment_spec["strategy"] = {"type": "Recreate"}
        spec["initContainers"] = [
            {
                "securityContext": {
                    "runAsUser": 0,
                    "runAsNonRoot": False,
                    "capabilities": {"drop": ["ALL"], "add": ["CHOWN", "FOWNER"]},
                }
            }
        ]
    return {"spec": deployment_spec, "status": {"readyReplicas": 1}}


def _path(path: str, service: str) -> dict[str, object]:
    return {"path": path, "backend": {"service": {"name": service}}}
