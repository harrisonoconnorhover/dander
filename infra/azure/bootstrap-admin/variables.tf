variable "subscription_id" {
  description = "Azure subscription receiving Dander stage-zero resources."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be an RFC 4122 UUID."
  }
}

variable "location" {
  description = "Azure location for the first Dander deployment."
  type        = string
  default     = "eastus"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,31}$", var.location))
    error_message = "location must be a normalized Azure location name."
  }
}

variable "resource_group_name" {
  description = "Resource group containing Azure stage-zero and runtime resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,62}[a-z0-9]$", var.resource_group_name))
    error_message = "resource_group_name must contain 3-64 lowercase letters, numbers, or hyphens."
  }
}

variable "storage_account_name" {
  description = "Globally unique Storage account used for Terraform state."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,23}$", var.storage_account_name))
    error_message = "storage_account_name must contain 3-24 lowercase letters and numbers."
  }
}

variable "state_container_name" {
  description = "Private blob container used for Terraform state."
  type        = string
  default     = "tfstate"

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$", var.state_container_name))
    error_message = "state_container_name must be a valid Azure blob container name."
  }
}

variable "state_allowed_ip_rule" {
  description = "One reviewed operator public IPv4 address for exact state access."
  type        = string

  validation {
    condition = (
      !strcontains(var.state_allowed_ip_rule, "/") &&
      length(split(".", var.state_allowed_ip_rule)) == 4 &&
      can(cidrnetmask("${var.state_allowed_ip_rule}/32"))
    )
    error_message = "state_allowed_ip_rule must be one exact IPv4 address."
  }
}

variable "acr_name" {
  description = "Globally unique Azure Container Registry name for Dander images."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{4,49}$", var.acr_name))
    error_message = "acr_name must contain 5-50 lowercase letters and numbers."
  }
}

variable "managed_identity_name" {
  description = "User-assigned identity used by Container Apps Jobs."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,62}[a-z0-9]$", var.managed_identity_name))
    error_message = "managed_identity_name must contain 3-64 lowercase letters, numbers, or hyphens."
  }
}

variable "tags" {
  description = "Additional non-secret tags applied to stage-zero resources."
  type        = map(string)
  default     = {}
}
