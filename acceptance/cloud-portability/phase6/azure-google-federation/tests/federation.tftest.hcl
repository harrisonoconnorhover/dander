mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "11111111-1111-4111-8111-111111111111"
      tenant_id       = "22222222-2222-4222-8222-222222222222"
      client_id       = "33333333-3333-4333-8333-333333333333"
      object_id       = "44444444-4444-4444-8444-444444444444"
    }
  }

  mock_data "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.ManagedIdentity/userAssignedIdentities/dander-phase6-runtime"
      client_id    = "55555555-5555-4555-8555-555555555555"
      principal_id = "66666666-6666-4666-8666-666666666666"
    }
  }
}

mock_provider "azuread" {
  mock_data "azuread_client_config" {
    defaults = {
      tenant_id = "22222222-2222-4222-8222-222222222222"
      client_id = "33333333-3333-4333-8333-333333333333"
      object_id = "44444444-4444-4444-8444-444444444444"
    }
  }

  mock_resource "azuread_application" {
    defaults = {
      id        = "/applications/77777777-7777-4777-8777-777777777777"
      client_id = "77777777-7777-4777-8777-777777777777"
    }
  }

  mock_resource "azuread_application_identifier_uri" {
    defaults = {
      identifier_uri = "api://77777777-7777-4777-8777-777777777777"
    }
  }
}

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number     = "1009770943166"
      project_id = "unit-project"
    }
  }

  mock_data "google_secret_manager_secret" {
    defaults = {
      secret_id = "source-api-token"
    }
  }

  mock_resource "google_iam_workload_identity_pool" {
    defaults = {
      name = "projects/1009770943166/locations/global/workloadIdentityPools/dander-phase6-azure"
    }
  }

  mock_resource "google_iam_workload_identity_pool_provider" {
    defaults = {
      name = "projects/1009770943166/locations/global/workloadIdentityPools/dander-phase6-azure/providers/container-apps"
    }
  }

  mock_resource "google_service_account" {
    defaults = {
      name  = "projects/unit-project/serviceAccounts/dander-phase6-azure@unit-project.iam.gserviceaccount.com"
      email = "dander-phase6-azure@unit-project.iam.gserviceaccount.com"
    }
  }
}

variables {
  subscription_id             = "11111111-1111-4111-8111-111111111111"
  tenant_id                   = "22222222-2222-4222-8222-222222222222"
  azure_resource_group_name   = "dander-phase6"
  azure_managed_identity_name = "dander-phase6-runtime"
  proof_name                  = "dander-phase6"
  gcp_project_id              = "unit-project"
  google_service_account_id   = "dander-phase6-azure"
  raw_dataset_id              = "raw"
  metadata_dataset_id         = "dander_meta"
  proof_secret_ids            = ["source-api-token"]
}

run "binds_only_the_selected_azure_identity" {
  command = apply

  assert {
    condition     = output.azure_application_id_uri == "api://77777777-7777-4777-8777-777777777777"
    error_message = "The Entra application ID URI must be stable and non-secret."
  }

  assert {
    condition     = output.google_workload_identity_audience == "//iam.googleapis.com/projects/1009770943166/locations/global/workloadIdentityPools/dander-phase6-azure/providers/container-apps"
    error_message = "The runtime audience must name the exact Azure federation provider."
  }

  assert {
    condition     = google_service_account_iam_member.azure_workload_identity.member == "principalSet://iam.googleapis.com/projects/1009770943166/locations/global/workloadIdentityPools/dander-phase6-azure/attribute.azure_object_id/66666666-6666-4666-8666-666666666666"
    error_message = "Only the selected Azure managed identity may impersonate the Google service account."
  }
}
