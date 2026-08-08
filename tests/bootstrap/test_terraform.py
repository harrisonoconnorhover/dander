"""Terraform bootstrap tests for DANDER-20."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from dander.bootstrap import TerraformBootstrap, TerraformBootstrapError

if TYPE_CHECKING:
    from pathlib import Path


def _pipelines(
    *, paused: bool = True, publish_dataplex: bool = False
) -> dict[str, dict[str, object]]:
    return {
        "greenhouse_jobs": {
            "job_name": "dander-greenhouse-public",
            "runtime_service_account_id": "dander-runtime",
            "scheduler_service_account_id": "dander-scheduler",
            "source": "greenhouse_job_board",
            "models": ["stg_greenhouse__jobs"],
            "build_models": True,
            "publish_dataplex": publish_dataplex,
            "schedule": "0 9 * * *",
            "time_zone": "America/New_York",
            "paused": paused,
            "secret_env": {},
        }
    }


def test_bootstrap_plans_without_applying(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == tmp_path.resolve()
        assert check
        commands.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    plan = TerraformBootstrap(tmp_path).execute(
        project="unit-project",
        state_bucket="unit-state",
        state_prefix="dander/state",
        bootstrap_service_account="dander-bootstrap@unit-project.iam.gserviceaccount.com",
        apply=False,
    )

    assert plan == tmp_path.resolve() / "dander-bootstrap.tfplan"
    assert commands[0][:2] == ("terraform", "init")
    assert commands[1][:2] == ("terraform", "plan")
    assert all(command[:2] != ("terraform", "apply") for command in commands)


def test_bootstrap_passes_complete_runtime_as_literal_arguments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    digest = "a" * 64

    TerraformBootstrap(tmp_path).execute(
        project="unit-project",
        state_bucket="unit-state",
        state_prefix="dander/state",
        bootstrap_service_account="dander-bootstrap@unit-project.iam.gserviceaccount.com",
        apply=True,
        region="us-east1",
        bigquery_location="US",
        runtime_cpu=2,
        runtime_memory="1Gi",
        runtime_timeout_seconds=900,
        runtime_max_retries=3,
        runtime_batch_rows=2048,
        require_guarded_free_tier=False,
        enable_runtime=True,
        container_image=f"us-east1-docker.pkg.dev/unit-project/dander/dander@sha256:{digest}",
        druff_container_image=(
            f"us-east1-docker.pkg.dev/unit-project/dander/druff@sha256:{'b' * 64}"
        ),
        pipelines=_pipelines(paused=False, publish_dataplex=True),
        failure_alert_email="operator@example.invalid",
        secret_ids=("greenhouse-client-secret", "greenhouse-client-id"),
        github_repository="WagnerJ-Dev/dander",
        github_ref="refs/heads/main",
    )

    plan = commands[1]
    assert (
        "-var=bootstrap_service_account=dander-bootstrap@unit-project.iam.gserviceaccount.com"
        in plan
    )
    assert "-var=enable_scheduled_job=true" in plan
    assert "-var=region=us-east1" in plan
    assert "-var=bigquery_location=US" in plan
    assert "-var=runtime_cpu=2" in plan
    assert "-var=runtime_memory=1Gi" in plan
    assert "-var=runtime_timeout_seconds=900" in plan
    assert "-var=runtime_max_retries=3" in plan
    assert "-var=runtime_batch_rows=2048" in plan
    assert (
        f"-var=druff_container_image=us-east1-docker.pkg.dev/unit-project/dander/druff"
        f"@sha256:{'b' * 64}"
    ) in plan
    assert "-var=require_guarded_free_tier=false" in plan
    assert "-var=billing_account_id=" in plan
    pipeline_argument = next(
        argument for argument in plan if argument.startswith("-var=pipelines=")
    )
    assert '"greenhouse_jobs"' in pipeline_argument
    assert '"paused":false' in pipeline_argument
    assert '"publish_dataplex":true' in pipeline_argument
    projection_argument = next(
        argument for argument in plan if argument.startswith("-var=execution_projections=")
    )
    projections = json.loads(projection_argument.removeprefix("-var=execution_projections="))
    projection = projections["greenhouse_jobs"]
    assert projection["image"].endswith("@sha256:" + digest)
    assert projection["command"][:8] == [
        "runtime",
        "execute",
        "--contract",
        "io.dander.runtime/v1",
        "--pipeline",
        "greenhouse_jobs",
        "--platform",
        "gcp",
    ]
    assert projection["resources"] == {
        "cpu_millis": 2000,
        "deadline_seconds": 900,
        "ephemeral_storage_mib": None,
        "launcher_retry_count": 3,
        "memory_mib": 1024,
        "runtime_retry_count": 0,
    }
    assert projection["schedule"]["paused"] is False
    assert projection["environment"]["DANDER_LAUNCHER"] == "cloud_run"
    assert "-var=failure_alert_email=operator@example.invalid" in plan
    assert '-var=secret_ids=["greenhouse-client-id","greenhouse-client-secret"]' in plan
    assert "-var=github_repository=WagnerJ-Dev/dander" in plan
    assert "-var=enable_cost_guard=false" in plan
    assert commands[2] == ("terraform", "apply", "dander-bootstrap.tfplan")


def test_bootstrap_accepts_empty_models_when_legacy_build_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    pipelines = _pipelines()
    pipelines["greenhouse_jobs"]["models"] = []
    pipelines["greenhouse_jobs"]["build_models"] = False

    TerraformBootstrap(tmp_path).execute(
        project="unit-project",
        state_bucket="unit-state",
        state_prefix="dander/state",
        bootstrap_service_account="dander-bootstrap@unit-project.iam.gserviceaccount.com",
        apply=False,
        require_guarded_free_tier=False,
        enable_runtime=True,
        container_image=f"example.invalid/dander@sha256:{'a' * 64}",
        pipelines=pipelines,
    )

    assert any(argument.startswith("-var=pipelines=") for argument in commands[1])


def test_bootstrap_rejects_an_unknown_launcher_before_terraform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess((), 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TerraformBootstrapError, match="Unknown launcher provider"):
        TerraformBootstrap(tmp_path).execute(
            project="unit-project",
            state_bucket="unit-state",
            state_prefix="dander/state",
            bootstrap_service_account=("dander-bootstrap@unit-project.iam.gserviceaccount.com"),
            apply=False,
            launcher_provider="missing",
            require_guarded_free_tier=False,
            enable_runtime=True,
            container_image=f"example.invalid/dander@sha256:{'a' * 64}",
            pipelines=_pipelines(),
        )

    assert calls == 0


def test_bootstrap_passes_simulation_first_cost_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    TerraformBootstrap(tmp_path).execute(
        project="unit-project",
        state_bucket="unit-state",
        state_prefix="dander/state",
        bootstrap_service_account="dander-bootstrap@unit-project.iam.gserviceaccount.com",
        apply=False,
        billing_account_id="ABCDEF-123456-ABCDEF",
        enable_cost_guard=True,
        cost_guard_budget_amount="4.50",
    )

    plan = commands[1]
    assert "-var=enable_cost_guard=true" in plan
    assert "-var=cost_guard_budget_amount=4.50" in plan
    assert "-var=cost_guard_simulate=true" in plan
    assert "-var=cost_guard_source_bucket=unit-state" in plan


def test_apply_saved_plan_does_not_replan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "dander-bootstrap.tfplan"
    plan_path.write_bytes(b"saved-plan")
    commands: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        assert cwd == tmp_path.resolve()
        assert check
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = TerraformBootstrap(tmp_path).apply_saved_plan(
        project="unit-project",
        state_bucket="unit-state",
        state_prefix="dander/state",
        bootstrap_service_account="dander-bootstrap@unit-project.iam.gserviceaccount.com",
    )

    assert result == plan_path
    assert commands[0][:2] == ("terraform", "init")
    assert commands[1] == ("terraform", "apply", "-input=false", "dander-bootstrap.tfplan")
    assert all(command[:2] != ("terraform", "plan") for command in commands)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"bootstrap_service_account": ""}, "bootstrap-service-account"),
        ({"enable_runtime": True}, "billing-account"),
        (
            {
                "enable_runtime": True,
                "billing_account_id": "ABCDEF-123456-ABCDEF",
                "container_image": "example.invalid/dander:latest",
            },
            "immutable",
        ),
        ({"billing_account_id": "ABCDEF-123456-ABCDEF"}, "enable-runtime"),
        ({"secret_ids": ("bad secret",)}, "secret id"),
        ({"github_repository": "not-a-repository"}, "GitHub repository"),
        ({"github_repository": "WagnerJ-Dev/dander"}, "enable-runtime"),
        ({"failure_alert_email": "operator@example.invalid"}, "enable-runtime"),
        ({"failure_alert_email": "not-an-email"}, "failure-alert"),
        ({"pipelines": _pipelines()}, "enable-runtime"),
        ({"github_ref": "main"}, "GitHub ref"),
        ({"enable_cost_guard": True}, "billing-account"),
        ({"live_cost_guard": True}, "enable-cost-guard"),
        ({"cost_guard_budget_amount": "5.01"}, "no greater than"),
        ({"cost_guard_budget_amount": "NaN"}, "no greater than"),
        ({"cost_guard_budget_name": "bad\nname"}, "display-name"),
        ({"runtime_cpu": 3}, "runtime_cpu"),
        ({"runtime_memory": "512MB"}, "runtime_memory"),
        ({"runtime_timeout_seconds": 0}, "runtime_timeout_seconds"),
        ({"runtime_max_retries": 11}, "runtime_max_retries"),
        ({"runtime_batch_rows": 100_001}, "runtime_batch_rows"),
        ({"druff_container_image": "example.invalid/druff:latest"}, "immutable"),
        (
            {
                "druff_container_image": f"example.invalid/druff@sha256:{'b' * 64}",
            },
            "enable-runtime",
        ),
        (
            {
                "enable_runtime": True,
                "billing_account_id": "ABCDEF-123456-ABCDEF",
                "container_image": f"example.invalid/dander@sha256:{'a' * 64}",
                "pipelines": _pipelines(),
                "require_guarded_free_tier": True,
            },
            "require_guarded_free_tier.*enable-cost-guard",
        ),
    ],
)
def test_bootstrap_rejects_unsafe_optional_inputs(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "project": "unit-project",
        "state_bucket": "unit-state",
        "state_prefix": "dander/state",
        "bootstrap_service_account": "dander-bootstrap@unit-project.iam.gserviceaccount.com",
        "apply": False,
        **overrides,
    }

    with pytest.raises(TerraformBootstrapError, match=message):
        TerraformBootstrap(tmp_path).execute(**arguments)  # type: ignore[arg-type]
