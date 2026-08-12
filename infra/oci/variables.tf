variable "tenancy_id" {
  description = "OCI tenancy owning the Dander dynamic group."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.tenancy\\.oc[0-9]+\\.\\.[A-Za-z0-9]+$", var.tenancy_id))
    error_message = "tenancy_id must be an OCI tenancy OCID."
  }
}

variable "compartment_id" {
  description = "OCI compartment receiving the Dander foundation."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.compartment\\.oc[0-9]+\\.\\.[A-Za-z0-9]+$", var.compartment_id))
    error_message = "compartment_id must be an OCI compartment OCID."
  }
}

variable "region" {
  description = "OCI region for the Dander foundation."
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
}

variable "name" {
  description = "Stable prefix for Dander OCI resources."
  type        = string
  default     = "dander"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,23}$", var.name))
    error_message = "name must contain 2-24 lowercase letters, numbers, or hyphens."
  }
}

variable "dynamic_group_name" {
  description = "Tenancy-unique dynamic group for Container Instance resource principals."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9_-]{0,99}$", var.dynamic_group_name))
    error_message = "dynamic_group_name must be a valid OCI dynamic-group name."
  }
}

variable "vcn_cidr" {
  description = "Private VCN CIDR for the OCI launcher."
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition     = can(cidrhost(var.vcn_cidr, 0)) && !can(regex("^0\\.", var.vcn_cidr))
    error_message = "vcn_cidr must be a valid non-default IPv4 CIDR."
  }
}

variable "runtime_subnet_cidr" {
  description = "Private subnet CIDR for run-scoped Container Instances."
  type        = string
  default     = "10.42.1.0/24"

  validation {
    condition     = can(cidrhost(var.runtime_subnet_cidr, 0)) && !can(regex("^0\\.", var.runtime_subnet_cidr))
    error_message = "runtime_subnet_cidr must be a valid non-default IPv4 CIDR."
  }
}

variable "freeform_tags" {
  description = "Additional non-secret OCI free-form tags."
  type        = map(string)
  default     = {}
}
