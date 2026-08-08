variable "aws_account_id" {
  description = "Twelve-digit AWS account receiving Dander stage-zero resources."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly twelve digits."
  }
}

variable "region" {
  description = "AWS region for state, registry, and the initial Dander deployment."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$", var.region))
    error_message = "region must be a valid AWS region name."
  }
}

variable "name" {
  description = "Stable prefix for Dander administrative resources."
  type        = string
  default     = "dander"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,23}$", var.name))
    error_message = "name must contain 2-24 lowercase letters, numbers, or hyphens."
  }
}

variable "state_bucket" {
  description = "Globally unique S3 bucket for encrypted Terraform state."
  type        = string

  validation {
    condition = (
      can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket)) &&
      !can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$", var.state_bucket))
    )
    error_message = "state_bucket must be a valid S3 bucket name."
  }
}

variable "lock_table" {
  description = "DynamoDB table used for Terraform state locking."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]{3,255}$", var.lock_table))
    error_message = "lock_table must be a valid DynamoDB table name."
  }
}

variable "ecr_repository_name" {
  description = "Private ECR repository receiving verified Dander OCI artifacts."
  type        = string
  default     = "dander"

  validation {
    condition     = can(regex("^[a-z0-9]+(?:[._/-][a-z0-9]+)*$", var.ecr_repository_name))
    error_message = "ecr_repository_name must be a valid private ECR repository name."
  }
}

variable "admin_principal_arn" {
  description = "Exact AWS principal permitted to assume the Dander deployment role."
  type        = string

  validation {
    condition     = can(regex("^arn:(?:aws|aws-us-gov):iam::[0-9]{12}:(?:root|user/[A-Za-z0-9+=,.@_/-]+|role/[A-Za-z0-9+=,.@_/-]+)$", var.admin_principal_arn))
    error_message = "admin_principal_arn must be an IAM root, user, or role ARN."
  }
}

variable "tags" {
  description = "Additional tags applied to all stage-zero resources."
  type        = map(string)
  default     = {}
}
