variable "tenancy_id" {
  description = "OCI tenancy receiving Dander stage-zero resources."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.tenancy\\.oc[0-9]+\\.\\.[A-Za-z0-9]+$", var.tenancy_id))
    error_message = "tenancy_id must be an OCI tenancy OCID."
  }
}

variable "compartment_id" {
  description = "Existing OCI compartment receiving Dander resources."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.compartment\\.oc[0-9]+\\.\\.[A-Za-z0-9]+$", var.compartment_id))
    error_message = "compartment_id must be an OCI compartment OCID."
  }
}

variable "region" {
  description = "OCI region for state, registry, and the initial deployment."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(?:-[a-z0-9]+)+-[1-9][0-9]*$", var.region))
    error_message = "region must be a normalized OCI region identifier."
  }
}

variable "config_file_profile" {
  description = "Short-lived OCI SecurityToken profile used by Terraform."
  type        = string
  default     = "DEFAULT"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", var.config_file_profile))
    error_message = "config_file_profile must be a valid OCI profile name."
  }
}

variable "state_bucket_name" {
  description = "Private, versioned Object Storage bucket for Terraform state."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{1,254}$", var.state_bucket_name))
    error_message = "state_bucket_name contains unsupported characters."
  }
}

variable "repository_name" {
  description = "Private immutable OCIR repository receiving verified Dander artifacts."
  type        = string
  default     = "dander/runtime"

  validation {
    condition     = can(regex("^[a-z0-9]+(?:[._/-][a-z0-9]+)*$", var.repository_name))
    error_message = "repository_name must be a valid OCIR repository path."
  }
}

variable "freeform_tags" {
  description = "Additional non-secret OCI free-form tags."
  type        = map(string)
  default     = {}
}
