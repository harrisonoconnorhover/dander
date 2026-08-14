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
