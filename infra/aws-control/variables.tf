variable "aws_account_id" {
  type        = string
  description = "Twelve-digit AWS account receiving the disposable D7 profile."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain twelve digits."
  }
}

variable "region" {
  type        = string
  description = "Single AWS region for the D7 profile."

  validation {
    condition     = can(regex("^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$", var.region))
    error_message = "region must be a valid AWS region."
  }
}

variable "name" {
  type        = string
  description = "Stable administrative prefix; resources add the d7 component."
  default     = "dander"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,23}$", var.name))
    error_message = "name must contain 2-24 lowercase letters, numbers, or hyphens."
  }
}

variable "deployment_role_arn" {
  type        = string
  description = "Reviewed short-lived role used for this Terraform root."

  validation {
    condition     = can(regex("^arn:(?:aws|aws-us-gov):iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", var.deployment_role_arn))
    error_message = "deployment_role_arn must be an AWS IAM role ARN."
  }
}

variable "graph_bucket" {
  type        = string
  description = "Globally unique disposable versioned S3 GraphStore bucket."

  validation {
    condition = (
      can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.graph_bucket)) &&
      startswith(var.graph_bucket, "${var.name}-d7-") &&
      !strcontains(var.graph_bucket, "..")
    )
    error_message = "graph_bucket must be a valid bucket under the reviewed D7 prefix."
  }
}

variable "ecr_repository_url" {
  type        = string
  description = "Existing retained ECR repository receiving the accepted application images."

  validation {
    condition = can(regex(
      "^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/[a-z0-9]+(?:[._/-][a-z0-9]+)*$",
      var.ecr_repository_url,
    ))
    error_message = "ecr_repository_url must be a private ECR repository URL."
  }
}

variable "vpc_id" {
  type        = string
  description = "Existing VPC selected for the disposable services."

  validation {
    condition     = can(regex("^vpc-[0-9a-f]{8,17}$", var.vpc_id))
    error_message = "vpc_id must be an AWS VPC id."
  }
}

