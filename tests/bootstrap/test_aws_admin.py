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

    runtime_image = terraform.split('sid    = "PublishRuntimeImage"', 1)[1].split(
        'sid       = "EcrAuthorization"', 1
    )[0]
    assert '"ecr:ListTagsForResource"' in runtime_image
    assert "resources = [aws_ecr_repository.runtime.arn]" in runtime_image
    assert '"ecr:*"' not in runtime_image

    log_tag_reads = terraform.split('sid     = "InspectDanderLogGroupTags"', 1)[1].split(
        'sid       = "InspectDanderFailureQueueTags"', 1
    )[0]
    assert 'actions = ["logs:ListTagsForResource"]' in log_tag_reads
    assert "log-group:/aws/vendedlogs/states/${var.name}-*" in log_tag_reads
    assert "log-group:/dander/${var.name}*/*" in log_tag_reads

    queue_tag_reads = terraform.split('sid       = "InspectDanderFailureQueueTags"', 1)[1].split(
        'sid       = "InspectDanderFailureTopicTags"', 1
    )[0]
    assert 'actions   = ["sqs:ListQueueTags"]' in queue_tag_reads
    assert ":${var.name}*-failures" in queue_tag_reads

    topic_tag_reads = terraform.split('sid       = "InspectDanderFailureTopicTags"', 1)[1].split(
        'sid       = "ValidateStateMachineDefinition"', 1
    )[0]
    assert 'actions   = ["sns:ListTagsForResource"]' in topic_tag_reads
    assert ":${var.name}*-failures" in topic_tag_reads

    definition_validation = terraform.split('sid       = "ValidateStateMachineDefinition"', 1)[
        1
    ].split('sid       = "InspectDanderStateMachineVersions"', 1)[0]
    assert 'actions   = ["states:ValidateStateMachineDefinition"]' in definition_validation
    assert 'resources = ["*"]' in definition_validation

    state_machine_versions = terraform.split('sid       = "InspectDanderStateMachineVersions"', 1)[
        1
    ].split('sid       = "InspectDanderKmsRotation"', 1)[0]
    assert 'actions   = ["states:ListStateMachineVersions"]' in state_machine_versions
    assert (
        'resources = ["arn:${local.partition}:states:${var.region}:'
        '${var.aws_account_id}:stateMachine:${var.name}-*"]' in state_machine_versions
    )

    kms_rotation = terraform.split('sid       = "InspectDanderKmsRotation"', 1)[1].split(
        'sid    = "ManageDanderRoles"', 1
    )[0]
    assert 'actions   = ["kms:GetKeyRotationStatus"]' in kms_rotation
    assert ":key/*" in kms_rotation
    assert 'variable = "aws:ResourceTag/managed-by"' in kms_rotation
    assert 'values   = ["dander"]' in kms_rotation

    generic_reads = terraform.split('sid    = "DescribeDeploymentPrerequisites"', 1)[1].split(
        'sid     = "InspectDanderLogGroupTags"', 1
    )[0]
    for action in (
        '"logs:ListTagsForResource"',
        '"sqs:ListQueueTags"',
        '"sns:ListTagsForResource"',
        '"states:ValidateStateMachineDefinition"',
        '"states:ListStateMachineVersions"',
        '"kms:GetKeyRotationStatus"',
    ):
        assert action not in generic_reads


