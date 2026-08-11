data "azurerm_client_config" "current" {}

check "authenticated_subscription_matches_target" {
  assert {
    condition     = data.azurerm_client_config.current.subscription_id == var.subscription_id
    error_message = "Authenticated Azure subscription does not match subscription_id."
  }
}

data "azurerm_resource_group" "dander" {
  name = var.resource_group_name
}

data "azurerm_container_registry" "runtime" {
  name                = var.acr_name
  resource_group_name = data.azurerm_resource_group.dander.name
}

data "azurerm_user_assigned_identity" "runtime" {
  name                = var.managed_identity_name
  resource_group_name = data.azurerm_resource_group.dander.name
}

module "container_apps_jobs" {
  source = "./modules/container-apps-jobs"

  name                            = var.name
  subscription_id                 = var.subscription_id
  tenant_id                       = data.azurerm_client_config.current.tenant_id
  location                        = var.location
  resource_group_name             = data.azurerm_resource_group.dander.name
  container_app_environment_name  = var.container_app_environment_name
  acr_id                          = data.azurerm_container_registry.runtime.id
  acr_login_server                = data.azurerm_container_registry.runtime.login_server
  key_vault_name                  = var.key_vault_name
  key_vault_allowed_ip_rule       = var.key_vault_allowed_ip_rule
  key_vault_operator_principal_id = data.azurerm_client_config.current.object_id
  managed_identity_id             = data.azurerm_user_assigned_identity.runtime.id
  managed_identity_client_id      = data.azurerm_user_assigned_identity.runtime.client_id
  managed_identity_principal_id   = data.azurerm_user_assigned_identity.runtime.principal_id
  execution_projections           = var.create_jobs ? var.execution_projections : {}
  infrastructure_subnet_id        = var.infrastructure_subnet_id
  tags                            = var.tags
}
