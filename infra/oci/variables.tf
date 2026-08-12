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

variable "object_storage_namespace" {
  description = "OCI Object Storage namespace used for non-secret projections and run records."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9_-]{0,99}$", var.object_storage_namespace))
    error_message = "object_storage_namespace must be a normalized OCI namespace."
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

variable "controller_dynamic_group_name" {
  description = "Tenancy-unique dynamic group for lifecycle Function resource principals."
  type        = string
  default     = "dander_phase7_controller"

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9_-]{0,99}$", var.controller_dynamic_group_name))
    error_message = "controller_dynamic_group_name must be a valid OCI dynamic-group name."
  }
}

variable "scheduler_dynamic_group_name" {
  description = "Tenancy-unique dynamic group for Dander Resource Schedules."
  type        = string
  default     = "dander_phase7_scheduler"

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9_-]{0,99}$", var.scheduler_dynamic_group_name))
    error_message = "scheduler_dynamic_group_name must be a valid OCI dynamic-group name."
  }
}

variable "controller_image" {
  description = "Immutable-tagged OCI Functions controller image in same-region OCIR."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = var.controller_image == null || can(regex(
      "^[a-z0-9.-]+/[a-z0-9_-]+/[a-z0-9._/-]+:[A-Za-z0-9._-]+$",
      var.controller_image,
    ))
    error_message = "controller_image must be a tagged OCIR image."
  }
}

variable "controller_image_digest" {
  description = "Exact sha256 digest bound to the OCI Functions controller image tag."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.controller_image_digest == null || can(regex("^sha256:[0-9a-f]{64}$", var.controller_image_digest))
    error_message = "controller_image_digest must be an immutable sha256 digest."
  }
}

variable "controller_memory_mib" {
  description = "Memory assigned to each lifecycle Function."
  type        = number
  default     = 512

  validation {
    condition     = contains([128, 256, 512, 1024, 2048], var.controller_memory_mib)
    error_message = "controller_memory_mib must be a supported bounded Functions allocation."
  }
}

variable "execution_projections" {
  description = "Validated OCI Container Instances templates keyed by pipeline id."
  type        = map(any)
  default     = {}

  validation {
    condition = alltrue([
      for id, projection in var.execution_projections : (
        can(regex("^[A-Za-z][A-Za-z0-9_-]{0,63}$", id)) &&
        projection.schema == "io.dander.execution/v1" &&
        projection.launcher == "oci_container_instances" &&
        projection.pipeline_id == id &&
        can(regex("@sha256:[0-9a-f]{64}$", projection.image)) &&
        projection.resources.runtime_retry_count == 0 &&
        projection.resources.deadline_seconds >= 1 &&
        projection.resources.deadline_seconds <= 3300 &&
        projection.resources.launcher_retry_count >= 0 &&
        projection.resources.launcher_retry_count <= 10 &&
        projection.schedule.task_count == 1 &&
        projection.schedule.maximum_parallelism == 1 &&
        projection.schedule.time_zone == "UTC" &&
        projection.network.extensions.oci_assign_public_ip == "false" &&
        projection.extensions.oci_restart_policy == "NEVER"
      )
    ])
    error_message = "execution_projections must satisfy the bounded OCI lifecycle contract."
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
