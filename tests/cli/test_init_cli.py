"""CLI coverage for the complete optional bootstrap."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click import unstyle
from typer.testing import CliRunner

from dander.cli.main import app
from dander.project import (
    DanderProject,
    PipelineSpec,
    PlatformRuntimeSpec,
    PlatformSafetySpec,
    PlatformSpec,
    scaffold_project,
)

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
            "--druff-container-image",
            f"example.invalid/project/repository/druff@sha256:{'b' * 64}",
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
    assert captured["druff_container_image"] == (
        f"example.invalid/project/repository/druff@sha256:{'b' * 64}"
    )
    pipelines = captured["pipelines"]
    assert isinstance(pipelines, dict)
    assert set(pipelines) == {
        "greenhouse_jobs",
        "greenhouse_jobs_graph",
        "hubspot_companies",
        "phase8_aws_qualification",
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


def test_image_publish_uses_bootstrap_identity_and_prints_platform_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_publish(self: object, **kwargs: object) -> str:
        captured.update(kwargs)
        return "us-central1-docker.pkg.dev/unit-project/dander/dander@sha256:" + "a" * 64

    monkeypatch.setattr("dander.cli.main.RuntimeImagePublisher.publish", fake_publish)

    result = CliRunner().invoke(
        app,
        [
            "image-publish",
            "--project",
            "unit-project",
            "--failure-alert-email",
            "operator@example.invalid",
            "--billing-account",
            "ABCDEF-123456-ABCDEF",
        ],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert captured["region"] == "us-central1"
    assert captured["impersonate_service_account"] == (
        "dander-bootstrap@unit-project.iam.gserviceaccount.com"
    )
    assert captured["require_source_free"] is True
    assert "init-platform-plan" in result.output
    assert "Published immutable runtime image" in result.output
    assert "operator@example.invalid" in result.output


def test_platform_plan_resolves_complete_manifest_without_applying(
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
            "init-platform-plan",
            "--project",
            "unit-project",
            "--state-bucket",
            "unit-state",
            "--bootstrap-service-account",
            "dander-bootstrap@unit-project.iam.gserviceaccount.com",
            "--container-image",
            f"example.invalid/dander@sha256:{'a' * 64}",
            "--failure-alert-email",
            "operator@example.invalid",
            "--billing-account",
            "ABCDEF-123456-ABCDEF",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["apply"] is False
    assert captured["enable_runtime"] is True
    pipelines = captured["pipelines"]
    assert isinstance(pipelines, dict)
    assert set(pipelines) == {
        "greenhouse_jobs",
        "greenhouse_jobs_graph",
        "hubspot_companies",
        "phase8_aws_qualification",
        "salesforce_accounts",
        "servicenow_incidents",
    }
    assert "terraform -chdir=infra show -no-color" in result.output
    assert "init-platform-apply" in result.output


def test_aws_plan_resolves_selected_fargate_deployment_without_applying(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    manifest = DanderProject(
        version=2,
        platform=PlatformSpec(
            region="us-east-1",
            runtime=PlatformRuntimeSpec(memory="2Gi", timeout_seconds=900),
            safety=PlatformSafetySpec(require_guarded_free_tier=False),
        ),
        pipelines={
            "greenhouse_jobs": PipelineSpec(
                source="greenhouse_job_board",
                models=["stg_greenhouse__jobs"],
            )
        },
        deployment_name="aws_fargate",
        launcher_provider="fargate",
        launcher_config={
            "provider": "fargate",
            "region": "us-east-1",
            "aws_account_id": "123456789012",
            "google_workload_identity_audience": (
                "//iam.googleapis.com/projects/123456789012/locations/global/"
                "workloadIdentityPools/dander-aws/providers/dander-aws"
            ),
            "subnet_ids": ["subnet-0123456789abcdef0"],
            "security_group_ids": ["sg-0123456789abcdef0"],
        },
    )

    def fake_load(*args: object, **kwargs: object) -> DanderProject:
        assert kwargs["deployment"] == "aws_fargate"
        return manifest

    def fake_validate(self: object, root: Path) -> None:
        del self, root

    def fake_execute(self: object, **kwargs: object) -> Path:
        captured.update(kwargs)
        return tmp_path / "dander-aws.tfplan"

    monkeypatch.setattr("dander.cli.aws_command.load_project_config", fake_load)
    monkeypatch.setattr(DanderProject, "validate_references", fake_validate)
    monkeypatch.setattr("dander.cli.aws_command.AwsTerraformBootstrap.execute", fake_execute)

    result = CliRunner().invoke(
        app,
        [
            "init-aws-plan",
            "--project",
            "unit-project",
            "--state-bucket",
            "unit-dander-state",
            "--container-image",
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:" + "a" * 64,
            "--deployment",
            "aws_fargate",
            "--lock-table",
            "dander-terraform-locks",
            "--aws-profile",
            "dander-test",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["apply"] is False
    assert captured["project"] == "unit-project"
    assert captured["profile_id"] == "gcp"
    assert captured["secret_config"] == {"provider": "gcp_secret_manager"}
    assert captured["launcher_config"] == manifest.resolved_launcher_config()
    assert captured["runtime_memory"] == "2Gi"
    assert "AWS deployment planned" in result.output
    assert "init-aws-apply" in result.output


def test_aws_native_plan_omits_gcp_project_and_passes_typed_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    secret = (
        "aws-sm://arn:aws:secretsmanager:us-east-1:123456789012:secret:dander/postgres-dsn-AbCdEf"
    )
    manifest = DanderProject(
        version=2,
        platform=PlatformSpec(
            region="us-east-1",
            runtime=PlatformRuntimeSpec(memory="2Gi", timeout_seconds=900),
            safety=PlatformSafetySpec(require_guarded_free_tier=False),
        ),
        pipelines={
            "greenhouse_jobs": PipelineSpec(
                source="greenhouse_job_board",
                models=["stg_greenhouse__jobs"],
                secrets={"DANDER_POSTGRES_DSN": secret},
            )
        },
        platform_name="aws_native",
        deployment_name="aws_native",
        warehouse_provider="redshift",
        warehouse_config={
            "provider": "redshift",
            "deployment": "provisioned",
            "host": "dander.abc123.us-east-1.redshift.amazonaws.com",
            "database": "analytics",
            "schema": "raw",
            "db_user": "dander_runtime",
            "region": "us-east-1",
            "cluster_identifier": "dander-phase8",
            "copy_role_arn": "arn:aws:iam::123456789012:role/DanderRedshiftCopy",
            "staging_bucket": "dander-phase8-staging",
        },
        state_provider="postgresql",
        state_config={
            "provider": "postgresql",
            "authority_id": "postgresql:aws-native",
            "dsn_env": "DANDER_POSTGRES_DSN",
        },
        catalog_provider="glue",
        catalog_config={
            "provider": "glue",
            "region": "us-east-1",
            "catalog_id": "123456789012",
        },
        secret_provider="aws_secret_manager",
        secret_config={"provider": "aws_secret_manager", "region": "us-east-1"},
        launcher_provider="fargate",
        launcher_config={
            "provider": "fargate",
            "region": "us-east-1",
            "aws_account_id": "123456789012",
            "subnet_ids": ["subnet-0123456789abcdef0"],
            "security_group_ids": ["sg-0123456789abcdef0"],
        },
    )

    def fake_load(*args: object, **kwargs: object) -> DanderProject:
        assert kwargs["deployment"] == "aws_native"
        return manifest

    def fake_validate(self: object, root: Path) -> None:
        del self, root

    def fake_execute(self: object, **kwargs: object) -> Path:
        captured.update(kwargs)
        return tmp_path / "dander-aws.tfplan"

    monkeypatch.setattr("dander.cli.aws_command.load_project_config", fake_load)
    monkeypatch.setattr(DanderProject, "validate_references", fake_validate)
    monkeypatch.setattr("dander.cli.aws_command.AwsTerraformBootstrap.execute", fake_execute)

    result = CliRunner().invoke(
        app,
        [
            "init-aws-plan",
            "--state-bucket",
            "unit-dander-state",
            "--container-image",
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:" + "a" * 64,
            "--deployment",
            "aws_native",
            "--lock-table",
            "dander-terraform-locks",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["project"] is None
    assert captured["profile_id"] == "aws_native"
    assert captured["warehouse_config"] == manifest.warehouse_config
    assert captured["state_config"] == manifest.state_config
    assert captured["catalog_config"] == manifest.catalog_config
    assert captured["secret_config"] == manifest.secret_config


def test_azure_bigquery_plan_requires_explicit_gcp_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    manifest = DanderProject(
        version=2,
        platform=PlatformSpec(
            region="eastus",
            runtime=PlatformRuntimeSpec(memory="2Gi", timeout_seconds=900),
            safety=PlatformSafetySpec(require_guarded_free_tier=False),
        ),
        pipelines={
            "warehouse_fixture": PipelineSpec(
                source="warehouse_fixture",
                models=[],
                build_models=False,
            )
        },
        platform_name="azure_bigquery",
        deployment_name="azure_bigquery",
        warehouse_provider="bigquery",
        warehouse_config={"provider": "bigquery", "location": "US", "dataset": "raw"},
        launcher_provider="azure_container_apps",
        launcher_config={
            "provider": "azure_container_apps",
            "region": "eastus",
            "subscription_id": "11111111-1111-4111-8111-111111111111",
        },
    )

    def fake_load(*args: object, **kwargs: object) -> DanderProject:
        assert kwargs["deployment"] == "azure_bigquery"
        return manifest

    def fake_validate(self: object, root: Path) -> None:
        del self, root

    def fake_execute(self: object, **kwargs: object) -> Path:
        captured.update(kwargs)
        return tmp_path / "dander-azure.tfplan"

    monkeypatch.setattr("dander.cli.azure_command.load_project_config", fake_load)
    monkeypatch.setattr(DanderProject, "validate_references", fake_validate)
    monkeypatch.setattr(
        "dander.cli.azure_command.AzureTerraformBootstrap.execute",
        fake_execute,
    )
    base_args = [
        "init-azure-plan",
        "--state-resource-group",
        "dander-phase6",
        "--state-storage-account",
        "danderphase6state",
        "--container-image",
        "danderphase6.azurecr.io/dander@sha256:" + "a" * 64,
        "--deployment",
        "azure_bigquery",
        "--key-vault-allowed-ip",
        "203.0.113.10",
    ]

    missing = CliRunner().invoke(app, base_args)
    planned = CliRunner().invoke(
        app,
        [*base_args, "--gcp-project", "unit-project"],
    )

    assert missing.exit_code == 1
    assert missing.exception is not None
    assert "requires an explicit --gcp-project" in str(missing.exception)
    assert planned.exit_code == 0, planned.output
    assert captured["gcp_project"] == "unit-project"
    assert captured["apply"] is False
    assert "Azure deployment planned" in planned.output


def test_aws_admin_plan_saves_without_applying_and_prints_exact_next_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_execute(self: object, **kwargs: object) -> Path:
        captured.update(kwargs)
        return tmp_path / "operator" / "dander-aws-admin-bootstrap.tfplan"

    monkeypatch.setattr("dander.cli.aws_command.AwsAdministrativeBootstrap.execute", fake_execute)
    operator_dir = tmp_path / "operator"
    result = CliRunner().invoke(
        app,
        [
            "init-aws-admin-plan",
            "--aws-account-id",
            "184463061564",
            "--state-bucket",
            "dander-184463061564-state",
            "--admin-principal-arn",
            "arn:aws:iam::184463061564:root",
            "--operator-artifact-dir",
            str(operator_dir),
            "--aws-profile",
            "dander-phase1b",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["aws_account_id"] == "184463061564"
    assert captured["lock_table"] == "dander-terraform-locks"
    assert "AWS administrative bootstrap planned" in result.output
    assert "init-aws-admin-apply" in result.output
    assert "terraform-workspace" in result.output


def test_aws_image_promotion_requires_confirmation_and_does_not_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_promote(self: object, **kwargs: object) -> str:
        captured.update(kwargs)
        return "184463061564.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:" + "a" * 64

    monkeypatch.setattr("dander.cli.aws_command.RuntimeImagePromoter.promote", fake_promote)
    config = tmp_path / "dander.yaml"
    config.write_text("version: 1\n", encoding="utf-8")
    source = "us-central1-docker.pkg.dev/unit-project/dander/dander@sha256:" + "a" * 64
    result = CliRunner().invoke(
        app,
        [
            "image-promote-aws",
            "--source-image",
            source,
            "--aws-account-id",
            "184463061564",
            "--aws-profile",
            "dander-deploy",
            "--config",
            str(config),
        ],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert captured["source_image"] == source
    assert captured["aws_profile"] == "dander-deploy"
    assert "Promoted byte-identical runtime image" in result.output
    assert "init-aws-plan" in result.output


def test_admin_plan_runs_read_only_permission_preflight_before_terraform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    def fake_preflight(**kwargs: object) -> None:
        events.append("preflight")
        assert kwargs["project"] == "unit-project"
        assert kwargs["state_bucket"] == "unit-state"

    def fake_execute(self: object, **kwargs: object) -> Path:
        events.append("terraform")
        return tmp_path / "dander-admin-bootstrap.tfplan"

    monkeypatch.setattr("dander.cli.main.require_stage_zero_permissions", fake_preflight)
    monkeypatch.setattr("dander.cli.main.AdministrativeBootstrap.execute", fake_execute)

    result = CliRunner().invoke(
        app,
        [
            "init-admin-plan",
            "--project",
            "unit-project",
            "--state-bucket",
            "unit-state",
            "--admin-member",
            "user:operator@example.invalid",
            "--operator-artifact-dir",
            str(tmp_path / "operator"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert events == ["preflight", "terraform"]
    assert "init-admin-apply" in result.output


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


def test_public_druff_is_named_in_apply_confirmation(
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
            "--druff-container-image",
            f"example.invalid/project/repository/druff@sha256:{'b' * 64}",
            "--apply",
        ],
        input="n\n",
    )

    assert "including a public Druff interface" in result.output


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
