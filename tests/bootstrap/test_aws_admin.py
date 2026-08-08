"""AWS stage-zero saved-plan and state-migration coverage."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from typing import TypedDict

import pytest

from dander.bootstrap import AwsAdministrativeBootstrap, AwsAdministrativeBootstrapError

_REPO_ROOT = Path(__file__).parents[2]


class _Arguments(TypedDict):
    aws_account_id: str
    region: str
    state_bucket: str
    state_key: str
    lock_table: str
    ecr_repository_name: str
    admin_principal_arn: str
    aws_profile: str
    name: str


def _arguments(tmp_path: Path) -> _Arguments:
    del tmp_path
    return {
        "aws_account_id": "184463061564",
        "region": "us-east-1",
        "state_bucket": "dander-184463061564-state",
        "state_key": "dander/aws/bootstrap-admin/terraform.tfstate",
        "lock_table": "dander-terraform-locks",
        "ecr_repository_name": "dander",
        "admin_principal_arn": "arn:aws:iam::184463061564:root",
        "aws_profile": "dander-phase1b",
        "name": "dander",
    }


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    infra_dir = tmp_path / "checkout" / "infra" / "aws" / "bootstrap-admin"
    infra_dir.mkdir(parents=True)
    (infra_dir / "main.tf").write_text('resource "terraform_data" "unit" {}\n')
    return infra_dir, tmp_path / "operator"


def test_deployment_role_scopes_fargate_operations_to_dander_resources() -> None:
    terraform = (_REPO_ROOT / "infra/aws/bootstrap-admin/main.tf").read_text(encoding="utf-8")

    for action in (
        "states:StartExecution",
        "states:ListExecutions",
        "states:DescribeExecution",
        "states:GetExecutionHistory",
        "states:StopExecution",
        "logs:FilterLogEvents",
    ):
        assert f'"{action}"' in terraform
    assert "stateMachine:${var.name}-*" in terraform
    assert "execution:${var.name}-*:*" in terraform
    assert "log-group:/dander/${var.name}/*:*" in terraform
    assert '"states:*"' not in terraform
    assert '"logs:*"' not in terraform


def test_aws_admin_plan_uses_secured_local_state_without_applying(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    commands: list[tuple[str, ...]] = []
    backends: list[dict[str, object]] = []
    environments: list[dict[str, str]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        env: dict[str, str],
        umask: int,
    ) -> subprocess.CompletedProcess[str]:
        assert check and umask == 0o077
        commands.append(args)
        environments.append(env)
        backends.append(json.loads((cwd / "backend.tf.json").read_text(encoding="utf-8")))
        for argument in args:
            if argument.startswith("-out="):
                Path(argument.removeprefix("-out=")).touch(mode=0o644)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    plan = AwsAdministrativeBootstrap(infra_dir, operator_dir).execute(**_arguments(tmp_path))

    assert plan == operator_dir / "dander-aws-admin-bootstrap.tfplan"
    assert stat.S_IMODE(plan.stat().st_mode) == 0o600
    assert commands[0][:2] == ("terraform", "init")
    assert commands[1][:2] == ("terraform", "plan")
    assert not any(command[:2] == ("terraform", "apply") for command in commands)
    backend = backends[0]["terraform"]["backend"]  # type: ignore[index]
    assert backend == {"local": {"path": str(operator_dir / "terraform.tfstate")}}
    assert all(environment["AWS_PROFILE"] == "dander-phase1b" for environment in environments)
    assert all(
        environment["TF_DATA_DIR"] == str(operator_dir / "terraform-data")
        for environment in environments
    )


def test_aws_admin_plan_removes_stale_terraform_source_from_private_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    workspace = operator_dir / "terraform-workspace"
    workspace.mkdir(parents=True)
    stale = workspace / "removed-resource.tf"
    stale.write_text('resource "terraform_data" "stale" {}\n', encoding="utf-8")

    def fake_run(
        args: tuple[str, ...], *, cwd: Path, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        assert not stale.exists()
        for argument in args:
            if argument.startswith("-out="):
                Path(argument.removeprefix("-out=")).touch()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    AwsAdministrativeBootstrap(infra_dir, operator_dir).execute(**_arguments(tmp_path))

    assert not stale.exists()


def test_aws_admin_apply_uses_saved_plan_then_migrates_state(
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
    bootstrap = AwsAdministrativeBootstrap(infra_dir, operator_dir)
    plan = bootstrap.execute(**_arguments(tmp_path))
    commands.clear()
    backend_types.clear()

    applied = bootstrap.apply_saved_plan(**_arguments(tmp_path))

    assert applied == plan
    assert commands[0][:2] == ("terraform", "init")
    assert commands[1] == ("terraform", "apply", "-input=false", str(plan))
    assert commands[2] == (
        "terraform",
        "init",
        "-migrate-state",
        "-force-copy",
        "-input=false",
    )
    assert backend_types == ["local", "local", "s3"]
    record = json.loads((operator_dir / "backend.json").read_text(encoding="utf-8"))
    assert record == {
        "schema": "io.dander.aws-bootstrap-backend/v1",
        "bucket": "dander-184463061564-state",
        "key": "dander/aws/bootstrap-admin/terraform.tfstate",
        "region": "us-east-1",
        "dynamodb_table": "dander-terraform-locks",
    }


def test_aws_admin_subsequent_plan_uses_migrated_s3_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    operator_dir.mkdir()
    (operator_dir / "backend.json").write_text(
        json.dumps(
            {
                "schema": "io.dander.aws-bootstrap-backend/v1",
                "bucket": "dander-184463061564-state",
                "key": "dander/aws/bootstrap-admin/terraform.tfstate",
                "region": "us-east-1",
                "dynamodb_table": "dander-terraform-locks",
            }
        ),
        encoding="utf-8",
    )
    backends: list[dict[str, object]] = []

    def fake_run(
        args: tuple[str, ...], *, cwd: Path, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        backends.append(json.loads((cwd / "backend.tf.json").read_text(encoding="utf-8")))
        for argument in args:
            if argument.startswith("-out="):
                Path(argument.removeprefix("-out=")).touch()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    AwsAdministrativeBootstrap(infra_dir, operator_dir).execute(**_arguments(tmp_path))

    assert backends[0]["terraform"]["backend"] == {  # type: ignore[index]
        "s3": {
            "bucket": "dander-184463061564-state",
            "dynamodb_table": "dander-terraform-locks",
            "encrypt": True,
            "key": "dander/aws/bootstrap-admin/terraform.tfstate",
            "region": "us-east-1",
        }
    }


def test_aws_admin_failed_state_migration_preserves_local_recovery_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)

    def fake_run(
        args: tuple[str, ...], *, cwd: Path, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del cwd, kwargs
        if "-migrate-state" in args:
            raise subprocess.CalledProcessError(1, args)
        for argument in args:
            if argument.startswith("-out="):
                Path(argument.removeprefix("-out=")).touch()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    bootstrap = AwsAdministrativeBootstrap(infra_dir, operator_dir)
    bootstrap.execute(**_arguments(tmp_path))

    with pytest.raises(AwsAdministrativeBootstrapError, match="init failed"):
        bootstrap.apply_saved_plan(**_arguments(tmp_path))

    backend = json.loads(
        (operator_dir / "terraform-workspace" / "backend.tf.json").read_text(encoding="utf-8")
    )
    assert backend["terraform"]["backend"] == {
        "local": {"path": str(operator_dir / "terraform.tfstate")}
    }
    assert not (operator_dir / "backend.json").exists()


def test_aws_admin_rejects_mismatched_backend_inputs_before_terraform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    operator_dir.mkdir()
    (operator_dir / "backend.json").write_text(
        json.dumps(
            {
                "schema": "io.dander.aws-bootstrap-backend/v1",
                "bucket": "different-state-bucket",
                "key": "dander/aws/bootstrap-admin/terraform.tfstate",
                "region": "us-east-1",
                "dynamodb_table": "dander-terraform-locks",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Terraform must not run"),
    )

    with pytest.raises(AwsAdministrativeBootstrapError, match="do not match"):
        AwsAdministrativeBootstrap(infra_dir, operator_dir).execute(**_arguments(tmp_path))


def test_aws_admin_rejects_a_symlinked_local_state_before_terraform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    operator_dir.mkdir()
    (operator_dir / "terraform.tfstate").symlink_to(tmp_path / "unrelated")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Terraform must not run"),
    )

    with pytest.raises(AwsAdministrativeBootstrapError, match="must not be a symlink"):
        AwsAdministrativeBootstrap(infra_dir, operator_dir).execute(**_arguments(tmp_path))


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"aws_account_id": "123"}, "account"),
        ({"state_bucket": "bad bucket"}, "bucket"),
        ({"state_key": "/absolute"}, "state key"),
        ({"admin_principal_arn": "arn:aws:iam::999999999999:root"}, "principal"),
        ({"aws_profile": "bad profile"}, "profile"),
    ],
)
def test_aws_admin_rejects_invalid_inputs(
    tmp_path: Path,
    override: dict[str, object],
    message: str,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    arguments = {**_arguments(tmp_path), **override}

    with pytest.raises(AwsAdministrativeBootstrapError, match=message):
        AwsAdministrativeBootstrap(infra_dir, operator_dir).execute(**arguments)  # type: ignore[arg-type]
