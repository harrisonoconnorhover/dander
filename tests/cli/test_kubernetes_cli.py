"""Kubernetes CLI keeps plans non-mutating and verification read-only."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from dander.cli.main import app
from dander.providers.kubernetes.operations import KubernetesPlan, KubernetesVerification

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def test_kubernetes_plan_prints_reviewed_helm_command(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    chart = tmp_path / "chart"
    values = tmp_path / "deployment.values.yaml"
    manifests = tmp_path / "deployment.manifests.yaml"

    def plan(_operations: object, **_: object) -> KubernetesPlan:
        return KubernetesPlan(
            chart=chart,
            values=values,
            manifests=manifests,
            release_name="dander",
            namespace="dander-test",
            context="kind-dander",
        )

    monkeypatch.setattr("dander.cli.kubernetes_command.KubernetesOperations.plan", plan)
    result = CliRunner().invoke(
        app,
        [
            "kubernetes",
            "plan",
            "--deployment",
            "local_cluster",
            "--container-image",
            "ghcr.io/example/dander@sha256:" + "a" * 64,
        ],
    )

    assert result.exit_code == 0, result.output
    assert manifests.name in result.output
    assert "helm --kube-context kind-dander upgrade --install dander" in " ".join(
        result.output.split()
    )
    assert "--namespace dander-test" in " ".join(result.output.split())


def test_kubernetes_verify_prints_only_sanitized_result(
    monkeypatch: MonkeyPatch,
) -> None:
    def verify(_operations: object, **_: object) -> KubernetesVerification:
        return KubernetesVerification(
            context="kind-dander",
            namespace="dander-test",
            service_account="dander-runtime",
            config_map="dander-config",
            pipelines=("records",),
            image="ghcr.io/example/dander@sha256:" + "b" * 64,
        )

    monkeypatch.setattr("dander.cli.kubernetes_command.KubernetesOperations.verify", verify)
    result = CliRunner().invoke(
        app,
        [
            "kubernetes",
            "verify",
            "--deployment",
            "local_cluster",
            "--expected-image",
            "ghcr.io/example/dander@sha256:" + "b" * 64,
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"pipelines": [' in result.output
    assert '"records"' in result.output
    assert "secret" not in result.output.lower()
