locals {
  partition = startswith(var.region, "us-gov-") ? "aws-us-gov" : "aws"
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
