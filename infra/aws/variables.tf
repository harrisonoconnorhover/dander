variable "name" {
  type        = string
  description = "Stable name prefix for this Dander Fargate deployment."
  default     = "dander"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,23}$", var.name))
    error_message = "name must be 2-24 lowercase letters, numbers, or hyphens."
  }
}

variable "aws_account_id" {
  type        = string
  description = "AWS account that owns the Fargate deployment."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "region" {
  type        = string
  description = "AWS region for ECR, ECS, Scheduler, Step Functions, logs, and failure routing."
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]+$", var.region))
    error_message = "region must be a valid AWS region name."
  }
}

variable "ecr_repository_name" {
  type        = string
  description = "ECR repository that receives immutable Dander runtime manifests."
  default     = "dander"
}

variable "execution_projections" {
  description = "Validated io.dander.execution/v1 Fargate templates keyed by pipeline id."
  type        = any
}

variable "aws_native_profile" {
  description = "Existing AWS-native Redshift, staging, and Glue coordinates; null for the GCP data plane."
  type = object({
    redshift_deployment         = string
    redshift_cluster_identifier = optional(string)
    redshift_workgroup_name     = optional(string)
    redshift_database           = string
    redshift_db_user            = optional(string)
    staging_bucket              = string
    staging_prefix              = string
    glue_catalog_id             = string
    glue_database_prefix        = string
  })
  default  = null
  nullable = true
}

variable "scheduler_delivery_retry_count" {
  type        = number
  description = "EventBridge delivery retries; distinct from whole-runtime launcher attempts."
  default     = 2
}

variable "scheduler_delivery_max_age_seconds" {
  type        = number
  description = "Maximum age for an undelivered scheduled invocation."
  default     = 3600
}

variable "tags" {
  type        = map(string)
  description = "Additional non-secret AWS tags."
  default     = {}
}
