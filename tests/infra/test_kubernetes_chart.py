"""Packaged Helm chart safety and deterministic-value coverage."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from dander.providers.kubernetes.operations import (
    KubernetesOperationError,
    KubernetesOperations,
)

ROOT = Path(__file__).parents[2]
IMAGE = "ghcr.io/example/dander@sha256:" + "b" * 64

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Runner:
    def __init__(self, responses: dict[tuple[str, ...], list[str]] | None = None) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.responses = responses or {}

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output, text
        command = tuple(args)
        self.commands.append(command)
        for prefix, responses in self.responses.items():
            if command[: len(prefix)] == prefix:
                return subprocess.CompletedProcess(command, 0, stdout=responses.pop(0), stderr="")
        if command[:2] == ("helm", "lint"):
            return subprocess.CompletedProcess(command, 0, stdout="lint ok", stderr="")
        if command[:2] == ("helm", "template"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="apiVersion: v1\nkind: ConfigMap\n",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {command}")


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "dander.yaml"
    platforms = tmp_path / "dander.platforms.yaml"
    project.write_text(
        """
version: 2
pipelines:
  records:
    source: records
    models: []
    build_models: false
""".lstrip(),
        encoding="utf-8",
    )
    platforms.write_text(
        """
version: 1
platforms:
  postgres:
    warehouse:
      provider: postgresql
      database: dander
    state:
      provider: postgresql
      authority_id: kubernetes:test
    catalog:
      provider: none
    secrets:
      provider: environment
deployments:
  local_cluster:
    platform: postgres
    launcher:
      provider: kubernetes
      context: kind-dander
      namespace: dander-test
      release_name: dander
      service_account_name: dander-runtime
      existing_secret_name: dander-runtime
    runtime:
      cpu: 1
      memory: 512Mi
      timeout_seconds: 300
      max_retries: 1
      batch_rows: 2
    safety:
      require_guarded_free_tier: false
    pipelines:
      records:
        schedule: "0 9 * * *"
        time_zone: America/New_York
        paused: true
        secret_bindings:
          DANDER_POSTGRES_DSN: postgres-dsn
""".lstrip(),
        encoding="utf-8",
    )
    connectors = tmp_path / "connectors"
    connectors.mkdir()
    (connectors / "records.yaml").write_text(
        """
name: records
base_url: https://example.invalid
auth_strategy: none
endpoints:
  - name: records
    path: /records
    primary_key: [id]
    raw_schema:
      - {name: id, type: STRING, mode: REQUIRED}
