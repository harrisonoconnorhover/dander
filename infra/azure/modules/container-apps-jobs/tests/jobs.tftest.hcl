mock_provider "azurerm" {
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
  name                            = "dander"
  subscription_id                 = "11111111-1111-4111-8111-111111111111"
  tenant_id                       = "22222222-2222-4222-8222-222222222222"
  location                        = "eastus"
  resource_group_name             = "dander-phase6"
  container_app_environment_name  = "dander-phase6-env"
  acr_id                          = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.ContainerRegistry/registries/danderphase6"
  acr_login_server                = "danderphase6.azurecr.io"
  key_vault_name                  = "dander-phase6-kv"
  key_vault_allowed_ip_rule       = "203.0.113.10"
  key_vault_operator_principal_id = "55555555-5555-4555-8555-555555555555"
  managed_identity_id             = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.ManagedIdentity/userAssignedIdentities/dander-phase6-runtime"
  managed_identity_client_id      = "33333333-3333-4333-8333-333333333333"
  managed_identity_principal_id   = "44444444-4444-4444-8444-444444444444"
  execution_projections = {
    bigquery_fixture = {
      launcher          = "azure_container_apps"
      image             = "danderphase6.azurecr.io/dander/runtime@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      command           = ["runtime", "execute", "--contract", "io.dander.runtime/v1", "--pipeline", "bigquery_fixture", "--platform", "gcp"]
      workload_identity = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.ManagedIdentity/userAssignedIdentities/dander-phase6-runtime"
      environment       = { AZURE_CLIENT_ID = "33333333-3333-4333-8333-333333333333", DANDER_GCP_WIF_AUDIENCE = "//iam.googleapis.com/projects/1009770943166/locations/global/workloadIdentityPools/dander-phase6-azure/providers/container-apps", HOME = "/tmp" }
      labels            = { pipeline = "bigquery_fixture", profile = "gcp" }
      secret_bindings   = { API_TOKEN = { provider = "gcp_secret_manager", reference = "gcp-sm://projects/unit-project/secrets/source-api-token/versions/latest" } }
      resources         = { cpu_millis = 1000, memory_mib = 2048, deadline_seconds = 900, launcher_retry_count = 1 }
      schedule          = { paused = true, expression = "15 4 * * *", maximum_parallelism = 1, task_count = 1 }
      network           = { placement = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/Microsoft.App/managedEnvironments/dander-phase6-env" }
      observability     = { alert_target = null }
      extensions        = { azure_acr_login_server = "danderphase6.azurecr.io", azure_key_vault_uri = "https://dander-phase6-kv.vault.azure.net", azure_managed_identity_client_id = "33333333-3333-4333-8333-333333333333" }
    }
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
      observability     = { alert_target = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/dander-phase6/providers/microsoft.insights/actionGroups/dander-phase6" }
      extensions        = { azure_acr_login_server = "danderphase6.azurecr.io", azure_key_vault_uri = "https://dander-phase6-kv.vault.azure.net", azure_managed_identity_client_id = "33333333-3333-4333-8333-333333333333" }
    }
  }
}

run "projects_exact_job_contract" {
  command = plan

  assert {
    condition = (
      azurerm_container_app_job.pipeline["warehouse_fixture"].replica_timeout_in_seconds == 900 &&
      azurerm_container_app_job.pipeline["warehouse_fixture"].replica_retry_limit == 1
    )
    error_message = "Container Apps must preserve the projected deadline and launcher retry count."
  }

  assert {
    condition = (
      one([
        for env in azurerm_container_app_job.pipeline["bigquery_fixture"].template[0].container[0].env :
        env.value if env.name == "API_TOKEN"
      ]) == "projects/unit-project/secrets/source-api-token/versions/latest" &&
      length(azurerm_container_app_job.pipeline["bigquery_fixture"].secret) == 0
    )
    error_message = "GCP Secret Manager references must remain runtime inputs, not Azure Key Vault secrets."
  }

  assert {
    condition = (
      azurerm_container_app_job.pipeline["warehouse_fixture"].template[0].container[0].args[0] == "runtime" &&
      azurerm_container_app_job.pipeline["warehouse_fixture"].template[0].container[0].image == "danderphase6.azurecr.io/dander/runtime@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    error_message = "Container Apps must preserve the OCI digest and runtime arguments."
  }

  assert {
    condition = (
      length(azurerm_container_app_job.pipeline["warehouse_fixture"].manual_trigger_config) == 1 &&
      length(azurerm_container_app_job.pipeline["warehouse_fixture"].schedule_trigger_config) == 0
    )
    error_message = "Paused pipelines must remain manual-only."
  }

  assert {
    condition     = azurerm_monitor_metric_alert.failed_execution["warehouse_fixture"].criteria[0].metric_name == "Executions"
    error_message = "A projected alert target must receive failed-execution monitoring."
  }


  assert {
    condition = (
      azurerm_key_vault.runtime.network_acls[0].default_action == "Deny" &&
      azurerm_key_vault.runtime.network_acls[0].bypass == "AzureServices"
    )
    error_message = "Key Vault must default-deny network access while allowing the Azure service path."
  }

  assert {
    condition = (
      azurerm_role_assignment.key_vault_operator.principal_id == "55555555-5555-4555-8555-555555555555" &&
      azurerm_role_assignment.key_vault_operator.role_definition_name == "Key Vault Secrets Officer" &&
      azurerm_role_assignment.key_vault_secrets.principal_id == "44444444-4444-4444-8444-444444444444" &&
      azurerm_role_assignment.key_vault_secrets.role_definition_name == "Key Vault Secrets User"
    )
    error_message = "The signed-in operator may rotate secrets while the runtime remains read-only."
  }
}
