variable "aws_region" {
  description = "AWS region for the isolated Fargate task."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region name."
  }
}

variable "gcp_project_id" {
  description = "Disposable GCP project containing the bounded proof table."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.gcp_project_id))
    error_message = "gcp_project_id must be a valid GCP project ID."
  }
}

variable "ecr_image" {
  description = "Immutable ECR OCI-index reference copied from staging GAR."
  type        = string

  validation {
    condition = can(regex(
      "^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$",
      var.ecr_image,
    ))
    error_message = "ecr_image must be an immutable private-ECR sha256 reference."
  }
}

variable "proof_dataset" {
  description = "Existing BigQuery dataset the probe may read."
  type        = string
  default     = "raw"

  validation {
    condition     = length(var.proof_dataset) <= 1024 && can(regex("^[A-Za-z_][A-Za-z0-9_]*$", var.proof_dataset))
    error_message = "proof_dataset must be a valid BigQuery dataset ID."
  }
}

variable "proof_table" {
  description = "Existing bounded BigQuery table the probe counts twice."
  type        = string
  default     = "salesforce_accounts"

  validation {
    condition     = length(var.proof_table) <= 1024 && can(regex("^[A-Za-z_][A-Za-z0-9_]*$", var.proof_table))
    error_message = "proof_table must be a valid BigQuery table ID."
  }
}

variable "cpu_architecture" {
  description = "Fargate architecture selected from the copied OCI index."
  type        = string
  default     = "ARM64"

  validation {
    condition     = contains(["ARM64", "X86_64"], var.cpu_architecture)
    error_message = "cpu_architecture must be ARM64 or X86_64."
  }
}
