"""Saved-plan lifecycle coverage for manifest-defined Azure deployments."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from dander.bootstrap import AzureTerraformBootstrap, AzureTerraformBootstrapError

if TYPE_CHECKING:
    from pathlib import Path

_SUBSCRIPTION_ID = "11111111-1111-4111-8111-111111111111"
_CLIENT_ID = "22222222-2222-4222-8222-222222222222"
_SUBNET_ID = (
    f"/subscriptions/{_SUBSCRIPTION_ID}/resourceGroups/dander-phase6/"
    "providers/Microsoft.Network/virtualNetworks/dander-phase6/subnets/container-apps"
)


def _launcher() -> dict[str, object]:
    return {
        "provider": "azure_container_apps",
        "region": "eastus",
        "subscription_id": _SUBSCRIPTION_ID,
        "resource_group_name": "dander-phase6",
        "container_app_environment_name": "dander-phase6-env",
        "acr_name": "danderphase6",
        "key_vault_name": "dander-phase6-kv",
        "managed_identity_name": "dander-phase6-runtime",
        "managed_identity_client_id": _CLIENT_ID,
    }


def _pipelines() -> dict[str, dict[str, object]]:
    return {
        "warehouse_fixture": {
            "job_name": "dander-warehouse-fixture",
            "runtime_service_account_id": "dander-runtime",
            "scheduler_service_account_id": "dander-scheduler",
            "source": "warehouse_fixture",
            "models": ["stg_warehouse_fixture"],
            "build_models": True,
            "publish_dataplex": False,
            "schedule": "15 4 * * *",
            "time_zone": "UTC",
            "paused": True,
            "secret_env": {"DANDER_POSTGRES_DSN": "postgres-dsn"},
        }
    }


def _execute(bootstrap: AzureTerraformBootstrap, **overrides: object) -> Path:
    arguments: dict[str, object] = {
        "deployment_name": "azure_snowflake",
        "state_resource_group_name": "dander-phase6",
        "state_storage_account_name": "danderphase6state",
        "state_container_name": "tfstate",
        "state_key": "dander/azure/state/terraform.tfstate",
        "container_image": ("danderphase6.azurecr.io/dander/runtime@sha256:" + "a" * 64),
        "launcher_config": _launcher(),
        "key_vault_allowed_ip_rule": "203.0.113.10",
        "runtime_cpu": 1,
        "runtime_memory": "2Gi",
        "runtime_timeout_seconds": 900,
        "runtime_max_retries": 1,
        "runtime_batch_rows": 2_048,
        "require_guarded_free_tier": False,
        "pipelines": _pipelines(),
        "apply": False,
        "alert_target": (
            f"/subscriptions/{_SUBSCRIPTION_ID}/resourceGroups/dander-phase6/"
            "providers/microsoft.insights/actionGroups/dander-phase6"
        ),
        "infrastructure_subnet_id": _SUBNET_ID,
        "name": "dander",
    }
    arguments.update(overrides)
    return bootstrap.execute(**arguments)  # type: ignore[arg-type]


def test_azure_bootstrap_builds_manifest_projection_without_apply(
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
        umask: int,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == tmp_path.resolve()
        assert check and umask == 0o077
        calls.append((args, env))
        for argument in args:
            if argument.startswith("-out="):
                (cwd / argument.removeprefix("-out=")).touch()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    plan = _execute(AzureTerraformBootstrap(tmp_path))

    assert plan == tmp_path.resolve() / "dander-azure.tfplan"
    init, terraform_plan = (call[0] for call in calls)
    assert "-backend-config=use_azuread_auth=true" in init
    assert all(call[0][:2] != ("terraform", "apply") for call in calls)
    assert all(call[1]["ARM_USE_AZUREAD"] == "true" for call in calls)
    projection_argument = next(
        item for item in terraform_plan if item.startswith("-var=execution_projections=")
    )
    projection = json.loads(projection_argument.removeprefix("-var=execution_projections="))[
        "warehouse_fixture"
    ]
    assert projection["launcher"] == "azure_container_apps"
    assert projection["resources"]["memory_mib"] == 2_048
    assert projection["schedule"]["paused"] is True
    assert projection["secret_bindings"]["DANDER_POSTGRES_DSN"] == {
        "provider": "azure_key_vault",
        "reference": ("azure-kv://https://dander-phase6-kv.vault.azure.net/secrets/postgres-dsn"),
    }
    assert "postgresql://" not in projection_argument
    assert f"-var=infrastructure_subnet_id={_SUBNET_ID}" in terraform_plan


def test_azure_apply_uses_only_the_saved_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "dander-azure.tfplan"
    plan_path.write_bytes(b"reviewed")
    calls: list[tuple[str, ...]] = []

    def fake_run(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = AzureTerraformBootstrap(tmp_path).apply_saved_plan(
        subscription_id=_SUBSCRIPTION_ID,
        state_resource_group_name="dander-phase6",
        state_storage_account_name="danderphase6state",
        state_container_name="tfstate",
        state_key="dander/azure/state/terraform.tfstate",
    )

    assert result == plan_path
    assert calls[1] == ("terraform", "apply", "-input=false", "dander-azure.tfplan")
    assert all(call[:2] != ("terraform", "plan") for call in calls)


def test_azure_foundation_plan_omits_jobs_until_secrets_are_seeded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(args)
        for argument in args:
            if argument.startswith("-out="):
                (tmp_path / argument.removeprefix("-out=")).touch()
        stdout = json.dumps({"resource_changes": [{"change": {"actions": ["create"]}}]})
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    _execute(AzureTerraformBootstrap(tmp_path), foundation_only=True)

    terraform_plan = calls[1]
    assert "-var=create_jobs=false" in terraform_plan
    assert calls[2] == ("terraform", "show", "-json", "dander-azure.tfplan")


def test_azure_foundation_plan_rejects_existing_resource_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        for argument in args:
            if argument.startswith("-out="):
                (tmp_path / argument.removeprefix("-out=")).touch()
        stdout = json.dumps({"resource_changes": [{"change": {"actions": ["update"]}}]})
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AzureTerraformBootstrapError, match="modify or delete"):
        _execute(AzureTerraformBootstrap(tmp_path), foundation_only=True)

    assert not (tmp_path / "dander-azure.tfplan").exists()


def test_azure_bootstrap_projects_gcp_federation_without_credentials(
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
        umask: int,
    ) -> subprocess.CompletedProcess[str]:
        del check, env, umask
        calls.append(args)
        for argument in args:
            if argument.startswith("-out="):
                (cwd / argument.removeprefix("-out=")).touch()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    launcher = {
        **_launcher(),
        "google_workload_identity_audience": (
            "//iam.googleapis.com/projects/1009770943166/locations/global/"
            "workloadIdentityPools/dander-phase6-azure/providers/container-apps"
        ),
        "google_application_id_uri": "api://33333333-3333-4333-8333-333333333333",
    }
    _execute(
        AzureTerraformBootstrap(tmp_path),
        deployment_name="azure_bigquery",
        launcher_config=launcher,
        profile_id="gcp",
        gcp_project="unit-project",
        pipelines={
            "warehouse_fixture": {
                **_pipelines()["warehouse_fixture"],
                "secret_env": {"API_TOKEN": "source-api-token"},
            }
        },
        infrastructure_subnet_id=None,
    )

    terraform_plan = calls[1]
    projection_argument = next(
        item for item in terraform_plan if item.startswith("-var=execution_projections=")
    )
    projection = json.loads(projection_argument.removeprefix("-var=execution_projections="))[
        "warehouse_fixture"
    ]
    assert projection["environment"]["DANDER_GCP_SERVICE_ACCOUNT"] == (
        "dander-runtime@unit-project.iam.gserviceaccount.com"
    )
    assert projection["secret_bindings"]["API_TOKEN"]["provider"] == "gcp_secret_manager"
    assert "private_key" not in projection_argument


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"runtime_memory": "512Mi"}, "CPU/memory"),
        ({"state_key": "/absolute"}, "state key"),
        ({"container_image": "other.azurecr.io/dander@sha256:" + "a" * 64}, "selected ACR"),
        ({"pipelines": {}}, "at least one pipeline"),
        ({"alert_target": "not-an-id"}, "alert target"),
        ({"key_vault_allowed_ip_rule": "0.0.0.0/0"}, "Key Vault allowed IP"),
        ({"infrastructure_subnet_id": None}, "infrastructure subnet"),
    ],
)
def test_azure_bootstrap_rejects_unsafe_inputs_before_terraform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("Terraform must not run")
    )
    with pytest.raises(AzureTerraformBootstrapError, match=message):
        _execute(AzureTerraformBootstrap(tmp_path), **overrides)
