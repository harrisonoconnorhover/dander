output "container_app_environment_id" {
  description = "Container Apps environment containing Dander jobs."
  value       = module.container_apps_jobs.container_app_environment_id
}

output "key_vault_uri" {
  description = "Key Vault URI referenced by Dander jobs."
  value       = module.container_apps_jobs.key_vault_uri
}

output "key_vault_operator_principal_id" {
  description = "Authenticated operator principal allowed to create and rotate proof secrets."
  value       = module.container_apps_jobs.key_vault_operator_principal_id
}

output "jobs" {
  description = "Container Apps Job names and ids keyed by pipeline."
  value       = module.container_apps_jobs.jobs
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace receiving Container Apps environment logs."
  value       = module.container_apps_jobs.log_analytics_workspace_id
}
