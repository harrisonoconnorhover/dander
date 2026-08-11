output "container_app_environment_id" {
  description = "Container Apps environment containing Dander jobs."
  value       = azurerm_container_app_environment.runtime.id
}

output "key_vault_uri" {
  description = "Key Vault URI used by versionless job secret references."
  value       = trimsuffix(azurerm_key_vault.runtime.vault_uri, "/")
}

output "key_vault_operator_principal_id" {
  description = "Operator principal allowed to create and rotate Key Vault secrets."
  value       = azurerm_role_assignment.key_vault_operator.principal_id
}

output "jobs" {
  description = "Container Apps Job names and ids keyed by pipeline."
  value = {
    for id, job in azurerm_container_app_job.pipeline : id => {
      id   = job.id
      name = job.name
    }
  }
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace receiving environment logs."
  value       = azurerm_log_analytics_workspace.runtime.id
}
