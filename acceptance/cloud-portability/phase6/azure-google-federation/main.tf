data "azurerm_client_config" "current" {}
data "azuread_client_config" "current" {}
data "google_project" "proof" {
  project_id = var.gcp_project_id
}

check "authenticated_azure_boundary_matches" {
  assert {
    condition = (
      lower(data.azurerm_client_config.current.subscription_id) == lower(var.subscription_id) &&
      lower(data.azurerm_client_config.current.tenant_id) == lower(var.tenant_id) &&
      lower(data.azuread_client_config.current.tenant_id) == lower(var.tenant_id)
    )
    error_message = "Authenticated Azure subscription and Entra tenant must match the reviewed proof inputs."
  }
}

data "azurerm_user_assigned_identity" "runtime" {
  name                = var.azure_managed_identity_name
  resource_group_name = var.azure_resource_group_name
}

resource "azuread_application" "google_federation" {
  display_name     = "${var.proof_name}-google-wif"
  owners           = [data.azuread_client_config.current.object_id]
  sign_in_audience = "AzureADMyOrg"

  lifecycle {
    ignore_changes = [identifier_uris]
  }
}

resource "azuread_application_identifier_uri" "google_federation" {
  application_id = azuread_application.google_federation.id
  identifier_uri = "api://${azuread_application.google_federation.client_id}"
}

resource "azuread_service_principal" "google_federation" {
  client_id                    = azuread_application.google_federation.client_id
  app_role_assignment_required = false
  owners                       = [data.azuread_client_config.current.object_id]
}

resource "google_iam_workload_identity_pool" "azure" {
  workload_identity_pool_id = "${var.proof_name}-azure"
  display_name              = "Dander Phase 6 Azure"
  description               = "Disposable keyless Container Apps to Google identity proof"
}

resource "google_iam_workload_identity_pool_provider" "azure" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.azure.workload_identity_pool_id
  workload_identity_pool_provider_id = "container-apps"
  display_name                       = "Dander Container Apps"
  description                        = "Trusts only the selected Dander Azure managed identity"

  attribute_mapping = {
    "google.subject"                  = "assertion.sub"
    "attribute.azure_object_id"       = "assertion.oid"
    "attribute.azure_tenant_id"       = "assertion.tid"
    "attribute.azure_application_uri" = "assertion.aud"
  }
  attribute_condition = "assertion.tid == '${var.tenant_id}' && assertion.oid == '${data.azurerm_user_assigned_identity.runtime.principal_id}' && assertion.aud == 'api://${azuread_application.google_federation.client_id}'"

  oidc {
    issuer_uri        = "https://sts.windows.net/${var.tenant_id}/"
    allowed_audiences = [azuread_application_identifier_uri.google_federation.identifier_uri]
  }
}

resource "google_service_account" "runtime" {
  account_id   = var.google_service_account_id
  display_name = "Dander Phase 6 Azure proof"
  description  = "Disposable GCP identity impersonated by one Azure managed identity"
}

resource "google_service_account_iam_member" "azure_workload_identity" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.azure.name}/attribute.azure_object_id/${data.azurerm_user_assigned_identity.runtime.principal_id}"
}

resource "google_project_iam_member" "bigquery_job_user" {
  project = var.gcp_project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "dataplex_catalog_editor" {
  project = var.gcp_project_id
  role    = "roles/dataplex.catalogEditor"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_bigquery_dataset_iam_member" "raw_editor" {
  project    = var.gcp_project_id
  dataset_id = var.raw_dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_bigquery_dataset_iam_member" "metadata_editor" {
  project    = var.gcp_project_id
  dataset_id = var.metadata_dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

data "google_secret_manager_secret" "proof" {
  for_each  = var.proof_secret_ids
  project   = var.gcp_project_id
  secret_id = each.value
}

resource "google_secret_manager_secret_iam_member" "proof_accessor" {
  for_each  = data.google_secret_manager_secret.proof
  project   = var.gcp_project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}