variable "subnet_ids" {
  type        = list(string)
  description = "At least two existing public subnets in distinct availability zones."

  validation {
    condition = (
      length(distinct(var.subnet_ids)) >= 2 &&
      alltrue([for value in var.subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", value))])
    )
    error_message = "subnet_ids must contain at least two distinct AWS subnet ids."
  }
}

variable "foundation_only" {
  type        = bool
  description = "Create only the provider foundation needed to learn the CloudFront origin."
  default     = false
}

variable "cloudfront_distribution_id" {
  type        = string
  description = "Distribution id observed from the reviewed foundation apply."
  default     = null
  nullable    = true
}

variable "cloudfront_domain" {
  type        = string
  description = "Provider-issued CloudFront domain observed from the foundation apply."
  default     = null
  nullable    = true
}

variable "dander_image" {
  type        = string
  description = "Immutable active or rollback Dander image in the retained ECR repository."
  default     = null
  nullable    = true
}

variable "druff_image" {
  type        = string
  description = "Immutable active or rollback Druff image in the retained ECR repository."
  default     = null
  nullable    = true
}

variable "control_args" {
  type        = list(string)
  description = "Exact D6-derived command for the Dander image entrypoint."
  default     = []
}

variable "control_oidc_json" {
  type        = string
  description = "Validated non-secret hosted OIDC configuration JSON."
  default     = ""
  sensitive   = true
}

variable "graph_store_json" {
  type        = string
  description = "Validated credential-free S3 GraphStore locator JSON."
  default     = ""
  sensitive   = true
}

variable "platforms_config_yaml" {
  type        = string
  description = "Validated non-secret platform manifest selected by hosted execution plans."
  default     = ""
  sensitive   = true

  validation {
    condition     = length(var.platforms_config_yaml) <= 32768
    error_message = "platforms_config_yaml must not exceed 32 KiB."
  }
}

variable "bootstrap_json" {
  type        = string
  description = "Public Druff bootstrap descriptor JSON."
  default     = ""
  sensitive   = true
}

variable "druff_caddyfile" {
  type        = string
  description = "Reviewed Caddy configuration for the immutable Druff export."
  default     = ""
  sensitive   = true
}

variable "execution_plan_json" {
  type        = map(string)
  description = "Canonical execution plans written to the Control config volume by revision."
  default     = {}
  sensitive   = true

  validation {
    condition = (
      length(var.execution_plan_json) <= 100 &&
      alltrue([for revision, value in var.execution_plan_json :
        can(regex("^[0-9a-f]{64}$", revision)) && value != ""
      ])
    )
    error_message = "execution_plan_json must contain at most 100 non-empty SHA-keyed plans."
  }
}

variable "trigger_spec_json" {
  type        = map(string)
  description = "Canonical scheduled TriggerSpecs written to the Control config volume by id."
  default     = {}
  sensitive   = true

  validation {
    condition = (
      length(var.trigger_spec_json) <= 100 &&
      alltrue([for trigger_id, value in var.trigger_spec_json :
        can(regex("^[a-z0-9][a-z0-9_-]{0,62}$", trigger_id)) && value != ""
      ])
    )
    error_message = "trigger_spec_json must contain at most 100 non-empty portable trigger ids."
  }
}

variable "control_schedules" {
  type = map(object({
    expression    = string
    time_zone     = string
    plan_revision = string
    enabled       = bool
    message       = string
  }))
  description = "EventBridge Scheduler projections for Control-owned trigger occurrences."
  default     = {}

  validation {
    condition = (
      length(var.control_schedules) <= 100 &&
      alltrue([for trigger_id, schedule in var.control_schedules :
        can(regex("^[a-z0-9][a-z0-9_-]{0,62}$", trigger_id)) &&
        can(regex("^(cron|rate|at)\\(.+\\)$", schedule.expression)) &&
        schedule.time_zone != "" &&
        can(regex("^[0-9a-f]{64}$", schedule.plan_revision)) &&
        schedule.message != ""
      ])
    )
    error_message = "control_schedules must contain at most 100 complete Scheduler projections."
  }
}

variable "control_fargate_bindings" {
  type = map(object({
    execution_arn_prefix = string
    log_group_arn        = string
    state_machine_arn    = string
  }))
  description = "Exact existing Fargate resources that Control may operate for registered plans."
  default     = {}

  validation {
    condition = (
      length(var.control_fargate_bindings) <= 100 &&
      alltrue([for revision, binding in var.control_fargate_bindings :
        can(regex("^[0-9a-f]{64}$", revision)) &&
        startswith(binding.execution_arn_prefix, "arn:") &&
        endswith(binding.execution_arn_prefix, ":") &&
        startswith(binding.log_group_arn, "arn:") &&
        startswith(binding.state_machine_arn, "arn:")
      ])
    )
    error_message = "control_fargate_bindings must contain at most 100 complete SHA-keyed bindings."
  }
}

variable "control_cloud_run_plan_revisions" {
  type        = set(string)
  description = "Registered execution-plan revisions operated through GCP Cloud Run."
  default     = []

  validation {
    condition = (
      length(var.control_cloud_run_plan_revisions) <= 100 &&
      alltrue([for revision in var.control_cloud_run_plan_revisions :
        can(regex("^[0-9a-f]{64}$", revision))
      ])
    )
    error_message = "control_cloud_run_plan_revisions must contain at most 100 SHA revisions."
  }
}

variable "gcp_control_service_account" {
  type        = string
  description = "GCP service account impersonated by the AWS Control task; empty disables GCP."
  default     = ""

  validation {
    condition = var.gcp_control_service_account == "" || can(regex(
      "^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\\.iam\\.gserviceaccount\\.com$",
      var.gcp_control_service_account,
    ))
    error_message = "gcp_control_service_account must be empty or a service-account email."
  }
}

variable "gcp_wif_audience" {
  type        = string
  description = "Google AWS workload-identity-provider audience; empty disables GCP."
  default     = ""

  validation {
    condition = var.gcp_wif_audience == "" || can(regex(
      "^//iam\\.googleapis\\.com/projects/[0-9]{6,20}/locations/global/workloadIdentityPools/[a-z][a-z0-9-]{3,31}/providers/[a-z][a-z0-9-]{3,31}$",
      var.gcp_wif_audience,
    ))
    error_message = "gcp_wif_audience must be empty or an exact workload provider audience."
  }
}
