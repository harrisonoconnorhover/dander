variable "subscription_id" {
  description = "Azure subscription containing the Phase 6 runtime identity."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be an RFC 4122 UUID."
  }
}

variable "tenant_id" {
  description = "Microsoft Entra tenant issuing the managed-identity token."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", var.tenant_id))
    error_message = "tenant_id must be an RFC 4122 UUID."
  }
}

variable "azure_resource_group_name" {
  description = "Existing resource group containing the runtime managed identity."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._()/-]{1,90}$", var.azure_resource_group_name))
    error_message = "azure_resource_group_name must be a valid Azure resource-group name."
  }
}

variable "azure_managed_identity_name" {
  description = "Existing user-assigned identity attached to the Container Apps Job."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,62}[a-z0-9]$", var.azure_managed_identity_name))
    error_message = "azure_managed_identity_name must be a normalized Azure resource name."
  }
}

variable "proof_name" {
  description = "Unique deterministic name for one disposable Phase 6 federation proof."
  type        = string
  default     = "dander-phase6"

  validation {
    condition = (
      length(var.proof_name) >= 6 &&
      length(var.proof_name) <= 20 &&
      can(regex("^[a-z][a-z0-9-]*[a-z0-9]$", var.proof_name)) &&
      !startswith(var.proof_name, "gcp-")
    )
    error_message = "proof_name must be 6-20 lowercase letters, numbers, or hyphens and must not start with gcp-."
  }
}

variable "gcp_project_id" {
  description = "GCP project containing the bounded BigQuery portability target."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.gcp_project_id))
    error_message = "gcp_project_id must be a valid GCP project ID."
  }
}

variable "google_service_account_id" {
  description = "Disposable Google service account impersonated by the Azure identity."
  type        = string
  default     = "dander-phase6-azure"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.google_service_account_id))
    error_message = "google_service_account_id must be a valid service-account ID."
  }
}

variable "raw_dataset_id" {
  description = "Existing bounded BigQuery warehouse dataset used by the proof."
  type        = string
  default     = "raw"

  validation {
    condition     = length(var.raw_dataset_id) <= 1024 && can(regex("^[A-Za-z_][A-Za-z0-9_]*$", var.raw_dataset_id))
    error_message = "raw_dataset_id must be a valid BigQuery dataset ID."
  }
}

variable "metadata_dataset_id" {
  description = "Existing bounded BigQuery state dataset used by the proof."
  type        = string
  default     = "dander_meta"

  validation {
    condition     = length(var.metadata_dataset_id) <= 1024 && can(regex("^[A-Za-z_][A-Za-z0-9_]*$", var.metadata_dataset_id))
    error_message = "metadata_dataset_id must be a valid BigQuery dataset ID."
  }
}

variable "proof_secret_ids" {
  description = "Existing Secret Manager containers the proof service account may read."
  type        = set(string)

  validation {
    condition = (
      length(var.proof_secret_ids) >= 1 &&
      alltrue([for id in var.proof_secret_ids : can(regex("^[A-Za-z][A-Za-z0-9_-]{0,254}$", id))])
    )
    error_message = "proof_secret_ids must contain at least one valid Secret Manager ID."
  }
}
