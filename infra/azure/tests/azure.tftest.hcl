mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "11111111-1111-4111-8111-111111111111"
      tenant_id       = "22222222-2222-4222-8222-222222222222"
      client_id       = "33333333-3333-4333-8333-333333333333"
      object_id       = "44444444-4444-4444-8444-444444444444"
    }
  }

  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6"
      name     = "dander-phase6"
      location = "eastus"
    }
  }

  mock_data "azurerm_container_registry" {
    defaults = {
      id           = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.ContainerRegistry/registries/danderphase6"
      login_server = "danderphase6.azurecr.io"
    }
  }

  mock_data "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.ManagedIdentity/userAssignedIdentities/dander-phase6-runtime"
      client_id    = "33333333-3333-4333-8333-333333333333"
      principal_id = "44444444-4444-4444-8444-444444444444"
    }
  }

  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.App/managedEnvironments/dander-phase6-env"
    }
  }

  mock_resource "azurerm_key_vault" {
    defaults = {
      id        = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.KeyVault/vaults/dander-phase6-kv"
      vault_uri = "https://dander-phase6-kv.vault.azure.net/"
    }
  }
}

variables {
  subscription_id                = "11111111-1111-4111-8111-111111111111"
  location                       = "eastus"
  name                           = "dander"
  resource_group_name            = "dander-phase6"
  container_app_environment_name = "dander-phase6-env"
  acr_name                       = "danderphase6"
  key_vault_name                 = "dander-phase6-kv"
  key_vault_allowed_ip_rule      = "203.0.113.10"
  managed_identity_name          = "dander-phase6-runtime"
  execution_projections = {
    warehouse_fixture = {
      launcher          = "azure_container_apps"
      image             = "danderphase6.azurecr.io/dander/runtime@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      command           = ["runtime", "execute", "--contract", "io.dander.runtime/v1", "--pipeline", "warehouse_fixture", "--platform", "azure_snowflake"]
      workload_identity = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.ManagedIdentity/userAssignedIdentities/dander-phase6-runtime"
      environment       = { AZURE_CLIENT_ID = "33333333-3333-4333-8333-333333333333", HOME = "/tmp" }
      labels            = { pipeline = "warehouse_fixture", profile = "azure_snowflake" }
      secret_bindings   = { DANDER_POSTGRES_DSN = { provider = "azure_key_vault", reference = "azure-kv://https://dander-phase6-kv.vault.azure.net/secrets/postgres-dsn" } }
      resources         = { cpu_millis = 1000, memory_mib = 2048, deadline_seconds = 900, launcher_retry_count = 1 }
      schedule          = { paused = true, expression = "15 4 * * *", maximum_parallelism = 1, task_count = 1 }
      network           = { placement = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.App/managedEnvironments/dander-phase6-env" }
      observability     = { alert_target = null }
      extensions        = { azure_acr_login_server = "danderphase6.azurecr.io", azure_key_vault_uri = "https://dander-phase6-kv.vault.azure.net", azure_managed_identity_client_id = "33333333-3333-4333-8333-333333333333" }
    }
  }
}

run "wires_stage_zero_resources_into_jobs" {
  command = plan

  assert {
    condition     = module.container_apps_jobs.jobs["warehouse_fixture"].name == "dander-00626d3b5f01"
    error_message = "The Azure root must preserve deterministic manifest-to-job naming."
  }

  assert {
    condition     = output.key_vault_operator_principal_id == "44444444-4444-4444-8444-444444444444"
    error_message = "The Azure root must bind secret administration to the authenticated operator."
  }
}
