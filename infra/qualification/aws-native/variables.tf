variable "aws_account_id" {
  type        = string
  description = "Exact AWS account selected by the committed qualification authorization."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "candidate_version" {
  type        = string
  description = "Exact immutable Dander release candidate bound to this qualification run."

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+rc[0-9]+$", var.candidate_version))
    error_message = "candidate_version must be an exact Dander release candidate such as 0.9.0rc24."
  }
}

variable "region" {
  type        = string
  description = "AWS region for the disposable qualification data plane."
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]$", var.region))
    error_message = "region must be an AWS region."
  }
}

variable "name" {
  type        = string
  description = "Unique prefix for resources owned by this qualification run."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.name))
    error_message = "name must be a short lowercase AWS resource prefix."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "Private address range for the disposable three-AZ data plane."
  default     = "10.82.0.0/24"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR."
  }
}

variable "redshift_base_capacity_rpu" {
  type        = number
  description = "Smallest reviewed Redshift Serverless base capacity for this run."
  default     = 8

  validation {
    condition     = var.redshift_base_capacity_rpu == 8
    error_message = "This qualification fixture is approved only at 8 RPU."
  }
}

variable "redshift_daily_usage_limit_rpu_hours" {
  type        = number
  description = "Daily compute limit that deactivates the workgroup before the USD 3 allocation."
  default     = 5

  validation {
    condition     = var.redshift_daily_usage_limit_rpu_hours > 0 && var.redshift_daily_usage_limit_rpu_hours <= 5
    error_message = "The Redshift usage limit must be positive and no more than 5 RPU-hours."
  }
}

variable "rds_instance_class" {
  type        = string
  description = "Approved small PostgreSQL state instance class."
  default     = "db.t4g.micro"

  validation {
    condition     = var.rds_instance_class == "db.t4g.micro"
    error_message = "This qualification fixture is approved only for db.t4g.micro."
  }
}

variable "tags" {
  type        = map(string)
  description = "Additional non-sensitive resource tags."
  default     = {}
}
