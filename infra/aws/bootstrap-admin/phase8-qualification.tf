locals {
  phase8_qualification_prefix = "${var.name}-p8q-"
}

# Phase 8 uses the ordinary short-lived deployment identity for its disposable data plane. Keep
# this policy separate from the retained platform and D7 grants so each boundary can be reviewed
# and evolved independently.
data "aws_iam_policy_document" "deployment_phase8_qualification_infrastructure" {
  statement {
    sid    = "InspectPhase8QualificationResources"
    effect = "Allow"
    actions = [
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeInternetGateways",
      "ec2:DescribeNetworkAcls",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribePrefixLists",
      "ec2:DescribeRouteTables",
      "ec2:DescribeSecurityGroupRules",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSubnets",
      "ec2:DescribeVpcAttribute",
      "ec2:DescribeVpcEndpoints",
      "ec2:DescribeVpcs",
      "glue:GetDatabase",
      "glue:GetTable",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "rds:DescribeDBInstances",
      "rds:DescribeDBSubnetGroups",
      "redshift-serverless:GetNamespace",
      "redshift-serverless:GetUsageLimit",
      "redshift-serverless:GetWorkgroup",
      "redshift-serverless:ListNamespaces",
      "redshift-serverless:ListTagsForResource",
      "redshift-serverless:ListUsageLimits",
      "redshift-serverless:ListWorkgroups",
      "s3:ListAllMyBuckets",
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetResourcePolicy",
      "secretsmanager:ListSecretVersionIds",
      "secretsmanager:ListSecrets",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "CreateTaggedPhase8QualificationNetwork"
    effect = "Allow"
    actions = [
      "ec2:CreateInternetGateway",
      "ec2:CreateRouteTable",
      "ec2:CreateSubnet",
      "ec2:CreateVpc",
      "ec2:CreateVpcEndpoint",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/managed-by"
      values   = ["dander"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/purpose"
      values   = ["phase8-qualification"]
    }
  }

  statement {
    sid    = "CreateTaggedPhase8QualificationSecurityResources"
    effect = "Allow"
    actions = [
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:CreateSecurityGroup",
    ]
    resources = [
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:security-group/*",
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:security-group-rule/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/managed-by"
      values   = ["dander"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/purpose"
      values   = ["phase8-qualification"]
    }
  }

  statement {
    sid     = "TagPhase8QualificationNetworkOnCreate"
    effect  = "Allow"
    actions = ["ec2:CreateTags"]
    resources = [
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:internet-gateway/*",
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:route-table/*",
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:security-group/*",
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:security-group-rule/*",
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:subnet/*",
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:vpc/*",
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:vpc-endpoint/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values = [
        "AuthorizeSecurityGroupEgress",
        "AuthorizeSecurityGroupIngress",
        "CreateInternetGateway",
        "CreateRouteTable",
        "CreateSecurityGroup",
        "CreateSubnet",
        "CreateVpc",
        "CreateVpcEndpoint",
      ]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/managed-by"
      values   = ["dander"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/purpose"
      values   = ["phase8-qualification"]
    }
  }

  statement {
    sid    = "UseTaggedPhase8QualificationNetworkDependencies"
    effect = "Allow"
    actions = [
      "ec2:CreateRouteTable",
      "ec2:CreateSubnet",
      "ec2:CreateVpcEndpoint",
    ]
    resources = [
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:route-table/*",
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:vpc/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/managed-by"
      values   = ["dander"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/purpose"
      values   = ["phase8-qualification"]
    }
  }

  statement {
    sid     = "UseVpcForPhase8QualificationSecurityGroupCreation"
    effect  = "Allow"
    actions = ["ec2:CreateSecurityGroup"]
    resources = [
      "arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:vpc/*"
    ]
  }

  statement {
    sid    = "ManageTaggedPhase8QualificationNetwork"
    effect = "Allow"
    actions = [
      "ec2:AssociateRouteTable",
      "ec2:AttachInternetGateway",
      "ec2:CreateRoute",
      "ec2:DeleteInternetGateway",
      "ec2:DeleteRoute",
      "ec2:DeleteRouteTable",
      "ec2:DeleteSecurityGroup",
      "ec2:DeleteSubnet",
      "ec2:DeleteTags",
      "ec2:DeleteVpc",
      "ec2:DeleteVpcEndpoints",
      "ec2:DetachInternetGateway",
      "ec2:DisassociateRouteTable",
      "ec2:ModifySubnetAttribute",
      "ec2:ModifyVpcAttribute",
      "ec2:ModifyVpcEndpoint",
      "ec2:ReplaceRoute",
      "ec2:ReplaceRouteTableAssociation",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/managed-by"
      values   = ["dander"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/purpose"
      values   = ["phase8-qualification"]
    }
  }

  statement {
    sid    = "ManagePhase8QualificationStagingBucket"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:GetAccelerateConfiguration",
      "s3:GetBucketAcl",
      "s3:GetBucketCORS",
      "s3:GetBucketLocation",
      "s3:GetBucketLogging",
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetBucketOwnershipControls",
      "s3:GetBucketPolicy",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetReplicationConfiguration",
      "s3:GetBucketRequestPayment",
      "s3:GetBucketTagging",
      "s3:GetBucketVersioning",
      "s3:GetBucketWebsite",
      "s3:GetEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:ListBucketVersions",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketTagging",
      "s3:PutEncryptionConfiguration",
      "s3:PutLifecycleConfiguration",
    ]
    resources = [
      "arn:${local.partition}:s3:::${local.phase8_qualification_prefix}*-${var.aws_account_id}-staging"
    ]
  }

  statement {
    sid    = "ManagePhase8QualificationStagingObjects"
    effect = "Allow"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:GetObject",
      "s3:ListMultipartUploadParts",
      "s3:PutObject",
    ]
    resources = [
      "arn:${local.partition}:s3:::${local.phase8_qualification_prefix}*-${var.aws_account_id}-staging/*"
    ]
  }

  statement {
    sid    = "ManagePhase8QualificationCopyRole"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "iam:PassRole",
      "iam:PutRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = [
      "arn:${local.partition}:iam::${var.aws_account_id}:role/${local.phase8_qualification_prefix}*-redshift-copy"
    ]
  }
}

resource "aws_iam_policy" "deployment_phase8_qualification_infrastructure" {
  name   = "${var.name}-phase8-qualification-infrastructure"
  policy = data.aws_iam_policy_document.deployment_phase8_qualification_infrastructure.json
  tags   = local.tags

  lifecycle {
    precondition {
      condition = length(
        jsonencode(jsondecode(data.aws_iam_policy_document.deployment_phase8_qualification_infrastructure.json))
      ) <= 6144
      error_message = "Phase 8 qualification infrastructure policy exceeds AWS's 6,144-character managed-policy quota."
    }
  }
}

resource "aws_iam_role_policy_attachment" "deployment_phase8_qualification_infrastructure" {
  role       = aws_iam_role.deployment.name
  policy_arn = aws_iam_policy.deployment_phase8_qualification_infrastructure.arn
}

data "aws_iam_policy_document" "deployment_phase8_qualification_data" {

  statement {
    sid     = "CreateRdsServiceLinkedRole"
    effect  = "Allow"
    actions = ["iam:CreateServiceLinkedRole"]
    resources = [
      "arn:${local.partition}:iam::${var.aws_account_id}:role/aws-service-role/rds.amazonaws.com/AWSServiceRoleForRDS"
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["rds.amazonaws.com"]
    }
  }

  statement {
    sid     = "CreateRedshiftServiceLinkedRole"
    effect  = "Allow"
    actions = ["iam:CreateServiceLinkedRole"]
    resources = [
      "arn:${local.partition}:iam::${var.aws_account_id}:role/aws-service-role/redshift.amazonaws.com/AWSServiceRoleForRedshift"
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["redshift.amazonaws.com"]
    }
  }

  statement {
    sid    = "ManagePhase8QualificationDatabase"
    effect = "Allow"
    actions = [
      "rds:AddTagsToResource",
      "rds:CreateDBInstance",
      "rds:CreateDBSubnetGroup",
      "rds:DeleteDBInstance",
      "rds:DeleteDBSubnetGroup",
      "rds:ListTagsForResource",
      "rds:ModifyDBInstance",
      "rds:ModifyDBSubnetGroup",
      "rds:RemoveTagsFromResource",
    ]
    resources = [
      "arn:${local.partition}:rds:${var.region}:${var.aws_account_id}:db:${local.phase8_qualification_prefix}*",
      "arn:${local.partition}:rds:${var.region}:${var.aws_account_id}:subgrp:${local.phase8_qualification_prefix}*",
    ]
  }

  statement {
    sid    = "CreateTaggedPhase8QualificationRedshift"
    effect = "Allow"
    actions = [
      "redshift-serverless:CreateNamespace",
      "redshift-serverless:CreateWorkgroup",
      "redshift-serverless:TagResource",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/managed-by"
      values   = ["dander"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/purpose"
      values   = ["phase8-qualification"]
    }
  }

  statement {
    sid    = "ManageTaggedPhase8QualificationRedshift"
    effect = "Allow"
    actions = [
      "redshift-serverless:DeleteNamespace",
      "redshift-serverless:DeleteWorkgroup",
      "redshift-serverless:TagResource",
      "redshift-serverless:UntagResource",
      "redshift-serverless:UpdateNamespace",
      "redshift-serverless:UpdateWorkgroup",
    ]
    resources = [
      "arn:${local.partition}:redshift-serverless:${var.region}:${var.aws_account_id}:namespace/*",
      "arn:${local.partition}:redshift-serverless:${var.region}:${var.aws_account_id}:workgroup/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/managed-by"
      values   = ["dander"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/purpose"
      values   = ["phase8-qualification"]
    }
  }

  # Usage-limit resources and completed Data API statements are not taggable. Keep these grants
  # action-bounded and account/region-bound; the surrounding namespace/workgroup lifecycle stays
  # tag-scoped above.
  statement {
    sid    = "ManagePhase8QualificationRedshiftOperations"
    effect = "Allow"
    actions = [
      "redshift-data:CancelStatement",
      "redshift-data:DescribeStatement",
      "redshift-data:ExecuteStatement",
      "redshift-data:GetStatementResult",
      "redshift-serverless:CreateUsageLimit",
      "redshift-serverless:DeleteUsageLimit",
      "redshift-serverless:GetCredentials",
      "redshift-serverless:UpdateUsageLimit",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid    = "ManagePhase8QualificationGlueProjection"
    effect = "Allow"
    actions = [
      "glue:CreateDatabase",
      "glue:CreateTable",
      "glue:DeleteDatabase",
      "glue:DeleteTable",
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTags",
      "glue:TagResource",
      "glue:UpdateDatabase",
      "glue:UpdateTable",
    ]
    resources = [
      "arn:${local.partition}:glue:${var.region}:${var.aws_account_id}:catalog",
      "arn:${local.partition}:glue:${var.region}:${var.aws_account_id}:database/dander_analytics_staging",
      "arn:${local.partition}:glue:${var.region}:${var.aws_account_id}:table/dander_analytics_staging/stg_phase8_aws__posts",
    ]
  }

  statement {
    sid    = "ManagePhase8QualificationPostgresqlSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetResourcePolicy",
      "secretsmanager:GetSecretValue",
      "secretsmanager:ListSecretVersionIds",
      "secretsmanager:PutSecretValue",
      "secretsmanager:TagResource",
      "secretsmanager:UntagResource",
      "secretsmanager:UpdateSecret",
    ]
    resources = [
      "arn:${local.partition}:secretsmanager:${var.region}:${var.aws_account_id}:secret:${local.phase8_qualification_prefix}*/postgres-dsn-*"
    ]
  }
}

resource "aws_iam_policy" "deployment_phase8_qualification_data" {
  name   = "${var.name}-phase8-qualification-data"
  policy = data.aws_iam_policy_document.deployment_phase8_qualification_data.json
  tags   = local.tags
}

resource "aws_iam_role_policy_attachment" "deployment_phase8_qualification_data" {
  role       = aws_iam_role.deployment.name
  policy_arn = aws_iam_policy.deployment_phase8_qualification_data.arn
}
