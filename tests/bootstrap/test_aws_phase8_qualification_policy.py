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
        "s3:DeleteObjectVersion",
        "s3:ListBucketVersions",
        "iam:CreateRole",
        "iam:CreateServiceLinkedRole",
        "iam:PassRole",
        "rds:CreateDBInstance",
        "rds:DeleteDBInstance",
        "redshift-serverless:CreateNamespace",
        "redshift-serverless:CreateUsageLimit",
        "redshift-serverless:DeleteWorkgroup",
        "redshift-serverless:GetCredentials",
        "redshift-serverless:TagResource",
        "redshift-data:ExecuteStatement",
        "glue:CreateDatabase",
        "glue:DeleteTable",
        "glue:GetTags",
        "glue:TagResource",
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
    assert 'sid     = "CreateRedshiftServiceLinkedRole"' in policy
    assert "redshift.amazonaws.com/AWSServiceRoleForRedshift" in policy
    assert 'values   = ["redshift.amazonaws.com"]' in policy
    assert policy.count('variable = "aws:RequestTag/purpose"') == 4
    assert policy.count('variable = "aws:ResourceTag/purpose"') == 4
    assert 'values   = ["phase8-qualification"]' in policy

    general_network_create = policy.split('sid    = "CreateTaggedPhase8QualificationNetwork"', 1)[
        1
    ].split('sid    = "CreateTaggedPhase8QualificationSecurityResources"', 1)[0]
    for security_action in (
        '"ec2:AuthorizeSecurityGroupEgress"',
        '"ec2:AuthorizeSecurityGroupIngress"',
        '"ec2:CreateSecurityGroup"',
        '"ec2:CreateTags"',
    ):
        assert security_action not in general_network_create

    security_create = policy.split(
        'sid    = "CreateTaggedPhase8QualificationSecurityResources"', 1
    )[1].split('sid     = "TagPhase8QualificationNetworkOnCreate"', 1)[0]
    assert '"ec2:CreateSecurityGroup"' in security_create
    assert '"ec2:AuthorizeSecurityGroupIngress"' in security_create
    assert '"ec2:AuthorizeSecurityGroupEgress"' in security_create
    assert "security-group/*" in security_create
    assert "security-group-rule/*" in security_create

    create_tags = policy.split('sid     = "TagPhase8QualificationNetworkOnCreate"', 1)[1].split(
        'sid    = "UseTaggedPhase8QualificationNetworkDependencies"', 1
    )[0]
    assert policy.count('"ec2:CreateTags"') == 1
    assert 'resources = ["*"]' not in create_tags
    assert 'variable = "ec2:CreateAction"' in create_tags
    for resource_type in (
        "internet-gateway",
        "route-table",
        "security-group",
        "security-group-rule",
        "subnet",
        "vpc",
        "vpc-endpoint",
    ):
        assert f":{resource_type}/*" in create_tags
    for create_action in (
        "AuthorizeSecurityGroupEgress",
        "AuthorizeSecurityGroupIngress",
        "CreateInternetGateway",
        "CreateRouteTable",
        "CreateSecurityGroup",
        "CreateSubnet",
        "CreateVpc",
        "CreateVpcEndpoint",
    ):
        assert f'"{create_action}"' in create_tags

    tagged_network_dependencies = policy.split(
        'sid    = "UseTaggedPhase8QualificationNetworkDependencies"', 1
    )[1].split('sid     = "UseVpcForPhase8QualificationSecurityGroupCreation"', 1)[0]
    for action in (
        "ec2:CreateRouteTable",
        "ec2:CreateSubnet",
        "ec2:CreateVpcEndpoint",
    ):
        assert f'"{action}"' in tagged_network_dependencies
    assert ":vpc/*" in tagged_network_dependencies
    assert ":route-table/*" in tagged_network_dependencies
    assert "RequestTag" not in tagged_network_dependencies
    assert 'variable = "aws:ResourceTag/managed-by"' in tagged_network_dependencies
    assert 'variable = "aws:ResourceTag/purpose"' in tagged_network_dependencies

    security_group_vpc_dependency = policy.split(
        'sid     = "UseVpcForPhase8QualificationSecurityGroupCreation"', 1
    )[1].split('sid    = "UseTaggedPhase8QualificationSecurityGroupForRules"', 1)[0]
    assert '"ec2:CreateSecurityGroup"' in security_group_vpc_dependency
    assert ":vpc/*" in security_group_vpc_dependency
    assert "RequestTag" not in security_group_vpc_dependency

    tagged_security_group_dependency = policy.split(
        'sid    = "UseTaggedPhase8QualificationSecurityGroupForRules"', 1
    )[1].split('sid    = "ManageTaggedPhase8QualificationNetwork"', 1)[0]
    assert '"ec2:AuthorizeSecurityGroupIngress"' in tagged_security_group_dependency
    assert '"ec2:AuthorizeSecurityGroupEgress"' in tagged_security_group_dependency
    assert ":security-group/*" in tagged_security_group_dependency
    assert "security-group-rule" not in tagged_security_group_dependency
    assert "RequestTag" not in tagged_security_group_dependency
    assert 'variable = "aws:ResourceTag/managed-by"' in tagged_security_group_dependency
    assert 'variable = "aws:ResourceTag/purpose"' in tagged_security_group_dependency

    assert "table/dander_analytics_staging/*" in policy
    assert "userDefinedFunction/dander_analytics_staging/*" in policy

    assert 'resource "aws_iam_policy" "deployment_phase8_qualification_infrastructure"' in policy
    assert 'resource "aws_iam_policy" "deployment_phase8_qualification_data"' in policy
    assert policy.count('resource "aws_iam_role_policy_attachment"') == 2
    assert 'resource "aws_iam_role_policy"' not in policy
    assert "Phase 8 qualification infrastructure policy exceeds AWS's" in policy
    assert ") <= 6144" in policy


def test_qualification_serializes_redshift_assumerole_lockdown_before_copy_grant() -> None:
    qualification = (_ROOT / "infra/qualification/aws-native/main.tf").read_text(encoding="utf-8")

    lockdown = qualification.split(
        'resource "aws_redshiftdata_statement" "runtime_assumerole_lockdown"', 1
    )[1].split('resource "aws_redshiftdata_statement" "runtime_copy"', 1)[0]
    copy_grant = qualification.split('resource "aws_redshiftdata_statement" "runtime_copy"', 1)[
        1
    ].split('resource "aws_redshiftserverless_usage_limit" "compute"', 1)[0]

    assert 'sql            = "REVOKE ASSUMEROLE ON ALL FROM PUBLIC FOR ALL"' in lockdown
    assert "depends_on = [aws_redshiftdata_statement.runtime_ddl]" in lockdown
    assert "depends_on = [aws_redshiftdata_statement.runtime_assumerole_lockdown]" in copy_grant