""".lstrip(),
        encoding="utf-8",
    )
    shutil.copytree(
        ROOT / "infra" / "kubernetes",
        tmp_path / "infra" / "kubernetes",
    )
    return project, platforms


def _cluster_responses(cronjobs: list[dict[str, object]]) -> list[str]:
    return [
        json.dumps(
            {
                "metadata": {
                    "name": "dander-runtime",
                    "annotations": {},
                },
                "automountServiceAccountToken": False,
            }
        ),
        json.dumps(
            {
                "metadata": {"name": "dander-config"},
                "data": {
                    "DANDER_DEPLOYMENT": "local_cluster",
                    "DANDER_PLATFORM_PROFILE": "postgres",
                    "DANDER_IMAGE_REFERENCE": IMAGE,
                },
            }
        ),
        json.dumps({"metadata": {"name": "dander-runtime"}}),
        json.dumps({"items": cronjobs}),
    ]


def test_plan_saves_non_secret_values_and_rendered_manifests(tmp_path: Path) -> None:
    project, platforms = _project(tmp_path)
    runner = _Runner()
    plan = KubernetesOperations(runner=runner).plan(
        config=project,
        platforms_config=platforms,
        deployment="local_cluster",
        image=IMAGE,
        output_dir=tmp_path / "plans",
    )

    values = yaml.safe_load(plan.values.read_text(encoding="utf-8"))
    pipeline = values["pipelines"]["records"]
    assert values["image"]["reference"] == IMAGE
    assert values["existingSecret"]["name"] == "dander-runtime"
    assert pipeline["secretEnvironment"] == [{"name": "DANDER_POSTGRES_DSN", "key": "postgres-dsn"}]
    assert pipeline["suspend"] is True
    assert pipeline["backoffLimit"] == 1
    assert pipeline["activeDeadlineSeconds"] == 300
    assert pipeline["resources"]["requests"] == pipeline["resources"]["limits"]
    assert plan.manifests.read_text(encoding="utf-8").startswith("apiVersion")
    assert plan.values.stat().st_mode & 0o777 == 0o600
    assert "postgresql://" not in plan.values.read_text(encoding="utf-8")
    assert [command[:2] for command in runner.commands] == [
        ("helm", "lint"),
        ("helm", "template"),
    ]


def test_verify_is_read_only_and_checks_schedule_pause_image_and_overlap(tmp_path: Path) -> None:
    project, platforms = _project(tmp_path)
    cronjob = {
        "metadata": {"name": "dander-records", "labels": {"dander.io/pipeline": "records"}},
        "spec": {
            "schedule": "0 9 * * *",
            "timeZone": "America/New_York",
            "suspend": True,
            "concurrencyPolicy": "Forbid",
            "successfulJobsHistoryLimit": 3,
            "failedJobsHistoryLimit": 3,
            "jobTemplate": {
                "spec": {
                    "backoffLimit": 1,
                    "activeDeadlineSeconds": 300,
                    "ttlSecondsAfterFinished": 3_600,
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "serviceAccountName": "dander-runtime",
                            "automountServiceAccountToken": False,
                            "terminationGracePeriodSeconds": 120,
                            "containers": [
                                {
                                    "name": "runtime",
                                    "image": IMAGE,
                                    "args": [
                                        "runtime",
                                        "execute",
                                        "--contract",
                                        "io.dander.runtime/v1",
                                        "--pipeline",
                                        "records",
                                        "--platform",
                                        "local_cluster",
                                        "--config",
                                        "/app/dander.yaml",
                                        "--models-dir",
                                        "/app/models",
                                        "--batch-rows",
                                        "2",
                                    ],
                                    "env": [
                                        {
                                            "name": "DANDER_IMAGE_DIGEST",
                                            "value": "sha256:" + "b" * 64,
                                        },
                                        {"name": "DANDER_LAUNCHER", "value": "kubernetes"},
                                        {
                                            "name": "DANDER_PRINCIPAL",
                                            "value": (
                                                "kubernetes://dander-test/"
                                                "serviceaccounts/dander-runtime"
                                            ),
                                        },
                                        {
                                            "name": "DANDER_POSTGRES_DSN",
                                            "valueFrom": {
                                                "secretKeyRef": {
                                                    "name": "dander-runtime",
                                                    "key": "postgres-dsn",
                                                }
                                            },
                                        },
                                    ],
                                    "resources": {
                                        "requests": {
                                            "cpu": "1",
                                            "memory": "512Mi",
                                            "ephemeral-storage": "1Gi",
                                        },
                                        "limits": {
                                            "cpu": "1",
                                            "memory": "512Mi",
                                            "ephemeral-storage": "1Gi",
                                        },
                                    },
                                }
                            ],
                        }
                    },
                }
            },
        },
    }
    runner = _Runner({("kubectl", "--context", "kind-dander"): _cluster_responses([cronjob])})

    result = KubernetesOperations(runner=runner).verify(
        config=project,
        platforms_config=platforms,
        deployment="local_cluster",
        expected_image=IMAGE,
    )

    assert result.pipelines == ("records",)
    assert result.image == IMAGE
    assert all(command[-2:] == ("-o", "json") for command in runner.commands)
    assert not any(
        command[-2:] in {("create", "job"), ("delete", "job")} for command in runner.commands
    )


def test_verify_fails_closed_on_drift(tmp_path: Path) -> None:
    project, platforms = _project(tmp_path)
    runner = _Runner({("kubectl", "--context", "kind-dander"): _cluster_responses([])})

    with pytest.raises(KubernetesOperationError, match="do not match"):
        KubernetesOperations(runner=runner).verify(
            config=project,
            platforms_config=platforms,
            deployment="local_cluster",
            expected_image=IMAGE,
        )


def test_chart_encodes_job_safety_and_no_secret_ownership() -> None:
    chart = ROOT / "infra" / "kubernetes" / "chart" / "dander"
    cronjobs = (chart / "templates" / "cronjobs.yaml").read_text(encoding="utf-8")
    service_account = (chart / "templates" / "serviceaccount.yaml").read_text(encoding="utf-8")

    assert "concurrencyPolicy: Forbid" in cronjobs
    assert "restartPolicy: Never" in cronjobs
    assert "activeDeadlineSeconds" in cronjobs
    assert "ttlSecondsAfterFinished" in cronjobs
    assert "secretKeyRef:" in cronjobs
    assert "readOnlyRootFilesystem: true" in cronjobs
    assert 'drop: ["ALL"]' in cronjobs
    assert "sizeLimit:" in cronjobs
    assert "automountServiceAccountToken: {{ $.Values.rbac.create }}" in cronjobs
    assert "automountServiceAccountToken: {{ .Values.rbac.create }}" in service_account
    assert not list((chart / "templates").glob("*secret*"))
