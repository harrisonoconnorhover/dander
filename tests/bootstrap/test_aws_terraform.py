"""Saved-plan lifecycle coverage for manifest-defined AWS deployments."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from dander.bootstrap import AwsTerraformBootstrap, AwsTerraformBootstrapError
from dander.project import load_project_config

if TYPE_CHECKING:
    from pathlib import Path


def _launcher() -> dict[str, object]:
    return {
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


def _pipelines() -> dict[str, dict[str, object]]:
    return {
        "greenhouse_jobs": {
            "job_name": "dander-greenhouse-public",
            "runtime_service_account_id": "dander-runtime",
            "scheduler_service_account_id": "dander-scheduler",
            "source": "greenhouse_job_board",
            "models": ["stg_greenhouse__jobs"],
            "build_models": True,
            "publish_dataplex": True,
            "schedule": "0 9 * * *",
            "time_zone": "America/New_York",
            "paused": True,
            "secret_env": {"API_TOKEN": "greenhouse-token"},
        }
    }


def _execute(bootstrap: AwsTerraformBootstrap, **overrides: object) -> Path:
    arguments: dict[str, object] = {
        "project": "unit-project",
        "deployment_name": "aws_fargate",
        "state_bucket": "unit-dander-state",
        "state_key": "dander/aws/state/terraform.tfstate",
        "state_region": "us-east-1",
        "lock_table": "dander-terraform-locks",
        "container_image": (
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:" + "a" * 64
        ),
        "launcher_config": _launcher(),
        "runtime_cpu": 1,
        "runtime_memory": "2Gi",
        "runtime_timeout_seconds": 900,
        "runtime_max_retries": 1,
        "runtime_batch_rows": 2_048,
        "require_guarded_free_tier": False,
        "pipelines": _pipelines(),
        "apply": False,
        "aws_profile": "dander-test",
        "name": "dander-test",
    }
    arguments.update(overrides)
    return bootstrap.execute(**arguments)  # type: ignore[arg-type]


def test_aws_bootstrap_builds_manifest_projection_without_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == tmp_path.resolve()
        assert check
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    plan = _execute(AwsTerraformBootstrap(tmp_path))

    assert plan == tmp_path.resolve() / "dander-aws.tfplan"
    init, terraform_plan = (call[0] for call in calls)
    assert init[:2] == ("terraform", "init")
    assert "-backend-config=dynamodb_table=dander-terraform-locks" in init
    assert terraform_plan[:2] == ("terraform", "plan")
    assert all(call[0][:2] != ("terraform", "apply") for call in calls)
    assert all(call[1]["AWS_PROFILE"] == "dander-test" for call in calls)
    projection_argument = next(
        item for item in terraform_plan if item.startswith("-var=execution_projections=")
    )
    projection = json.loads(projection_argument.removeprefix("-var=execution_projections="))[
        "greenhouse_jobs"
    ]
    assert projection["launcher"] == "fargate"
    assert projection["profile_id"] == "aws_fargate"
    assert projection["labels"]["profile"] == "gcp"
    assert projection["resources"]["memory_mib"] == 2_048
    assert projection["schedule"]["paused"] is True
    command = projection["command"]
    assert command[command.index("--platform") + 1] == "aws_fargate"
    assert projection["secret_bindings"]["API_TOKEN"] == {
        "provider": "gcp_secret_manager",
        "reference": "gcp-sm://projects/unit-project/secrets/greenhouse-token/versions/latest",
    }
    projected_platforms = json.loads(projection["environment"]["DANDER_PLATFORMS_CONFIG_JSON"])
    assert list(projected_platforms["deployments"]) == ["aws_fargate"]
    assert projected_platforms["deployments"]["aws_fargate"]["platform"] == "gcp"


def test_aws_bootstrap_builds_the_manifest_bound_aws_native_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del env
        assert cwd == tmp_path.resolve()
        assert check
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    secret_prefix = "aws-sm://arn:aws:secretsmanager:us-east-1:123456789012:secret:dander/"
    pipelines = _pipelines()
    pipelines["greenhouse_jobs"]["secret_env"] = {
        "API_TOKEN": secret_prefix + "api-token-AbCdEf",
        "DANDER_POSTGRES_DSN": secret_prefix + "postgres-dsn-AbCdEf",
    }

    _execute(
        AwsTerraformBootstrap(tmp_path),
        project=None,
        profile_id="aws_native",
        launcher_config={
            key: value
            for key, value in _launcher().items()
            if key != "google_workload_identity_audience"
        },
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
            "staging_prefix": "dander/staging",
        },
        state_config={
            "provider": "postgresql",
            "authority_id": "postgresql:aws-native",
            "dsn_env": "DANDER_POSTGRES_DSN",
        },
        catalog_config={
            "provider": "glue",
            "region": "us-east-1",
            "catalog_id": "123456789012",
            "database_prefix": "dander",
        },
        secret_config={"provider": "aws_secret_manager", "region": "us-east-1"},
        pipelines=pipelines,
    )

    terraform_plan = calls[1]
    projection_argument = next(
        item for item in terraform_plan if item.startswith("-var=execution_projections=")
    )
    projection = json.loads(projection_argument.removeprefix("-var=execution_projections="))[
        "greenhouse_jobs"
    ]
    assert projection["profile_id"] == "aws_fargate"
    assert projection["labels"]["profile"] == "aws_native"
    command = projection["command"]
    assert command[command.index("--platform") + 1] == "aws_fargate"
    assert projection["secret_bindings"]["DANDER_POSTGRES_DSN"] == {
        "provider": "aws_secret_manager",
        "reference": secret_prefix + "postgres-dsn-AbCdEf",
    }
    assert "GCP_PROJECT_ID" not in projection["environment"]
    projected_platforms = json.loads(projection["environment"]["DANDER_PLATFORMS_CONFIG_JSON"])
    assert list(projected_platforms["platforms"]) == ["aws_native"]
    assert list(projected_platforms["deployments"]) == ["aws_fargate"]
    assert projected_platforms["platforms"]["aws_native"]["warehouse"]["provider"] == ("redshift")
    assert (
        projected_platforms["deployments"]["aws_fargate"]["pipelines"]["greenhouse_jobs"][
            "secret_bindings"
        ]
        == pipelines["greenhouse_jobs"]["secret_env"]
    )
    logical_path = tmp_path / "dander.yaml"
    platforms_path = tmp_path / "dander.platforms.json"
    logical_path.write_text(
        json.dumps(
            {
                "version": 2,
                "pipelines": {
                    "greenhouse_jobs": {
                        "source": "greenhouse_job_board",
                        "models": ["stg_greenhouse__jobs"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    platforms_path.write_text(json.dumps(projected_platforms), encoding="utf-8")

    resolved = load_project_config(
        logical_path,
        platforms_path=platforms_path,
        deployment="aws_fargate",
    )

    assert resolved.warehouse_provider == "redshift"
    assert resolved.state_provider == "postgresql"
    assert resolved.catalog_provider == "glue"
    assert resolved.secret_provider == "aws_secret_manager"
    native_argument = next(
        item for item in terraform_plan if item.startswith("-var=aws_native_profile=")
    )
    native = json.loads(native_argument.removeprefix("-var=aws_native_profile="))
    assert native == {
        "glue_catalog_id": "123456789012",
        "glue_database_prefix": "dander",
        "redshift_cluster_identifier": "dander-phase8",
        "redshift_database": "analytics",
        "redshift_db_user": "dander_runtime",
        "redshift_deployment": "provisioned",
        "redshift_workgroup_name": None,
        "staging_bucket": "dander-phase8-staging",
        "staging_prefix": "dander/staging",
    }
    tags_argument = next(item for item in terraform_plan if item.startswith("-var=tags="))
    assert json.loads(tags_argument.removeprefix("-var=tags="))["dander-profile"] == "aws_native"


def test_aws_apply_uses_only_the_saved_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "dander-aws.tfplan"
    plan_path.write_bytes(b"reviewed")
    calls: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del env
        assert cwd == tmp_path.resolve()
        assert check
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = AwsTerraformBootstrap(tmp_path).apply_saved_plan(
        state_bucket="unit-dander-state",
        state_key="dander/aws/state/terraform.tfstate",
        state_region="us-east-1",
        lock_table="dander-terraform-locks",
        aws_profile="dander-test",
    )

    assert result == plan_path
    assert calls[0][:2] == ("terraform", "init")
    assert calls[1] == ("terraform", "apply", "-input=false", "dander-aws.tfplan")
    assert all(call[:2] != ("terraform", "plan") for call in calls)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"require_guarded_free_tier": True}, "guarded-free-tier"),
        ({"runtime_memory": "512Mi"}, "CPU/memory"),
        ({"state_key": "/absolute"}, "state key"),
        ({"aws_profile": "bad profile"}, "AWS profile"),
        ({"lock_table": "bad table"}, "lock table"),
        ({"container_image": "example.invalid/dander@sha256:" + "a" * 64}, "ECR image"),
        (
            {
                "container_image": (
                    "999999999999.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:" + "a" * 64
                )
            },
            "account and region",
        ),
        ({"pipelines": {}}, "at least one pipeline"),
    ],
)
def test_aws_bootstrap_rejects_unsafe_inputs_before_terraform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Terraform must not run"),
    )

    with pytest.raises(AwsTerraformBootstrapError, match=message):
        _execute(AwsTerraformBootstrap(tmp_path), **overrides)
