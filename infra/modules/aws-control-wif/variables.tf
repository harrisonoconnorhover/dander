variable "project_id" {
  type        = string
  description = "GCP project containing the Cloud Run Jobs selected by AWS Control."
}

variable "aws_control_role_arn" {
  type        = string
  description = "Exact ECS task-role ARN allowed to federate into GCP."

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_-]+$", var.aws_control_role_arn))
    error_message = "aws_control_role_arn must be one unpathed commercial-AWS IAM role ARN."
  }
}

variable "runtime_service_account_names" {
  type        = map(string)
  description = "Cloud Run runtime service-account resource names keyed by stable pipeline id."
  default     = {}
}
