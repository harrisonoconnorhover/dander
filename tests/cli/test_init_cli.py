"""CLI coverage for the complete optional bootstrap."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click import unstyle
from typer.testing import CliRunner

from dander.cli.main import app
from dander.project import scaffold_project

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_init_passes_optional_runtime_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_execute(self: object, **kwargs: object) -> Path:
        captured.update(kwargs)
        return tmp_path / "dander-bootstrap.tfplan"

    monkeypatch.setattr("dander.cli.main.TerraformBootstrap.execute", fake_execute)

    result = CliRunner().invoke(
        app,
        [
            "init",
            "--project",
            "unit-project",
            "--state-bucket",
            "unit-state",
            "--enable-runtime",
            "--billing-account",
            "ABCDEF-123456-ABCDEF",
            "--container-image",
            f"example.invalid/project/repository/image@sha256:{'a' * 64}",
            "--secret-id",
            "api-token",
            "--github-repository",
            "WagnerJ-Dev/dander",
            "--failure-alert-email",
            "operator@example.invalid",
            "--enable-cost-guard",
            "--cost-guard-budget-amount",
            "4.50",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["enable_runtime"] is True
    pipelines = captured["pipelines"]
    assert isinstance(pipelines, dict)
    assert set(pipelines) == {
        "greenhouse_jobs",
        "greenhouse_jobs_graph",
        "hubspot_companies",
        "salesforce_accounts",
        "servicenow_incidents",
    }
    assert pipelines["greenhouse_jobs"]["paused"] is False
    assert pipelines["greenhouse_jobs_graph"]["paused"] is True
    assert pipelines["greenhouse_jobs_graph"]["models"] == []
    assert pipelines["greenhouse_jobs_graph"]["build_models"] is False
    assert pipelines["hubspot_companies"]["paused"] is False
    assert pipelines["salesforce_accounts"]["paused"] is False
    assert pipelines["servicenow_incidents"]["paused"] is False
    assert captured["secret_ids"] == ("api-token",)
    assert captured["github_repository"] == "WagnerJ-Dev/dander"
    assert captured["failure_alert_email"] == "operator@example.invalid"
    assert captured["region"] == "us-central1"
    assert captured["bigquery_location"] == "US"
    assert captured["runtime_cpu"] == 1
    assert captured["runtime_memory"] == "512Mi"
    assert captured["runtime_timeout_seconds"] == 300
    assert captured["runtime_max_retries"] == 1
    assert captured["runtime_batch_rows"] == 10_000
    assert captured["require_guarded_free_tier"] is True
    assert captured["enable_cost_guard"] is True
    assert captured["cost_guard_budget_amount"] == "4.50"
    assert captured["live_cost_guard"] is False


def test_init_apply_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = False

    def fake_execute(self: object, **kwargs: object) -> Path:
        nonlocal called
        called = True
        return tmp_path / "must-not-execute.tfplan"

    monkeypatch.setattr("dander.cli.main.TerraformBootstrap.execute", fake_execute)

    result = CliRunner().invoke(
        app,
        [
            "init",
            "--project",
            "unit-project",
            "--state-bucket",
            "unit-state",
            "--apply",
        ],
        input="n\n",
    )

    assert result.exit_code != 0
    assert not called


def test_init_plan_without_container_image_reports_usage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute(self: object, **kwargs: object) -> Path:
        raise AssertionError("must not execute")

    monkeypatch.setattr("dander.cli.main.TerraformBootstrap.execute", fake_execute)

    result = CliRunner().invoke(
        app,
        [
            "init",
            "--project",
            "unit-project",
            "--billing-account",
            "ABCDEF-123456-ABCDEF",
        ],
    )

    output = unstyle(result.output)
    assert result.exit_code == 2
    assert "Invalid value for '--container-image'" in output
    assert "plan-only runtime initialization" in output
    assert "requires an immutable image" in output
    assert "Traceback" not in output


def test_live_cost_guard_is_named_in_apply_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute(self: object, **kwargs: object) -> Path:
        raise AssertionError("must not execute")

    monkeypatch.setattr("dander.cli.main.TerraformBootstrap.execute", fake_execute)

    result = CliRunner().invoke(
        app,
        [
            "init",
            "--project",
            "unit-project",
            "--state-bucket",
            "unit-state",
            "--enable-cost-guard",
            "--billing-account",
            "ABCDEF-123456-ABCDEF",
            "--live-cost-guard",
            "--apply",
        ],
        input="n\n",
    )

    assert "LIVE automatic billing detachment" in result.output


def test_init_apply_bootstraps_state_identity_image_and_platform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    captured: dict[str, object] = {}

    def fake_bucket(self: object, **kwargs: object) -> bool:
        events.append("state")
        assert kwargs["bucket"] == "unit-project-dander-state"
        return True

    def fake_admin(self: object, **kwargs: object) -> Path:
        events.append("admin")
        assert kwargs["adopt_state_bucket"] is True
        return tmp_path / "admin.tfplan"

    def fake_publish(self: object, **kwargs: object) -> str:
        events.append("image")
        return "us-central1-docker.pkg.dev/unit-project/dander/dander@sha256:" + "a" * 64

    def fake_wait(**kwargs: object) -> None:
        events.append("identity")
        assert kwargs["service_account"] == (
            "dander-bootstrap@unit-project.iam.gserviceaccount.com"
        )

    def fake_platform(self: object, **kwargs: object) -> Path:
        events.append("platform")
        captured.update(kwargs)
        return tmp_path / "platform.tfplan"

    monkeypatch.setattr("dander.cli.main.StateBucketBootstrap.ensure", fake_bucket)
    monkeypatch.setattr("dander.cli.main.AdministrativeBootstrap.execute", fake_admin)
    monkeypatch.setattr("dander.cli.main.RuntimeImagePublisher.publish", fake_publish)
    monkeypatch.setattr("dander.cli.main.wait_for_service_account_impersonation", fake_wait)
    monkeypatch.setattr(
        "dander.cli.main.active_admin_member",
        lambda **_kwargs: "user:operator@example.invalid",
    )
    monkeypatch.setattr("dander.cli.main.TerraformBootstrap.execute", fake_platform)

    result = CliRunner().invoke(
        app,
        [
            "init",
            "--project",
            "unit-project",
            "--billing-account",
            "ABCDEF-123456-ABCDEF",
            "--operator-artifact-dir",
            str(tmp_path / "operator"),
            "--apply",
        ],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert events == ["state", "admin", "identity", "image", "platform"]
    assert captured["enable_runtime"] is True
    assert captured["enable_cost_guard"] is True
    assert captured["bootstrap_service_account"] == (
        "dander-bootstrap@unit-project.iam.gserviceaccount.com"
    )


def test_init_uses_manifest_platform_and_only_explicit_cli_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_execute(self: object, **kwargs: object) -> Path:
        captured.append(kwargs)
        return tmp_path / "dander-bootstrap.tfplan"

    connector_dir = tmp_path / "connectors"
    models_dir = tmp_path / "models"
    connector_dir.mkdir()
    models_dir.mkdir()
    (connector_dir / "source.yaml").write_text(
        """
