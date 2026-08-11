variable "subscription_id" {
  description = "Azure subscription that owns the selected deployment."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be an RFC 4122 UUID."
  }
}

variable "location" {
  description = "Azure location for Container Apps resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,31}$", var.location))
    error_message = "location must be a normalized Azure location name."
  }
}

variable "name" {
  description = "Stable name prefix for this Dander deployment."
  type        = string
  default     = "dander"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,22}[a-z0-9]$", var.name))
    error_message = "name must contain 3-24 lowercase letters, numbers, or hyphens."
  }
}

variable "resource_group_name" {
  description = "Existing stage-zero resource group."
  type        = string
}

variable "container_app_environment_name" {
  description = "Container Apps managed environment to create."
  type        = string
}

variable "acr_name" {
  description = "Existing stage-zero Azure Container Registry name."
  type        = string
}

variable "key_vault_name" {
  description = "Key Vault to create for declared secret containers."
  type        = string
}

variable "key_vault_allowed_ip_rule" {
  description = "One reviewed operator public IPv4 address for exact Key Vault data access."
  type        = string

  validation {
    condition = (
      !strcontains(var.key_vault_allowed_ip_rule, "/") &&
      length(split(".", var.key_vault_allowed_ip_rule)) == 4 &&
      can(cidrnetmask("${var.key_vault_allowed_ip_rule}/32"))
    )
    error_message = "key_vault_allowed_ip_rule must be one exact IPv4 address."
  }
}

variable "managed_identity_name" {
  description = "Existing stage-zero user-assigned managed identity name."
  type        = string
}

variable "execution_projections" {
  description = "Validated io.dander.execution/v1 Azure templates keyed by pipeline id."
  type        = any
}

variable "create_jobs" {
  description = "Create jobs and alerts after their referenced Key Vault secrets have been seeded."
  type        = bool
  default     = true
}

variable "infrastructure_subnet_id" {
  description = "Optional existing delegated subnet for the Container Apps environment; required for Key Vault references and configured with the Microsoft.KeyVault service endpoint."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.infrastructure_subnet_id == null ||
      can(regex("^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/[Mm]icrosoft\\.[Nn]etwork/virtualNetworks/[^/]+/subnets/[^/]+$", var.infrastructure_subnet_id))
    )
    error_message = "infrastructure_subnet_id must be an Azure virtual-network subnet resource id."
  }
}

variable "tags" {
  description = "Additional non-secret Azure tags."
  type        = map(string)
  default     = {}
}
