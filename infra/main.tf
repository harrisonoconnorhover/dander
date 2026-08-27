module "bigquery" {
  source = "./modules/bigquery"

  project_id = var.project_id
  location   = var.bigquery_location
  datasets   = var.datasets
}

module "scheduled_job" {
  count  = var.enable_scheduled_job ? 1 : 0
  source = "./modules/scheduled-job"

  project_id                = var.project_id
  region                    = var.region
  billing_account_id        = var.billing_account_id
  container_image           = var.runtime_container_image
  runtime_cpu               = var.runtime_cpu
  runtime_memory            = var.runtime_memory
  runtime_timeout_seconds   = var.runtime_timeout_seconds
  runtime_max_retries       = var.runtime_max_retries
  runtime_batch_rows        = var.runtime_batch_rows
  require_guarded_free_tier = var.require_guarded_free_tier
  pipelines                 = var.pipelines
  execution_projections     = var.execution_projections
  failure_alert_email       = var.failure_alert_email
  transform_dataset_ids = setsubtract(
    toset(var.datasets),
    toset(["raw"]),
  )

  depends_on = [module.bigquery]
}

module "druff" {
  count  = var.druff_container_image == "" ? 0 : 1
  source = "./modules/druff"

  project_id      = var.project_id
  region          = var.region
  container_image = var.druff_container_image

  depends_on = [module.scheduled_job]
}

locals {
  pipeline_secret_ids = toset(flatten([
    for pipeline in values(var.pipelines) : values(pipeline.secret_env)
  ]))
  managed_secret_ids = setunion(var.secret_ids, local.pipeline_secret_ids)
  pipeline_secret_accessors = var.enable_scheduled_job ? {
    for secret_id in local.managed_secret_ids : secret_id => toset([
      for pipeline_id, pipeline in var.pipelines :
      "serviceAccount:${module.scheduled_job[0].runtime_service_accounts[pipeline_id]}"
      if contains(values(pipeline.secret_env), secret_id)
    ])
  } : {}
}

module "secret_manager" {
  count  = length(local.managed_secret_ids) > 0 ? 1 : 0
  source = "./modules/secret-manager"

  project_id          = var.project_id
  secret_ids          = local.managed_secret_ids
  accessors_by_secret = local.pipeline_secret_accessors
}

module "github_wif" {
  count  = var.github_repository != "" && var.enable_scheduled_job ? 1 : 0
  source = "./modules/github-wif"

  project_id          = var.project_id
  region              = var.region
  artifact_repository = module.scheduled_job[0].artifact_repository_id
  github_repository   = var.github_repository
  github_ref          = var.github_ref
  service_account_ids = setunion(
    toset(values(module.scheduled_job[0].runtime_service_account_names)),
    toset(values(module.scheduled_job[0].scheduler_service_account_names)),
  )
}

module "aws_control_wif" {
  count  = var.aws_control_role_arn != "" && var.enable_scheduled_job ? 1 : 0
  source = "./modules/aws-control-wif"

  project_id                    = var.project_id
  aws_control_role_arn          = var.aws_control_role_arn
  runtime_service_account_names = module.scheduled_job[0].runtime_service_account_names
}

check "github_wif_requires_runtime" {
  assert {
    condition     = var.github_repository == "" || var.enable_scheduled_job
    error_message = "github_repository requires enable_scheduled_job=true."
  }
}

check "aws_control_wif_requires_runtime" {
  assert {
    condition     = var.aws_control_role_arn == "" || var.enable_scheduled_job
    error_message = "aws_control_role_arn requires enable_scheduled_job=true."
  }
}

check "druff_requires_runtime" {
  assert {
    condition     = var.druff_container_image == "" || var.enable_scheduled_job
    error_message = "druff_container_image requires enable_scheduled_job=true."
  }
}

check "runtime_requires_pipelines" {
  assert {
    condition     = !var.enable_scheduled_job || length(var.pipelines) > 0
    error_message = "enable_scheduled_job=true requires at least one pipelines entry."
  }
}

check "pipelines_require_runtime" {
  assert {
    condition     = var.enable_scheduled_job || length(var.pipelines) == 0
    error_message = "pipelines entries require enable_scheduled_job=true."
  }
}

check "failure_alerts_require_runtime" {
  assert {
    condition     = var.failure_alert_email == "" || var.enable_scheduled_job
    error_message = "failure_alert_email requires enable_scheduled_job=true."
  }
}

check "guarded_runtime_requires_cost_guard" {
  assert {
    condition = !var.enable_scheduled_job || (
      !var.require_guarded_free_tier || var.enable_cost_guard
    )
    error_message = "require_guarded_free_tier=true requires enable_cost_guard=true for hosted jobs."
  }
}

module "cost_guard" {
  count  = var.enable_cost_guard ? 1 : 0
  source = "./modules/cost-guard"
  providers = {
    google         = google
    google.billing = google.billing
  }

  project_id          = var.project_id
  region              = var.region
  billing_account_id  = var.billing_account_id
  source_bucket       = var.cost_guard_source_bucket
  function_source_dir = "${path.root}/functions/stop_billing"
  budget_name         = var.cost_guard_budget_name
  budget_amount       = var.cost_guard_budget_amount
  simulate            = var.cost_guard_simulate
}

check "cost_guard_inputs" {
  assert {
    condition = !var.enable_cost_guard || (
      var.billing_account_id != "" && var.cost_guard_source_bucket != ""
    )
    error_message = "The cost guard requires billing_account_id and cost_guard_source_bucket."
  }
}