name: source
base_url: https://example.test
auth_strategy: none
endpoints:
  - name: records
    path: /records
    raw_schema:
      - name: id
        type: STRING
""".strip(),
        encoding="utf-8",
    )
    (models_dir / "model.sql").write_text("SELECT 1\n", encoding="utf-8")
    config = tmp_path / "dander.yaml"
    config.write_text(
        """
version: 1
platform:
  region: us-east1
  bigquery_location: EU
  runtime:
    cpu: 2
    memory: 1Gi
    timeout_seconds: 900
    max_retries: 3
    batch_rows: 2048
  safety:
    require_guarded_free_tier: false
pipelines:
  example:
    source: source
    models: [model]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("dander.cli.main.TerraformBootstrap.execute", fake_execute)
    base_args = [
        "init",
        "--project",
        "unit-project",
        "--container-image",
        f"example.invalid/project/repository/image@sha256:{'a' * 64}",
        "--config",
        str(config),
    ]

    authored = CliRunner().invoke(app, base_args)
    overridden = CliRunner().invoke(
        app,
        [
            *base_args,
            "--region",
            "europe-west1",
            "--bigquery-location",
            "europe-west1",
            "--runtime-cpu",
            "4",
            "--runtime-memory",
            "2Gi",
            "--runtime-timeout-seconds",
            "1200",
            "--runtime-max-retries",
            "5",
            "--runtime-batch-rows",
            "4096",
            "--require-guarded-free-tier",
            "--billing-account",
            "ABCDEF-123456-ABCDEF",
        ],
    )

    assert authored.exit_code == 0, authored.output
    assert overridden.exit_code == 0, overridden.output
    assert {
        key: captured[0][key]
        for key in (
            "region",
            "bigquery_location",
            "runtime_cpu",
            "runtime_memory",
            "runtime_timeout_seconds",
            "runtime_max_retries",
            "runtime_batch_rows",
            "require_guarded_free_tier",
        )
    } == {
        "region": "us-east1",
        "bigquery_location": "EU",
        "runtime_cpu": 2,
        "runtime_memory": "1Gi",
        "runtime_timeout_seconds": 900,
        "runtime_max_retries": 3,
        "runtime_batch_rows": 2048,
        "require_guarded_free_tier": False,
    }
    assert captured[1]["region"] == "europe-west1"
    assert captured[1]["bigquery_location"] == "europe-west1"
    assert captured[1]["runtime_cpu"] == 4
    assert captured[1]["runtime_memory"] == "2Gi"
    assert captured[1]["runtime_timeout_seconds"] == 1200
    assert captured[1]["runtime_max_retries"] == 5
    assert captured[1]["runtime_batch_rows"] == 4096
    assert captured[1]["require_guarded_free_tier"] is True
    assert captured[0]["enable_cost_guard"] is False
    assert captured[0]["billing_account_id"] == ""
    assert captured[1]["enable_cost_guard"] is True
    assert captured[1]["billing_account_id"] == "ABCDEF-123456-ABCDEF"
    warning = "Dander is not managing, limiting, or preventing cloud spending"
    assert unstyle(authored.output).count(warning) == 1
    assert warning not in unstyle(overridden.output)


