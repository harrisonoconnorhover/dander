mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "184463061564"
      arn        = "arn:aws:iam::184463061564:root"
      user_id    = "184463061564"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

variables {
  aws_account_id      = "184463061564"
  region              = "us-east-1"
  name                = "dander"
  state_bucket        = "dander-184463061564-state"
  lock_table          = "dander-terraform-locks"
  ecr_repository_name = "dander"
  admin_principal_arn = "arn:aws:iam::184463061564:root"
}

override_resource {
  target = aws_kms_key.stage_zero
  values = {
    arn = "arn:aws:kms:us-east-1:184463061564:key/00000000-0000-0000-0000-000000000000"
  }
}

override_resource {
  target = aws_iam_policy.deployment_d7_provider
  values = {
    arn = "arn:aws:iam::184463061564:policy/dander-d7-control-plane-provider"
  }
}

override_resource {
  target = aws_iam_policy.deployment_phase8_qualification_infrastructure
  values = {
    arn = "arn:aws:iam::184463061564:policy/dander-phase8-qualification-infrastructure"
  }
}

override_resource {
  target = aws_iam_policy.deployment_phase8_qualification_data
  values = {
    arn = "arn:aws:iam::184463061564:policy/dander-phase8-qualification-data"
  }
}

run "hardened_stage_zero" {
  command = apply

  assert {
    condition = (
      aws_s3_bucket_public_access_block.terraform_state.block_public_acls &&
      aws_s3_bucket_public_access_block.terraform_state.block_public_policy &&
      aws_s3_bucket_public_access_block.terraform_state.ignore_public_acls &&
      aws_s3_bucket_public_access_block.terraform_state.restrict_public_buckets
    )
    error_message = "Terraform state must block every form of public access."
  }

  assert {
    condition = (
      aws_kms_key.stage_zero.enable_key_rotation &&
      one(one(aws_s3_bucket_server_side_encryption_configuration.terraform_state.rule).apply_server_side_encryption_by_default).sse_algorithm == "aws:kms"
    )
    error_message = "Stage-zero artifacts must use a rotating customer-managed encryption key."
  }

  assert {
    condition     = aws_s3_bucket_versioning.terraform_state.versioning_configuration[0].status == "Enabled"
    error_message = "Terraform state versioning must remain enabled."
  }

  assert {
    condition = (
      aws_dynamodb_table.terraform_locks.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.terraform_locks.point_in_time_recovery[0].enabled
    )
    error_message = "The lock table must be on-demand and recoverable."
  }

  assert {
    condition = (
      aws_ecr_repository.runtime.image_tag_mutability == "IMMUTABLE" &&
      aws_ecr_repository.runtime.image_scanning_configuration[0].scan_on_push
    )
    error_message = "The runtime repository must reject mutable tags and scan pushes."
  }

  assert {
    condition = (
      aws_iam_role_policy_attachment.deployment_phase8_qualification_infrastructure.role == aws_iam_role.deployment.name &&
      aws_iam_role_policy_attachment.deployment_phase8_qualification_data.role == aws_iam_role.deployment.name &&
      aws_iam_policy.deployment_phase8_qualification_infrastructure.name == "dander-phase8-qualification-infrastructure" &&
      aws_iam_policy.deployment_phase8_qualification_data.name == "dander-phase8-qualification-data"
    )
    error_message = "The short-lived deployment role must carry the isolated Phase 8 qualification policies."
  }
}
