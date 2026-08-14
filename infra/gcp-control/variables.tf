variable "project_id" {
  type        = string
  description = "GCP project for the disposable D7 control-plane profile."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid Google Cloud project id."
  }
}

variable "project_number" {
  type        = string
  description = "Read-only project number used to derive Cloud Run URLs before apply."

  validation {
    condition     = can(regex("^[0-9]{6,20}$", var.project_number))
    error_message = "project_number must contain only digits."
  }
}

variable "region" {
  type        = string
  description = "Single Cloud Run and GCS region."

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]$", var.region))
    error_message = "region must be a valid Google Cloud region."
  }
}

variable "bootstrap_service_account" {
  type        = string
  description = "Existing reviewed infrastructure identity impersonated for this root."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\\.iam\\.gserviceaccount\\.com$", var.bootstrap_service_account))
    error_message = "bootstrap_service_account must be a Google service-account email."
  }
}

variable "control_service_name" {
  type        = string
  description = "Cloud Run and service-account name for Dander Control."
  default     = "dander-control-d7"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.control_service_name))
    error_message = "control_service_name must be a 6-30 character DNS label."
  }
}

variable "druff_service_name" {
  type        = string
  description = "Cloud Run and service-account name for Druff."
  default     = "druff-control-d7"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.druff_service_name))
    error_message = "druff_service_name must be a 6-30 character DNS label."
  }
}

variable "graph_bucket" {
  type        = string
  description = "Globally unique disposable GCS GraphStore bucket."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$", var.graph_bucket)) && !strcontains(var.graph_bucket, "..")
    error_message = "graph_bucket must be a valid GCS bucket name."
  }
}

variable "dander_image" {
  type        = string
  description = "Immutable Dander image for the selected active or rollback projection."

  validation {
    condition     = can(regex("^[a-z0-9.-]+/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$", var.dander_image))
    error_message = "dander_image must use an immutable sha256 digest."
  }
}

variable "druff_image" {
  type        = string
  description = "Immutable Druff image for the selected active or rollback projection."

  validation {
    condition     = can(regex("^[a-z0-9.-]+/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$", var.druff_image))
    error_message = "druff_image must use an immutable sha256 digest."
  }
}

variable "control_args" {
  type        = list(string)
  description = "Exact D6-derived command arguments for the Dander image entrypoint."

  validation {
    condition = jsonencode(var.control_args) == jsonencode([
      "control", "serve",
      "--host", "0.0.0.0",
      "--port", "8770",
      "--oidc-config", "/etc/dander/oidc/control-oidc.json",
      "--graph-store-config", "/etc/dander/graph-store/control-graph-store.json",
    ])
    error_message = "control_args must match the exact D6 GCP Control projection."
  }
}

variable "control_oidc_json" {
  type        = string
  description = "Validated non-secret hosted OIDC configuration JSON."
  sensitive   = true
}

variable "graph_store_json" {
  type        = string
  description = "Validated credential-free GCS GraphStore locator JSON."
  sensitive   = true
}

variable "bootstrap_json" {
  type        = string
  description = "Public Druff bootstrap descriptor JSON."
  sensitive   = true
}

variable "druff_caddyfile" {
  type        = string
  description = "Reviewed Caddy configuration that serves bootstrap and the immutable export."
  sensitive   = true
}
