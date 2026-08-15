"""Contracts for the stage-zero authority used by disposable AWS qualification."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parents[2]


def test_redshift_copy_role_trusts_provisioned_and_serverless_services() -> None:
    qualification = (_ROOT / "infra/qualification/aws-native/main.tf").read_text(encoding="utf-8")

    assert '"redshift-serverless.amazonaws.com"' in qualification
    assert '"redshift.amazonaws.com"' in qualification


def test_deployment_role_has_action_bounded_phase8_qualification_policies() -> None:
    policy = (_ROOT / "infra/aws/bootstrap-admin/phase8-qualification.tf").read_text(
        encoding="utf-8"
    )

    for action in (
        "ec2:CreateVpc",
        "ec2:DeleteVpc",
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "iam:CreateRole",
        "iam:PassRole",
        "rds:CreateDBInstance",
        "rds:DeleteDBInstance",
        "redshift-serverless:CreateNamespace",
        "redshift-serverless:CreateUsageLimit",
        "redshift-serverless:DeleteWorkgroup",
        "redshift-data:ExecuteStatement",
        "glue:CreateDatabase",
        "glue:DeleteTable",
        "secretsmanager:CreateSecret",
        "secretsmanager:DeleteSecret",
    ):
        assert f'"{action}"' in policy

    for service in (
        "ec2",
        "glue",
        "iam",
        "rds",
        "redshift-data",
        "redshift-serverless",
        "s3",
        "secretsmanager",
    ):
        assert f'"{service}:*"' not in policy

    assert "${local.phase8_qualification_prefix}*-${var.aws_account_id}-staging" in policy
    assert "role/${local.phase8_qualification_prefix}*-redshift-copy" in policy
    assert "db:${local.phase8_qualification_prefix}*" in policy
    assert "secret:${local.phase8_qualification_prefix}*/postgres-dsn-*" in policy
    assert policy.count('variable = "aws:RequestTag/purpose"') == 2
    assert policy.count('variable = "aws:ResourceTag/purpose"') == 2
    assert 'values   = ["phase8-qualification"]' in policy

    assert 'resource "aws_iam_policy" "deployment_phase8_qualification_infrastructure"' in policy
    assert 'resource "aws_iam_policy" "deployment_phase8_qualification_data"' in policy
    assert policy.count('resource "aws_iam_role_policy_attachment"') == 2
    assert 'resource "aws_iam_role_policy"' not in policy