def test_deployment_role_scopes_d7_hosted_control_authority() -> None:
    terraform = (_REPO_ROOT / "infra/aws/bootstrap-admin/main.tf").read_text(encoding="utf-8")
    storage_policy = terraform.split('data "aws_iam_policy_document" "deployment_d7_storage"', 1)[
        1
    ].split('data "aws_iam_policy_document" "deployment_d7_provider"', 1)[0]
    provider_policy = terraform.split('data "aws_iam_policy_document" "deployment_d7_provider"', 1)[
        1
    ].split('resource "aws_iam_role_policy" "deployment_d7"', 1)[0]

    for action in (
        "cloudfront:CreateDistribution",
        "cloudfront:CreateOriginRequestPolicy",
        "ec2:CreateSecurityGroup",
        "ec2:DescribeAccountAttributes",
        "ec2:DescribeInternetGateways",
        "ec2:DescribeVpcAttribute",
        "ec2:GetManagedPrefixListEntries",
        "ecs:CreateService",
        "ecs:DescribeServiceDeployments",
        "ecs:ListServiceDeployments",
        "ecs:UpdateService",
        "elasticloadbalancing:CreateLoadBalancer",
        "elasticloadbalancing:DescribeListenerAttributes",
        "iam:ListInstanceProfilesForRole",
        "iam:ListRoleTags",
        "logs:ListTagsForResource",
    ):
        assert f'"{action}"' in provider_policy
    for action in (
        "s3:GetBucketCORS",
        "s3:GetBucketLogging",
        "s3:GetBucketObjectLockConfiguration",
        "s3:GetBucketPolicy",
        "s3:GetBucketWebsite",
        "s3:GetLifecycleConfiguration",
        "s3:GetReplicationConfiguration",
        "s3:ListBucketVersions",
        "s3:DeleteObjectVersion",
    ):
        assert f'"{action}"' in storage_policy
    assert "arn:${local.partition}:s3:::${var.name}-d7-*" in storage_policy
    assert (
        "arn:${local.partition}:logs:${var.region}:${var.aws_account_id}:log-group:"
        "/dander/${var.name}/d7/*"
    ) in provider_policy
    assert "service/${var.name}-d7-*/*" in provider_policy
    assert "service-deployment/${var.name}-d7-*/*/*" in provider_policy
    assert "role/${var.name}-d7-*" in provider_policy
    assert provider_policy.count('"ec2:CreateSecurityGroup"') == 2
    assert provider_policy.count('"ec2:AuthorizeSecurityGroupEgress"') == 2
    assert provider_policy.count('"ec2:AuthorizeSecurityGroupIngress"') == 2
    assert provider_policy.count('"ec2:CreateTags"') == 2
    assert (
        "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:security-group/*"
        in provider_policy
    )
    assert (
        provider_policy.count(
            "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:security-group-rule/*"
        )
        == 2
    )
    assert "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:vpc/*" in provider_policy
    assert 'variable = "ec2:CreateAction"' in provider_policy
    assert 'values   = ["CreateSecurityGroup"]' in provider_policy
    create_rules = provider_policy.split('sid    = "CreateD7SecurityGroupRules"', 1)[1].split(
        'sid     = "TagD7SecurityGroupRulesOnCreate"', 1
    )[0]
    tag_rules = provider_policy.split('sid     = "TagD7SecurityGroupRulesOnCreate"', 1)[1].split(
        'sid    = "ManageD7SecurityGroups"', 1
    )[0]
    for statement in (create_rules, tag_rules):
        assert 'variable = "aws:RequestTag/managed-by"' in statement
        assert 'values   = ["dander"]' in statement
        assert 'variable = "aws:RequestTag/phase"' in statement
        assert 'values   = ["d7"]' in statement
    assert 'variable = "ec2:CreateAction"' in tag_rules
    assert '"AuthorizeSecurityGroupEgress"' in tag_rules
    assert '"AuthorizeSecurityGroupIngress"' in tag_rules
    assert 'variable = "aws:RequestTag/phase"' in provider_policy
    assert 'variable = "aws:ResourceTag/phase"' in provider_policy
    assert 'values   = ["elasticloadbalancing.amazonaws.com"]' in provider_policy
    assert 'd7_state_prefix = "dander/d7/control-plane/"' in terraform
    assert 'variable = "s3:prefix"' in storage_policy
    assert 'values   = ["${local.d7_state_prefix}*"]' in storage_policy
    assert (
        'resources = ["${aws_s3_bucket.terraform_state.arn}/${local.d7_state_prefix}*"]'
        in storage_policy
    )
    for wildcard in (
        '"cloudfront:*"',
        '"ec2:*"',
        '"ecs:*"',
        '"elasticloadbalancing:*"',
        '"iam:*"',
        '"logs:*"',
        '"s3:*"',
    ):
        assert wildcard not in storage_policy
        assert wildcard not in provider_policy

    for provider_action in (
        '"cloudfront:CreateDistribution"',
        '"ec2:CreateSecurityGroup"',
        '"ecs:CreateService"',
        '"ecs:DescribeServiceDeployments"',
        '"ecs:ListServiceDeployments"',
        '"elasticloadbalancing:CreateLoadBalancer"',
        '"iam:ListInstanceProfilesForRole"',
        '"iam:ListRoleTags"',
        '"logs:ListTagsForResource"',
    ):
        assert provider_action not in storage_policy
    for storage_action in (
        '"s3:ListBucketVersions"',
        '"s3:GetObjectVersion"',
        '"s3:DeleteObjectVersion"',
    ):
        assert storage_action not in provider_policy

    state_policy = terraform.split('data "aws_iam_policy_document" "deployment"', 1)[1].split(
        'resource "aws_iam_role_policy" "deployment"', 1
    )[0]
    assert '"s3:ListBucketVersions"' not in state_policy
    assert '"s3:GetObjectVersion"' not in state_policy
    assert '"s3:DeleteObjectVersion"' not in state_policy

    assert 'resource "aws_iam_policy" "deployment_d7_provider"' in terraform
    assert 'resource "aws_iam_role_policy_attachment" "deployment_d7_provider"' in terraform
    assert 'name   = "${var.name}-d7-control-plane-provider"' in terraform
    assert "policy = data.aws_iam_policy_document.deployment_d7_storage.json" in terraform
    assert "policy = data.aws_iam_policy_document.deployment_d7_provider.json" in terraform
    assert "policy_arn = aws_iam_policy.deployment_d7_provider.arn" in terraform
    assert terraform.count("precondition {") == 2
    assert "configured inline policies exceed AWS's 10,240-character quota" in terraform
    assert "D7 provider policy exceeds AWS's 6,144-character managed-policy quota" in terraform
    assert ") <= 10240" in terraform
    assert ") <= 6144" in terraform


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
            "kms_key_id": ("arn:aws:kms:us-east-1:184463061564:alias/dander-stage-zero"),
            "region": "us-east-1",
        }
    }


def test_aws_admin_uses_govcloud_kms_alias_for_remote_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    infra_dir, operator_dir = _layout(tmp_path)
    operator_dir.mkdir()
    arguments = {
        **_arguments(tmp_path),
        "region": "us-gov-west-1",
        "state_bucket": "dander-gov-state",
        "admin_principal_arn": "arn:aws-us-gov:iam::184463061564:root",
    }
    (operator_dir / "backend.json").write_text(
        json.dumps(
            {
                "schema": "io.dander.aws-bootstrap-backend/v1",
                "bucket": arguments["state_bucket"],
                "key": arguments["state_key"],
                "region": arguments["region"],
                "dynamodb_table": arguments["lock_table"],
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
    AwsAdministrativeBootstrap(infra_dir, operator_dir).execute(**arguments)  # type: ignore[arg-type]

    backend = backends[0]["terraform"]["backend"]["s3"]  # type: ignore[index]
    assert backend["kms_key_id"] == (
        "arn:aws-us-gov:kms:us-gov-west-1:184463061564:alias/dander-stage-zero"
    )


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
