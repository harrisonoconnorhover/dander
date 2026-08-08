"""Version 2 platform profile and deterministic migration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from dander.cli.main import app
from dander.project import ProjectConfigError, load_project_config, prepare_version_one_migration

if TYPE_CHECKING:
    from pathlib import Path

_V1_PROJECT = """
version: 1
platform:
  region: us-east1
  bigquery_location: EU
  runtime:
    cpu: 2
    memory: 1Gi
    timeout_seconds: 600
    max_retries: 2
    batch_rows: 2048
  safety:
    require_guarded_free_tier: false
plugins:
  example:
    distribution: dander-connector-example
    version: 1.2.3
pipelines:
  example_records:
    source: example
    models: [stg_example__records]
    publish_dataplex: true
    schedule: "15 4 * * *"
    time_zone: UTC
    paused: false
    secrets:
      EXAMPLE_TOKEN: example-token
    resources:
      job: dander-example-records
      runtime_service_account: dander-example-run
      scheduler_service_account: dander-example-sched
""".strip()


def test_version_one_migration_is_deterministic_and_behaviorally_equivalent(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.yaml"
    project_path.write_text(_V1_PROJECT, encoding="utf-8")
    original = load_project_config(project_path)

    first = prepare_version_one_migration(project_path)
    second = prepare_version_one_migration(project_path)
    project_path.write_text(first.logical_yaml, encoding="utf-8")
    platforms_path.write_text(first.platforms_yaml, encoding="utf-8")
    migrated = load_project_config(project_path)

    assert first == second
    assert migrated.version == 2
    assert migrated.platform_name == "gcp"
    assert migrated.deployment_name == "gcp_cloud_run"
    assert original.warehouse_provider == "bigquery"
    assert migrated.warehouse_provider == "bigquery"
    assert original.state_provider == "bigquery"
    assert migrated.state_provider == "bigquery"
    assert original.catalog_provider == "dataplex"
    assert migrated.catalog_provider == "dataplex"
    assert original.secret_provider == "gcp_secret_manager"
    assert migrated.secret_provider == "gcp_secret_manager"
    assert original.launcher_provider == "cloud_run"
    assert migrated.launcher_provider == "cloud_run"
    assert migrated.platform == original.platform
    assert migrated.plugins == original.plugins
    assert migrated.pipelines == original.pipelines
    assert migrated.terraform_pipelines() == original.terraform_pipelines()


def test_version_two_requires_explicit_selection_with_multiple_deployments(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.yaml"
    project_path.write_text(_V1_PROJECT, encoding="utf-8")
    migration = prepare_version_one_migration(project_path)
    project_path.write_text(migration.logical_yaml, encoding="utf-8")
    platforms = yaml.safe_load(migration.platforms_yaml)
    platforms["deployments"]["shadow_cloud_run"] = platforms["deployments"]["gcp_cloud_run"].copy()
    logical = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    logical["pipelines"]["undeployed_records"] = {
        "source": "example",
        "models": ["stg_example__records"],
    }
    project_path.write_text(yaml.safe_dump(logical), encoding="utf-8")
    platforms_path.write_text(yaml.safe_dump(platforms), encoding="utf-8")

    with pytest.raises(ProjectConfigError, match="Multiple deployments"):
        load_project_config(project_path)

    resolved = load_project_config(project_path, deployment="shadow_cloud_run")
    assert resolved.deployment_name == "shadow_cloud_run"
    assert set(resolved.pipelines) == {"example_records", "undeployed_records"}
    assert set(resolved.terraform_pipelines()) == {"example_records"}


def test_version_two_rejects_unknown_provider_and_unknown_pipeline(tmp_path: Path) -> None:
    project_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.yaml"
    project_path.write_text(_V1_PROJECT, encoding="utf-8")
    migration = prepare_version_one_migration(project_path)
    project_path.write_text(migration.logical_yaml, encoding="utf-8")
    platforms = yaml.safe_load(migration.platforms_yaml)
    platforms["platforms"]["gcp"]["warehouse"]["provider"] = "snowflake"
    platforms_path.write_text(yaml.safe_dump(platforms), encoding="utf-8")

    with pytest.raises(
        ProjectConfigError,
        match=r"platforms\.gcp\.warehouse\.provider",
    ):
        load_project_config(project_path)

    platforms = yaml.safe_load(migration.platforms_yaml)
    pipelines = platforms["deployments"]["gcp_cloud_run"]["pipelines"]
    pipelines["missing_pipeline"] = pipelines["example_records"]
    platforms_path.write_text(yaml.safe_dump(platforms), encoding="utf-8")

    with pytest.raises(ProjectConfigError, match="unknown pipeline 'missing_pipeline'"):
        load_project_config(project_path)


def test_version_two_none_catalog_disables_external_publication(tmp_path: Path) -> None:
    project_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.yaml"
    project_path.write_text(_V1_PROJECT, encoding="utf-8")
    migration = prepare_version_one_migration(project_path)
    project_path.write_text(migration.logical_yaml, encoding="utf-8")
    platforms = yaml.safe_load(migration.platforms_yaml)
    platforms["platforms"]["gcp"]["catalog"]["provider"] = "none"
    platforms_path.write_text(yaml.safe_dump(platforms), encoding="utf-8")

    resolved = load_project_config(project_path)

    assert resolved.catalog_provider == "none"
    assert resolved.pipelines["example_records"].publish_dataplex is False


def test_version_two_preserves_postgresql_state_connection_reference(tmp_path: Path) -> None:
    project_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.yaml"
    project_path.write_text(_V1_PROJECT, encoding="utf-8")
    migration = prepare_version_one_migration(project_path)
    project_path.write_text(migration.logical_yaml, encoding="utf-8")
    platforms = yaml.safe_load(migration.platforms_yaml)
    platforms["platforms"]["gcp"]["state"] = {
        "provider": "postgresql",
        "authority_id": "postgresql:portable-state",
        "dsn_env": "DANDER_STATE_DATABASE_URL",
        "schema_name": "dander_control",
        "pool_min_size": 2,
        "pool_max_size": 8,
        "lease_seconds": 180,
        "terminal_history_retention_days": 120,
    }
    platforms_path.write_text(yaml.safe_dump(platforms), encoding="utf-8")

    resolved = load_project_config(project_path)

    assert resolved.state_provider == "postgresql"
    assert resolved.state_config == {
        "provider": "postgresql",
        "authority_id": "postgresql:portable-state",
        "authority_epoch": 1,
        "dsn_env": "DANDER_STATE_DATABASE_URL",
        "schema_name": "dander_control",
        "pool_min_size": 2,
        "pool_max_size": 8,
        "pool_timeout_seconds": 10.0,
        "lease_seconds": 180,
        "terminal_history_retention_days": 120,
    }


def test_cloud_run_rejects_environment_only_secrets(tmp_path: Path) -> None:
    project_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.yaml"
    project_path.write_text(_V1_PROJECT, encoding="utf-8")
    migration = prepare_version_one_migration(project_path)
    project_path.write_text(migration.logical_yaml, encoding="utf-8")
    platforms = yaml.safe_load(migration.platforms_yaml)
    platforms["platforms"]["gcp"]["secrets"]["provider"] = "environment"
    platforms_path.write_text(yaml.safe_dump(platforms), encoding="utf-8")

    with pytest.raises(ProjectConfigError, match="Cloud Run requires"):
        load_project_config(project_path)


def test_version_two_resolves_complete_fargate_launcher(tmp_path: Path) -> None:
    project_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.yaml"
    project_path.write_text(_V1_PROJECT, encoding="utf-8")
    migration = prepare_version_one_migration(project_path)
    project_path.write_text(migration.logical_yaml, encoding="utf-8")
    platforms = yaml.safe_load(migration.platforms_yaml)
    deployment = platforms["deployments"].pop("gcp_cloud_run")
    deployment["launcher"] = {
        "provider": "fargate",
        "region": "us-east-1",
        "aws_account_id": "123456789012",
        "google_workload_identity_audience": (
            "//iam.googleapis.com/projects/123456789012/locations/global/"
            "workloadIdentityPools/dander-aws/providers/dander-aws"
        ),
        "subnet_ids": ["subnet-0123456789abcdef0"],
        "security_group_ids": ["sg-0123456789abcdef0"],
        "architecture": "X86_64",
        "assign_public_ip": True,
    }
    deployment["runtime"]["memory"] = "2Gi"
    deployment["safety"]["require_guarded_free_tier"] = False
    platforms["deployments"]["aws_fargate"] = deployment
    platforms_path.write_text(yaml.safe_dump(platforms), encoding="utf-8")

    resolved = load_project_config(project_path, deployment="aws_fargate")

    assert resolved.launcher_provider == "fargate"
    assert resolved.platform.region == "us-east-1"
    assert resolved.resolved_launcher_config() == deployment["launcher"] | {
        "ephemeral_storage_mib": 20_480,
        "stop_timeout_seconds": 120,
    }


def test_config_migrate_check_is_read_only_then_write_is_atomic(tmp_path: Path) -> None:
    project_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.yaml"
    project_path.write_text(_V1_PROJECT, encoding="utf-8")

    checked = CliRunner().invoke(
        app,
        ["config", "migrate", "--config", str(project_path), "--check"],
    )
    assert checked.exit_code == 0, checked.output
    assert "no files changed" in checked.output
    assert project_path.read_text(encoding="utf-8") == _V1_PROJECT
    assert not platforms_path.exists()

    migrated = CliRunner().invoke(
        app,
        ["config", "migrate", "--config", str(project_path)],
    )
    assert migrated.exit_code == 0, migrated.output
    assert platforms_path.is_file()
    resolved = load_project_config(project_path)
    assert resolved.version == 2
    assert resolved.terraform_pipelines()["example_records"]["paused"] is False

    refused = CliRunner().invoke(
        app,
        ["config", "migrate", "--config", str(project_path)],
    )
    assert refused.exit_code != 0
    assert refused.exception is not None
    assert "Only a version 1" in str(refused.exception)
