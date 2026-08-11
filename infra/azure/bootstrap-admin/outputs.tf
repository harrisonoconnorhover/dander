output "resource_group_name" {
  description = "Resource group containing the Azure deployment."
  value       = azurerm_resource_group.dander.name
}

output "state_storage_account_name" {
  description = "Storage account containing remote Terraform state."
  value       = azurerm_storage_account.terraform_state.name
}

output "state_container_name" {
  description = "Blob container containing remote Terraform state."
  value       = azurerm_storage_container.terraform_state.name
}

output "runtime_registry_id" {
  description = "Azure Container Registry resource id."
  value       = azurerm_container_registry.runtime.id
}

output "runtime_registry_login_server" {
  description = "Azure Container Registry login server."
  value       = azurerm_container_registry.runtime.login_server
}

output "runtime_identity_id" {
  description = "User-assigned managed identity resource id."
  value       = azurerm_user_assigned_identity.runtime.id
}

output "runtime_identity_client_id" {
  description = "User-assigned managed identity client id required by the launcher config."
  value       = azurerm_user_assigned_identity.runtime.client_id
}

output "runtime_identity_principal_id" {
  description = "User-assigned managed identity principal id used for role assignments."
  value       = azurerm_user_assigned_identity.runtime.principal_id
}
