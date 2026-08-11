locals {
  tags = merge(var.tags, {
    component  = "azure-stage-zero"
    managed-by = "dander"
  })
}

data "azurerm_client_config" "current" {}

check "authenticated_subscription_matches_target" {
  assert {
    condition     = data.azurerm_client_config.current.subscription_id == var.subscription_id
    error_message = "Authenticated Azure subscription does not match subscription_id."
  }
}

resource "azurerm_resource_group" "dander" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_storage_account" "terraform_state" {
  name                            = var.storage_account_name
  resource_group_name             = azurerm_resource_group.dander.name
  location                        = azurerm_resource_group.dander.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  access_tier                     = "Hot"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  public_network_access_enabled   = true
  shared_access_key_enabled       = false
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true

    container_delete_retention_policy {
      days = 14
    }

    delete_retention_policy {
      days = 14
    }
  }

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
    ip_rules       = [var.state_allowed_ip_rule]
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "terraform_state" {
  name                  = var.state_container_name
  storage_account_id    = azurerm_storage_account.terraform_state.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [azurerm_role_assignment.terraform_state_operator]
}

resource "azurerm_role_assignment" "terraform_state_operator" {
  scope                = azurerm_storage_account.terraform_state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_container_registry" "runtime" {
  name                          = var.acr_name
  resource_group_name           = azurerm_resource_group.dander.name
  location                      = azurerm_resource_group.dander.location
  sku                           = "Basic"
  admin_enabled                 = false
  public_network_access_enabled = true
  tags                          = local.tags
}

resource "azurerm_user_assigned_identity" "runtime" {
  name                = var.managed_identity_name
  resource_group_name = azurerm_resource_group.dander.name
  location            = azurerm_resource_group.dander.location
  tags                = local.tags
}
