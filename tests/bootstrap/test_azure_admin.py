"""Azure stage-zero saved-plan and state-migration coverage."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from typing import TypedDict

import pytest

from dander.bootstrap import (
    AzureAdministrativeBootstrap,
    AzureAdministrativeBootstrapError,
)


class _Arguments(TypedDict):
    subscription_id: str
    location: str
    resource_group_name: str
    storage_account_name: str
    state_container_name: str
    state_allowed_ip_rule: str
    state_key: str
    acr_name: str
    managed_identity_name: str


def _arguments() -> _Arguments:
    return {
        "subscription_id": "11111111-1111-4111-8111-111111111111",
        "location": "eastus",
        "resource_group_name": "dander-phase6",
        "storage_account_name": "danderphase6state",
        "state_container_name": "tfstate",
        "state_allowed_ip_rule": "203.0.113.10",
        "state_key": "dander/azure/bootstrap-admin/terraform.tfstate",
        "acr_name": "danderphase6",
        "managed_identity_name": "dander-phase6-runtime",
    }


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    infra_dir = tmp_path / "checkout" / "infra" / "azure" / "bootstrap-admin"
    infra_dir.mkdir(parents=True)
    (infra_dir / "main.tf").write_text('resource "terraform_data" "unit" {}\n', encoding="utf-8")
    return infra_dir, tmp_path / "operator"


def test_azure_admin_plan_uses_secured_local_state_without_applying(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    commands: list[tuple[str, ...]] = []
    backends: list[dict[str, object]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        env: dict[str, str],
        umask: int,
    ) -> subprocess.CompletedProcess[str]:
        assert check and umask == 0o077
        assert env["TF_DATA_DIR"] == str(operator_dir / "terraform-data")
        commands.append(args)
        backends.append(json.loads((cwd / "backend.tf.json").read_text(encoding="utf-8")))
        for argument in args:
            if argument.startswith("-out="):
                Path(argument.removeprefix("-out=")).touch(mode=0o644)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    plan = AzureAdministrativeBootstrap(infra_dir, operator_dir).execute(**_arguments())

    assert plan == operator_dir / "dander-azure-admin-bootstrap.tfplan"
    assert stat.S_IMODE(plan.stat().st_mode) == 0o600
    assert commands[0][:2] == ("terraform", "init")
    assert commands[1][:2] == ("terraform", "plan")
    assert not any(command[:2] == ("terraform", "apply") for command in commands)
    assert backends[0]["terraform"] == {
        "backend": {"local": {"path": str(operator_dir / "terraform.tfstate")}}
    }


def test_azure_admin_apply_uses_saved_plan_then_migrates_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    commands: list[tuple[str, ...]] = []
    backend_types: list[str] = []

    def fake_run(
        args: tuple[str, ...], *, cwd: Path, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(args)
        backend = json.loads((cwd / "backend.tf.json").read_text(encoding="utf-8"))
        backend_types.append(next(iter(backend["terraform"]["backend"])))
        for argument in args:
            if argument.startswith("-out="):
                Path(argument.removeprefix("-out=")).touch()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    bootstrap = AzureAdministrativeBootstrap(infra_dir, operator_dir)
    plan = bootstrap.execute(**_arguments())
    commands.clear()
    backend_types.clear()

    applied = bootstrap.apply_saved_plan(**_arguments())

    assert applied == plan
    assert commands[1] == ("terraform", "apply", "-input=false", str(plan))
    assert commands[2] == (
        "terraform",
        "init",
        "-migrate-state",
        "-force-copy",
        "-input=false",
    )
    assert backend_types == ["local", "local", "azurerm"]
    record = json.loads((operator_dir / "backend.json").read_text(encoding="utf-8"))
    assert record == {
        "schema": "io.dander.azure-bootstrap-backend/v1",
        "subscription_id": _arguments()["subscription_id"],
        "resource_group_name": "dander-phase6",
        "storage_account_name": "danderphase6state",
        "container_name": "tfstate",
        "key": "dander/azure/bootstrap-admin/terraform.tfstate",
    }


def test_azure_admin_retries_state_migration_during_role_propagation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    migration_attempts = 0
    sleeps: list[int] = []

    def fake_run(
        args: tuple[str, ...], *, cwd: Path, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal migration_attempts
        del cwd, kwargs
        for argument in args:
            if argument.startswith("-out="):
                Path(argument.removeprefix("-out=")).touch()
        if args[1:3] == ("init", "-migrate-state"):
            migration_attempts += 1
            if migration_attempts < 3:
                return subprocess.CompletedProcess(
                    args,
                    1,
                    stdout="",
                    stderr="AuthorizationPermissionMismatch",
                )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("dander.bootstrap.azure_admin.time.sleep", sleeps.append)
    bootstrap = AzureAdministrativeBootstrap(infra_dir, operator_dir)
    bootstrap.execute(**_arguments())

    bootstrap.apply_saved_plan(**_arguments())

    assert migration_attempts == 3
    assert sleeps == [10, 10]
    assert (operator_dir / "backend.json").is_file()


def test_azure_admin_does_not_retry_unrelated_state_migration_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    migration_attempts = 0

    def fake_run(
        args: tuple[str, ...], *, cwd: Path, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal migration_attempts
        del cwd, kwargs
        for argument in args:
            if argument.startswith("-out="):
                Path(argument.removeprefix("-out=")).touch()
        if args[1:3] == ("init", "-migrate-state"):
            migration_attempts += 1
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="invalid backend")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "dander.bootstrap.azure_admin.time.sleep",
        lambda _: pytest.fail("Unrelated failures must not be retried"),
    )
    bootstrap = AzureAdministrativeBootstrap(infra_dir, operator_dir)
    bootstrap.execute(**_arguments())

    with pytest.raises(AzureAdministrativeBootstrapError, match="terraform init failed"):
        bootstrap.apply_saved_plan(**_arguments())

    assert migration_attempts == 1
    backend = json.loads(
        (operator_dir / "terraform-workspace" / "backend.tf.json").read_text(encoding="utf-8")
    )
    assert "local" in backend["terraform"]["backend"]


def test_azure_admin_reuses_only_the_recorded_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    operator_dir.mkdir()
    (operator_dir / "backend.json").write_text(
        json.dumps(
            {
                "schema": "io.dander.azure-bootstrap-backend/v1",
                "subscription_id": _arguments()["subscription_id"],
                "resource_group_name": "dander-phase6",
                "storage_account_name": "danderphase6state",
                "container_name": "tfstate",
                "key": "dander/azure/bootstrap-admin/terraform.tfstate",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(
        args: tuple[str, ...], *, cwd: Path, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        backend = json.loads((cwd / "backend.tf.json").read_text(encoding="utf-8"))
        assert backend["terraform"]["backend"]["azurerm"]["use_azuread_auth"] is True
        for argument in args:
            if argument.startswith("-out="):
                Path(argument.removeprefix("-out=")).touch()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    AzureAdministrativeBootstrap(infra_dir, operator_dir).execute(**_arguments())

    changed = _arguments()
    changed["state_key"] = "different.tfstate"
    with pytest.raises(AzureAdministrativeBootstrapError, match="do not match"):
        AzureAdministrativeBootstrap(infra_dir, operator_dir).execute(**changed)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"subscription_id": "not-a-uuid"}, "subscription"),
        ({"location": "East US"}, "location"),
        ({"storage_account_name": "Bad-Storage"}, "storage account"),
        ({"state_key": "/absolute"}, "state key"),
        ({"state_allowed_ip_rule": "0.0.0.0/0"}, "allowed IP"),
        ({"acr_name": "too-short"}, "ACR"),
    ],
)
def test_azure_admin_rejects_invalid_inputs_before_terraform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    update: dict[str, str],
    message: str,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("Terraform must not run")
    )
    arguments = _arguments()
    arguments.update(update)  # type: ignore[typeddict-item]
    with pytest.raises(AzureAdministrativeBootstrapError, match=message):
        AzureAdministrativeBootstrap(infra_dir, operator_dir).execute(**arguments)


def test_azure_admin_rejects_repository_local_artifacts(tmp_path: Path) -> None:
    infra_dir, _ = _layout(tmp_path)
    with pytest.raises(AzureAdministrativeBootstrapError, match="outside"):
        AzureAdministrativeBootstrap(infra_dir, tmp_path / "checkout" / "artifacts")
