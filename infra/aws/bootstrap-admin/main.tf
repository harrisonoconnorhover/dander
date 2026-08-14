locals {
  partition       = startswith(var.region, "us-gov-") ? "aws-us-gov" : "aws"
  d7_state_prefix = "dander/d7/control-plane/"
  tags = merge(var.tags, {
    component  = "aws-stage-zero"
    managed-by = "dander"
  })
}

data "aws_caller_identity" "current" {}

check "authenticated_account_matches_target" {
  assert {
    condition     = data.aws_caller_identity.current.account_id == var.aws_account_id
    error_message = "Authenticated AWS account does not match aws_account_id."
  }
}

resource "aws_kms_key" "stage_zero" {
  description             = "Encrypt Dander AWS state, lock, and registry artifacts"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "stage_zero" {
  name          = "alias/${var.name}-stage-zero"
  target_key_id = aws_kms_key.stage_zero.key_id
}

resource "aws_s3_bucket" "terraform_state" {
  bucket = var.state_bucket
  tags   = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.stage_zero.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_dynamodb_table" "terraform_locks" {
  name         = var.lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.stage_zero.arn
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_ecr_repository" "runtime" {
  name                 = var.ecr_repository_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.stage_zero.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "runtime" {
  repository = aws_ecr_repository.runtime.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Remove untagged promotion residue after fourteen days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

data "aws_iam_policy_document" "deployment_assume" {
  statement {
    sid     = "ExactOperator"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.admin_principal_arn]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = [var.admin_principal_arn]
    }
  }
}

resource "aws_iam_role" "deployment" {
  name                 = "${var.name}-bootstrap"
  max_session_duration = 3600
  assume_role_policy   = data.aws_iam_policy_document.deployment_assume.json
  tags                 = local.tags
}

data "aws_iam_policy_document" "deployment" {
  statement {
    sid    = "TerraformStateBucket"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:GetBucketVersioning",
      "s3:ListBucket",
    ]
    resources = [aws_s3_bucket.terraform_state.arn]
  }

  statement {
    sid    = "TerraformStateObjects"
    effect = "Allow"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.terraform_state.arn}/*"]
  }

  statement {
    sid    = "TerraformStateLocks"
    effect = "Allow"
    actions = [
      "dynamodb:DeleteItem",
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
    ]
    resources = [aws_dynamodb_table.terraform_locks.arn]
  }

  statement {
    sid    = "PublishRuntimeImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:ListImages",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.runtime.arn]
  }

  statement {
    sid       = "EcrAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "OperateDanderControllers"
    effect = "Allow"
    actions = [
      "states:ListExecutions",
      "states:StartExecution",
    ]
    resources = [
      "arn:${local.partition}:states:${var.region}:${var.aws_account_id}:stateMachine:${var.name}-*"
    ]
  }

  statement {
    sid    = "ObserveAndCancelDanderExecutions"
    effect = "Allow"
    actions = [
      "states:DescribeExecution",
      "states:GetExecutionHistory",
      "states:StopExecution",
    ]
    resources = [
      "arn:${local.partition}:states:${var.region}:${var.aws_account_id}:execution:${var.name}-*:*"
    ]
  }

  statement {
    sid    = "ReadDanderTaskLogs"
    effect = "Allow"
    actions = [
      "logs:DescribeLogStreams",
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
    ]
    resources = [
      "arn:${local.partition}:logs:${var.region}:${var.aws_account_id}:log-group:/dander/${var.name}/*:*"
    ]
  }

  statement {
    sid    = "UseStageZeroEncryptionKey"
    effect = "Allow"
    actions = [
      "kms:CreateGrant",
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo",
    ]
    resources = [aws_kms_key.stage_zero.arn]
  }

  statement {
    sid    = "DescribeDeploymentPrerequisites"
    effect = "Allow"
    actions = [
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSubnets",
      "ec2:DescribeVpcs",
      "ecs:DescribeClusters",
      "ecs:DescribeTaskDefinition",
      "ecs:ListTagsForResource",
      "events:DescribeRule",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListRolePolicies",
      "kms:DescribeKey",
      "kms:GetKeyPolicy",
      "kms:ListAliases",
      "kms:ListResourceTags",
      "logs:DescribeLogGroups",
      "scheduler:GetSchedule",
      "sns:GetTopicAttributes",
      "sqs:GetQueueAttributes",
      "states:DescribeStateMachine",
      "states:ListTagsForResource",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageDanderRoles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:PassRole",
      "iam:PutRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = [
      "arn:${local.partition}:iam::${var.aws_account_id}:role/${var.name}-*",
      "arn:${local.partition}:iam::${var.aws_account_id}:role/dander-*",
    ]
  }

  statement {
    sid    = "ManageDanderDeployment"
    effect = "Allow"
    actions = [
      "ecs:CreateCluster",
      "ecs:DeleteCluster",
      "ecs:DeregisterTaskDefinition",
      "ecs:RegisterTaskDefinition",
      "ecs:TagResource",
      "ecs:UntagResource",
      "events:DeleteRule",
      "events:PutRule",
      "events:PutTargets",
      "events:RemoveTargets",
      "events:TagResource",
      "events:UntagResource",
      "kms:CreateAlias",
      "kms:CreateKey",
      "kms:DeleteAlias",
      "kms:EnableKeyRotation",
      "kms:PutKeyPolicy",
      "kms:ScheduleKeyDeletion",
      "kms:TagResource",
      "kms:UntagResource",
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:TagLogGroup",
      "logs:UntagLogGroup",
      "scheduler:CreateSchedule",
      "scheduler:DeleteSchedule",
      "scheduler:TagResource",
      "scheduler:UntagResource",
      "scheduler:UpdateSchedule",
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:SetTopicAttributes",
      "sns:TagResource",
      "sns:UntagResource",
      "sqs:CreateQueue",
      "sqs:DeleteQueue",
      "sqs:SetQueueAttributes",
      "sqs:TagQueue",
      "sqs:UntagQueue",
      "states:CreateStateMachine",
      "states:DeleteStateMachine",
      "states:TagResource",
      "states:UntagResource",
      "states:UpdateStateMachine",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deployment" {
  name   = "dander-platform-administration"
  role   = aws_iam_role.deployment.id
  policy = data.aws_iam_policy_document.deployment.json
}

data "aws_iam_policy_document" "deployment_d7" {
  statement {
    sid       = "ListD7TerraformStateVersions"
    effect    = "Allow"
    actions   = ["s3:ListBucketVersions"]
    resources = [aws_s3_bucket.terraform_state.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.d7_state_prefix}*"]
    }
  }

  statement {
    sid    = "ManageD7TerraformStateVersions"
    effect = "Allow"
    actions = [
      "s3:DeleteObjectVersion",
      "s3:GetObjectVersion",
    ]
    resources = ["${aws_s3_bucket.terraform_state.arn}/${local.d7_state_prefix}*"]
  }

  statement {
    sid    = "InspectD7ProviderResources"
    effect = "Allow"
    actions = [
      "cloudfront:GetCachePolicy",
      "cloudfront:GetDistribution",
      "cloudfront:GetDistributionConfig",
      "cloudfront:GetOriginRequestPolicy",
      "cloudfront:ListCachePolicies",
      "cloudfront:ListDistributions",
      "cloudfront:ListOriginRequestPolicies",
      "cloudfront:ListTagsForResource",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeManagedPrefixLists",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribeSecurityGroupRules",
      "elasticloadbalancing:DescribeListeners",
      "elasticloadbalancing:DescribeLoadBalancerAttributes",
      "elasticloadbalancing:DescribeLoadBalancers",
      "elasticloadbalancing:DescribeRules",
      "elasticloadbalancing:DescribeTargetGroupAttributes",
      "elasticloadbalancing:DescribeTargetGroups",
      "elasticloadbalancing:DescribeTargetHealth",
      "elasticloadbalancing:DescribeTags",
      "ecs:DescribeServices",
      "ecs:DescribeTasks",
      "ecs:ListServices",
      "ecs:ListTaskDefinitions",
      "ecs:ListTasks",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageD7Buckets"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:GetAccelerateConfiguration",
      "s3:GetBucketAcl",
      "s3:GetBucketLocation",
      "s3:GetBucketOwnershipControls",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetBucketRequestPayment",
      "s3:GetBucketTagging",
      "s3:GetBucketVersioning",
      "s3:GetEncryptionConfiguration",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:ListBucketVersions",
      "s3:PutBucketOwnershipControls",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketTagging",
      "s3:PutBucketVersioning",
      "s3:PutEncryptionConfiguration",
    ]
    resources = ["arn:${local.partition}:s3:::${var.name}-d7-*"]
  }

  statement {
    sid    = "ManageD7BucketObjects"
    effect = "Allow"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:GetObject",
      "s3:GetObjectAttributes",
      "s3:GetObjectVersion",
      "s3:ListMultipartUploadParts",
      "s3:PutObject",
    ]
    resources = ["arn:${local.partition}:s3:::${var.name}-d7-*/*"]
  }

  statement {
    sid    = "CreateD7CloudFrontResources"
    effect = "Allow"
    actions = [
      "cloudfront:CreateCachePolicy",
      "cloudfront:CreateDistribution",
      "cloudfront:CreateOriginRequestPolicy",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageD7CloudFrontResources"
    effect = "Allow"
    actions = [
      "cloudfront:DeleteCachePolicy",
      "cloudfront:DeleteDistribution",
      "cloudfront:DeleteOriginRequestPolicy",
      "cloudfront:TagResource",
      "cloudfront:UntagResource",
      "cloudfront:UpdateCachePolicy",
      "cloudfront:UpdateDistribution",
      "cloudfront:UpdateOriginRequestPolicy",
    ]
    resources = [
      "arn:${local.partition}:cloudfront::${var.aws_account_id}:cache-policy/*",
      "arn:${local.partition}:cloudfront::${var.aws_account_id}:distribution/*",
      "arn:${local.partition}:cloudfront::${var.aws_account_id}:origin-request-policy/*",
    ]
  }

  statement {
    sid    = "CreateD7LoadBalancingResources"
    effect = "Allow"
    actions = [
      "elasticloadbalancing:CreateListener",
      "elasticloadbalancing:CreateLoadBalancer",
      "elasticloadbalancing:CreateRule",
      "elasticloadbalancing:CreateTargetGroup",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageD7LoadBalancingResources"
    effect = "Allow"
    actions = [
      "elasticloadbalancing:AddTags",
      "elasticloadbalancing:DeleteListener",
      "elasticloadbalancing:DeleteLoadBalancer",
      "elasticloadbalancing:DeleteRule",
      "elasticloadbalancing:DeleteTargetGroup",
      "elasticloadbalancing:ModifyListener",
      "elasticloadbalancing:ModifyLoadBalancerAttributes",
      "elasticloadbalancing:ModifyRule",
      "elasticloadbalancing:ModifyTargetGroup",
      "elasticloadbalancing:ModifyTargetGroupAttributes",
      "elasticloadbalancing:RemoveTags",
      "elasticloadbalancing:SetSecurityGroups",
      "elasticloadbalancing:SetSubnets",
    ]
    resources = [
      "arn:${local.partition}:elasticloadbalancing:${var.region}:${var.aws_account_id}:listener/app/${var.name}-d7-*/*/*",
      "arn:${local.partition}:elasticloadbalancing:${var.region}:${var.aws_account_id}:listener-rule/app/${var.name}-d7-*/*/*/*",
      "arn:${local.partition}:elasticloadbalancing:${var.region}:${var.aws_account_id}:loadbalancer/app/${var.name}-d7-*/*",
      "arn:${local.partition}:elasticloadbalancing:${var.region}:${var.aws_account_id}:targetgroup/${var.name}-d7-*/*",
    ]
  }

  statement {
    sid    = "CreateD7SecurityGroups"
    effect = "Allow"
    actions = [
      "ec2:CreateSecurityGroup",
      "ec2:CreateTags",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/managed-by"
      values   = ["dander"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/phase"
      values   = ["d7"]
    }
  }

  statement {
    sid    = "ManageD7SecurityGroups"
    effect = "Allow"
    actions = [
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:DeleteSecurityGroup",
      "ec2:DeleteTags",
      "ec2:ModifySecurityGroupRules",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
    ]
    resources = ["arn:${local.partition}:ec2:${var.region}:${var.aws_account_id}:security-group/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/managed-by"
      values   = ["dander"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/phase"
      values   = ["d7"]
    }
  }

  statement {
    sid    = "ManageD7EcsServices"
    effect = "Allow"
    actions = [
      "ecs:CreateService",
      "ecs:DeleteService",
      "ecs:StopTask",
      "ecs:UpdateService",
    ]
    resources = [
      "arn:${local.partition}:ecs:${var.region}:${var.aws_account_id}:service/${var.name}-d7-*/*",
      "arn:${local.partition}:ecs:${var.region}:${var.aws_account_id}:task/${var.name}-d7-*/*",
    ]
  }

  statement {
    sid     = "CreateElasticLoadBalancingServiceRole"
    effect  = "Allow"
    actions = ["iam:CreateServiceLinkedRole"]
    resources = [
      "arn:${local.partition}:iam::${var.aws_account_id}:role/aws-service-role/elasticloadbalancing.amazonaws.com/AWSServiceRoleForElasticLoadBalancing"
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["elasticloadbalancing.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "deployment_d7" {
  name   = "dander-d7-control-plane"
  role   = aws_iam_role.deployment.id
  policy = data.aws_iam_policy_document.deployment_d7.json
}
