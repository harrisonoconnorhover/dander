variable "name" {
  type        = string
  description = "Stable Dander deployment prefix."
}

variable "subscription_id" {
  type        = string
  description = "Azure subscription owning the deployment."
}

variable "tenant_id" {
  type        = string
  description = "Entra tenant used by Key Vault RBAC."
}

variable "location" {
  type        = string
  description = "Azure location for runtime resources."
}

variable "resource_group_name" {
  type        = string
  description = "Existing resource group for runtime resources."
}

variable "container_app_environment_name" {
  type        = string
  description = "Container Apps managed environment name."
}

variable "acr_id" {
  type        = string
  description = "Existing Azure Container Registry resource id."
}

variable "acr_login_server" {
  type        = string
  description = "Existing Azure Container Registry login server."
}

variable "key_vault_name" {
  type        = string
  description = "Key Vault name for declared secret containers."
}

variable "key_vault_allowed_ip_rule" {
  type        = string
  description = "One reviewed operator public IPv4 address for Key Vault data access."
}

variable "managed_identity_id" {
  type        = string
  description = "User-assigned managed identity resource id."
}

variable "managed_identity_client_id" {
  type        = string
  description = "User-assigned managed identity client id."
}

variable "managed_identity_principal_id" {
  type        = string
  description = "User-assigned managed identity principal id."
}

variable "execution_projections" {
  type        = any
  description = "Validated Azure execution templates keyed by pipeline."
}

variable "infrastructure_subnet_id" {
  type        = string
  description = "Optional existing delegated subnet for the managed environment."
  default     = null
  nullable    = true
}

variable "tags" {
  type        = map(string)
  description = "Additional non-secret Azure tags."
  default     = {}
}
