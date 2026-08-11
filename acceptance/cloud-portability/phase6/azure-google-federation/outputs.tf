output "azure_application_id_uri" {
  description = "Non-secret Entra audience requested by the Container Apps managed identity."
  value       = azuread_application_identifier_uri.google_federation.identifier_uri
}

output "azure_managed_identity_client_id" {
  description = "User-assigned managed identity client ID selected by the runtime."
  value       = data.azurerm_user_assigned_identity.runtime.client_id
}

output "azure_managed_identity_principal_id" {
  description = "Object ID pinned by the Google provider condition and IAM grant."
  value       = data.azurerm_user_assigned_identity.runtime.principal_id
}

output "google_service_account_email" {
  description = "Disposable Google service account impersonated by the Azure identity."
  value       = google_service_account.runtime.email
}

output "google_workload_identity_audience" {
  description = "Non-secret audience consumed by Dander's Azure launcher projection."
  value       = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.azure.name}"
}

output "google_workload_identity_provider_name" {
  description = "Full Google workload identity provider resource name."
  value       = google_iam_workload_identity_pool_provider.azure.name
}