def test_init_unguarded_apply_passes_empty_billing_account_to_stage_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_dir = scaffold_project(tmp_path / "generated")
    captured_admin: dict[str, object] = {}
    captured_platform: dict[str, object] = {}

    monkeypatch.setattr("dander.cli.main.StateBucketBootstrap.ensure", lambda self, **kwargs: True)

    def fake_admin(self: object, **kwargs: object) -> Path:
        captured_admin.update(kwargs)
        return tmp_path / "admin.tfplan"

    def fake_platform(self: object, **kwargs: object) -> Path:
        captured_platform.update(kwargs)
        return tmp_path / "platform.tfplan"

    monkeypatch.setattr("dander.cli.main.AdministrativeBootstrap.execute", fake_admin)
    monkeypatch.setattr(
        "dander.cli.main.RuntimeImagePublisher.publish",
        lambda self, **kwargs: (
            "us-central1-docker.pkg.dev/unit-project/dander/dander@sha256:" + "a" * 64
        ),
    )
    monkeypatch.setattr(
        "dander.cli.main.wait_for_service_account_impersonation", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "dander.cli.main.active_admin_member",
        lambda **kwargs: "user:operator@example.invalid",
    )
    monkeypatch.setattr("dander.cli.main.TerraformBootstrap.execute", fake_platform)

    result = CliRunner().invoke(
        app,
        [
            "init",
            "--project",
            "unit-project",
            "--config",
            str(project_dir / "dander.yaml"),
            "--operator-artifact-dir",
            str(tmp_path / "operator"),
            "--failure-alert-email",
            "operator@example.invalid",
            "--apply",
        ],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert captured_admin["billing_account_id"] == ""
    assert captured_platform["billing_account_id"] == ""
    assert captured_platform["enable_cost_guard"] is False
    assert captured_platform["require_guarded_free_tier"] is False
    assert (
        unstyle(result.output).count(
            "Dander is not managing, limiting, or preventing cloud spending"
        )
        == 1
    )


def test_init_rejects_required_guard_when_cost_guard_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_execute(self: object, **kwargs: object) -> Path:
        nonlocal called
        called = True
        raise AssertionError("must not execute")

    monkeypatch.setattr("dander.cli.main.TerraformBootstrap.execute", fake_execute)
    result = CliRunner().invoke(
        app,
        [
            "init",
            "--project",
            "unit-project",
            "--billing-account",
            "ABCDEF-123456-ABCDEF",
            "--container-image",
            f"example.invalid/project/repository/image@sha256:{'a' * 64}",
            "--no-cost-guard",
        ],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "require_guarded_free_tier=true requires the cost guard" in str(result.exception)
    assert called is False
