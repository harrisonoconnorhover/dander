mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "11111111-1111-4111-8111-111111111111"
      tenant_id       = "22222222-2222-4222-8222-222222222222"
      client_id       = "33333333-3333-4333-8333-333333333333"
      object_id       = "44444444-4444-4444-8444-444444444444"
    }
  }
}

variables {
  subscription_id       = "11111111-1111-4111-8111-111111111111"
  location              = "eastus"
  resource_group_name   = "dander-phase6"
  storage_account_name  = "danderphase6state"
  state_container_name  = "tfstate"
  state_allowed_ip_rule = "203.0.113.10"
  acr_name              = "danderphase6"
  managed_identity_name = "dander-phase6-runtime"
}

run "hardened_stage_zero" {
  command = plan

  assert {
    condition = (
      azurerm_storage_account.terraform_state.min_tls_version == "TLS1_2" &&
      azurerm_storage_account.terraform_state.https_traffic_only_enabled &&
      !azurerm_storage_account.terraform_state.allow_nested_items_to_be_public &&
      !azurerm_storage_account.terraform_state.shared_access_key_enabled &&
      azurerm_storage_account.terraform_state.network_rules[0].default_action == "Deny"
    )
    error_message = "Terraform state must use Entra authentication, TLS, and private blobs."
  }

  assert {
    condition = (
      azurerm_storage_account.terraform_state.blob_properties[0].versioning_enabled &&
      azurerm_storage_container.terraform_state.container_access_type == "private"
    )
    error_message = "Terraform state must be versioned in a private container."
  }

  assert {
    condition = (
      !azurerm_container_registry.runtime.admin_enabled &&
      azurerm_container_registry.runtime.sku == "Basic"
    )
    error_message = "The runtime registry must disable static administrator credentials."
  }

  assert {
    condition     = azurerm_role_assignment.terraform_state_operator.role_definition_name == "Storage Blob Data Contributor"
    error_message = "The authenticated operator must be able to use the Entra-authenticated state backend."
  }
}
