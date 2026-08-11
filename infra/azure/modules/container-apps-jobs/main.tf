locals {
  container_app_environment_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.App/managedEnvironments/${var.container_app_environment_name}"
  key_vault_uri                = "https://${var.key_vault_name}.vault.azure.net"
  resource_names = {
    for id in keys(var.execution_projections) :
    id => "${substr(var.name, 0, 12)}-${substr(sha1(id), 0, 12)}"
  }
  tags = merge(var.tags, {
    component  = "azure-container-apps-jobs"
    managed-by = "dander"
  })
}

check "projections_match_selected_azure_resources" {
  assert {
    condition = alltrue([
      for projection in values(var.execution_projections) : (
        projection.launcher == "azure_container_apps" &&
        projection.workload_identity == var.managed_identity_id &&
        projection.network.placement == local.container_app_environment_id &&
        projection.extensions.azure_acr_login_server == var.acr_login_server &&
        projection.extensions.azure_managed_identity_client_id == var.managed_identity_client_id &&
        projection.extensions.azure_key_vault_uri == local.key_vault_uri
      )
    ])
    error_message = "Azure projections must match the selected registry, identity, environment, and Key Vault."
  }
}

check "secret_references_match_selected_key_vault" {
  assert {
    condition = alltrue(flatten([
      for projection in values(var.execution_projections) : [
        for binding in values(projection.secret_bindings) : (
          binding.provider == "azure_key_vault" &&
          startswith(binding.reference, "azure-kv://${local.key_vault_uri}/secrets/")
        )
      ]
    ]))
    error_message = "Azure secret references must use the selected Key Vault."
  }
}

resource "azurerm_log_analytics_workspace" "runtime" {
  name                = "${var.name}-logs"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_container_app_environment" "runtime" {
  name                           = var.container_app_environment_name
  location                       = var.location
  resource_group_name            = var.resource_group_name
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.runtime.id
  infrastructure_subnet_id       = var.infrastructure_subnet_id
  internal_load_balancer_enabled = var.infrastructure_subnet_id == null ? null : true
  zone_redundancy_enabled        = var.infrastructure_subnet_id == null ? null : false
  tags                           = local.tags
}

resource "azurerm_key_vault" "runtime" {
  name                          = var.key_vault_name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  tenant_id                     = var.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  public_network_access_enabled = true
  soft_delete_retention_days    = 90
  tags                          = local.tags

  network_acls {
    bypass         = "AzureServices"
    default_action = "Deny"
    ip_rules       = [var.key_vault_allowed_ip_rule]
  }
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = var.acr_id
  role_definition_name = "AcrPull"
  principal_id         = var.managed_identity_principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "key_vault_secrets" {
  scope                = azurerm_key_vault.runtime.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = var.managed_identity_principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_container_app_job" "pipeline" {
  for_each = var.execution_projections

  name                         = local.resource_names[each.key]
  location                     = var.location
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.runtime.id
  replica_timeout_in_seconds   = each.value.resources.deadline_seconds
  replica_retry_limit          = each.value.resources.launcher_retry_count
  tags                         = merge(local.tags, each.value.labels, { pipeline = each.key })

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  registry {
    server   = var.acr_login_server
    identity = var.managed_identity_id
  }

  dynamic "secret" {
    for_each = each.value.secret_bindings
    content {
      name                = "secret-${substr(sha1(secret.key), 0, 16)}"
      identity            = var.managed_identity_id
      key_vault_secret_id = trimprefix(secret.value.reference, "azure-kv://")
    }
  }

  template {
    container {
      name   = "runtime"
      image  = each.value.image
      cpu    = each.value.resources.cpu_millis / 1000
      memory = "${each.value.resources.memory_mib / 1024}Gi"
      args   = each.value.command

      dynamic "env" {
        for_each = each.value.environment
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = each.value.secret_bindings
        content {
          name        = env.key
          secret_name = "secret-${substr(sha1(env.key), 0, 16)}"
        }
      }
    }
  }

  dynamic "manual_trigger_config" {
    for_each = each.value.schedule.paused ? [true] : []
    content {
      parallelism              = each.value.schedule.maximum_parallelism
      replica_completion_count = each.value.schedule.task_count
    }
  }

  dynamic "schedule_trigger_config" {
    for_each = each.value.schedule.paused ? [] : [true]
    content {
      cron_expression          = each.value.schedule.expression
      parallelism              = each.value.schedule.maximum_parallelism
      replica_completion_count = each.value.schedule.task_count
    }
  }

  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.key_vault_secrets,
  ]
}

resource "azurerm_monitor_metric_alert" "failed_execution" {
  for_each = {
    for id, projection in var.execution_projections :
    id => projection if projection.observability.alert_target != null
  }

  name                = "${local.resource_names[each.key]}-failed"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_container_app_job.pipeline[each.key].id]
  description         = "Dander Container Apps Job reported a failed execution."
  severity            = 1
  frequency           = "PT1M"
  window_size         = "PT5M"
  auto_mitigate       = true
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.App/jobs"
    metric_name      = "Executions"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0

    dimension {
      name     = "state"
      operator = "Include"
      values   = ["Failed"]
    }
  }

  action {
    action_group_id = each.value.observability.alert_target
  }
}
