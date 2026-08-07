variable "aws_region" {
  description = "AWS region for the isolated Phase 1B proof."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region name."
  }
}

variable "repository_name" {
  description = "Private ECR repository that receives the copied OCI index."
  type        = string
  default     = "dander-phase1b"

  validation {
    condition     = can(regex("^[a-z0-9]+(?:[._/-][a-z0-9]+)*$", var.repository_name))
    error_message = "repository_name must be a valid private ECR repository name."
  }
}
