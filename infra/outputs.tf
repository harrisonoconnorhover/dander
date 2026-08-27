output "dataset_ids" {
  description = "BigQuery dataset ids created by the bootstrap."
  value       = module.bigquery.dataset_ids
}

output "scheduled_jobs" {
  description = "Scheduled pipeline details keyed by pipeline id when enabled."
  value       = var.enable_scheduled_job ? module.scheduled_job[0].jobs : {}
}

output "failure_alerting" {
  description = "Failure notification resources when an operator email is configured."
  value       = var.enable_scheduled_job ? module.scheduled_job[0].failure_alerting : null
}

output "secret_resources" {
  description = "Secret Manager resource names created by the bootstrap."
  value       = length(local.managed_secret_ids) > 0 ? module.secret_manager[0].secret_resources : null
}

output "github_workload_identity" {
  description = "Keyless GitHub deployment identity details when enabled."
  value = var.github_repository != "" && var.enable_scheduled_job ? {
    provider_resource_name = module.github_wif[0].provider_resource_name
    service_account_email  = module.github_wif[0].service_account_email
  } : null
}

output "aws_control_workload_identity" {
  description = "Keyless AWS Control identity details when configured."
  value = var.aws_control_role_arn != "" && var.enable_scheduled_job ? {
    provider_resource_name = module.aws_control_wif[0].provider_resource_name
    service_account_email  = module.aws_control_wif[0].service_account_email
    audience               = module.aws_control_wif[0].audience
  } : null
}

output "cost_guard" {
  description = "Budget and simulation-first kill-switch details when enabled."
  value = var.enable_cost_guard ? {
    budget_name   = module.cost_guard[0].budget_name
    function_name = module.cost_guard[0].function_name
    pubsub_topic  = module.cost_guard[0].pubsub_topic
    simulated     = var.cost_guard_simulate
  } : null
}

output "druff" {
  description = "Hosted Druff UI details when an immutable image is configured."
  value = var.druff_container_image == "" ? null : {
    service_name = module.druff[0].service_name
    url          = module.druff[0].url
  }
}
