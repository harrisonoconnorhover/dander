variable "project_id" {
  type        = string
  description = "GCP project id receiving the Dander datasets."
}

variable "bootstrap_service_account" {
  type        = string
  description = "Existing dander-bootstrap service account used for platform Terraform impersonation."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\\.iam\\.gserviceaccount\\.com$", var.bootstrap_service_account))
    error_message = "bootstrap_service_account must be a valid service-account email."
  }
}

variable "region" {
  type        = string
  description = "Default GCP region for provider resources."
  default     = "us-central1"
}

variable "bigquery_location" {
  type        = string
  description = "BigQuery data location."
  default     = "US"
}

variable "runtime_cpu" {
  type        = number
  description = "Cloud Run CPU count shared by every hosted pipeline."
  default     = 1

  validation {
    condition     = contains([1, 2, 4, 6, 8], var.runtime_cpu)
    error_message = "runtime_cpu must be one of 1, 2, 4, 6, or 8."
  }
}

variable "runtime_memory" {
  type        = string
  description = "Cloud Run memory limit shared by every hosted pipeline."
  default     = "512Mi"

  validation {
    condition     = can(regex("^[1-9][0-9]*(Mi|Gi)$", var.runtime_memory))
    error_message = "runtime_memory must be a positive Mi or Gi quantity."
  }
}

variable "runtime_timeout_seconds" {
  type        = number
  description = "Per-task timeout in seconds shared by every hosted pipeline."
  default     = 300

  validation {
    condition     = var.runtime_timeout_seconds >= 1 && var.runtime_timeout_seconds <= 86400 && floor(var.runtime_timeout_seconds) == var.runtime_timeout_seconds
    error_message = "runtime_timeout_seconds must be an integer from 1 through 86400."
  }
}

variable "runtime_max_retries" {
  type        = number
  description = "Task retries after the initial attempt for every hosted pipeline."
  default     = 1

  validation {
    condition     = var.runtime_max_retries >= 0 && var.runtime_max_retries <= 10 && floor(var.runtime_max_retries) == var.runtime_max_retries
    error_message = "runtime_max_retries must be an integer from 0 through 10."
  }
}

variable "runtime_batch_rows" {
  type        = number
  description = "Maximum rows sent in one BigQuery writer request by hosted pipelines."
  default     = 10000

  validation {
    condition     = var.runtime_batch_rows >= 1 && var.runtime_batch_rows <= 100000 && floor(var.runtime_batch_rows) == var.runtime_batch_rows
    error_message = "runtime_batch_rows must be an integer from 1 through 100000."
  }
}

variable "require_guarded_free_tier" {
  type        = bool
  description = "Require hosted pipelines to run Dander's guarded-free-tier preflight."
  default     = true
}

variable "datasets" {
  type        = list(string)
  description = "BigQuery datasets created for the first runtime slice."
  default     = ["raw", "staging", "marts", "dander_meta"]
}

variable "enable_scheduled_job" {
  type        = bool
  description = "Provision the public-ingestion Cloud Run Job and daily scheduler."
  default     = false
}

variable "aws_control_role_arn" {
  type        = string
  description = "Optional AWS ECS task role allowed to operate Cloud Run Jobs through WIF."
  default     = ""

  validation {
    condition     = var.aws_control_role_arn == "" || can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_-]+$", var.aws_control_role_arn))
    error_message = "aws_control_role_arn must be empty or one unpathed commercial-AWS role ARN."
  }
}

variable "billing_account_id" {
  type        = string
  description = "Billing account id used for the runtime's read-only budget preflight."
  default     = ""
}

variable "runtime_container_image" {
  type        = string
  description = "Immutable Artifact Registry image reference for the Cloud Run Job."
  default     = ""
}

variable "druff_container_image" {
  type        = string
  description = "Optional immutable Druff UI image; empty disables the hosted interface."
  default     = ""

  validation {
    condition     = var.druff_container_image == "" || can(regex("@sha256:[0-9a-f]{64}$", var.druff_container_image))
    error_message = "druff_container_image must be empty or an immutable image reference ending in @sha256:<64 lowercase hex characters>."
  }
}

variable "pipelines" {
  description = "Expanded hosted pipeline definitions keyed by stable Dander pipeline id."
  type = map(object({
    job_name                     = string
    runtime_service_account_id   = string
    scheduler_service_account_id = string
    source                       = string
    models                       = list(string)
    build_models                 = bool
    publish_dataplex             = bool
    schedule                     = string
    time_zone                    = string
    paused                       = bool
    secret_env                   = map(string)
  }))
  default = {}
}

variable "execution_projections" {
  description = "Validated cloud-neutral execution templates keyed by pipeline id."
  type = map(object({
    schema                  = string
    contract                = string
    pipeline_id             = string
    profile_id              = string
    launcher                = string
    image                   = string
    command                 = list(string)
    configuration_reference = string
    environment             = map(string)
    secret_bindings = map(object({
      provider  = string
      reference = string
    }))
    workload_identity = string
    resources = object({
      cpu_millis            = number
      memory_mib            = number
      ephemeral_storage_mib = number
      deadline_seconds      = number
      runtime_retry_count   = number
      launcher_retry_count  = number
    })
    schedule = object({
      task_count          = number
      maximum_parallelism = number
      expression          = string
      time_zone           = string
      paused              = bool
    })
    network = object({
      placement  = string
      extensions = map(string)
    })
    labels = map(string)
    observability = object({
      log_destination  = string
      metric_namespace = string
      alert_target     = string
      retention_days   = number
    })
    extensions = map(string)
  }))
  default = {}

  validation {
    condition = !var.enable_scheduled_job || (
      toset(keys(var.execution_projections)) == toset(keys(var.pipelines)) &&
      alltrue([
        for id, projection in var.execution_projections :
        projection.schema == "io.dander.execution/v1" &&
        projection.contract == "io.dander.runtime/v1" &&
        projection.pipeline_id == id &&
        projection.profile_id == "gcp" &&
        projection.launcher == "cloud_run" &&
        can(regex("@sha256:[0-9a-f]{64}$", projection.image)) &&
        length(projection.command) > 0 &&
        projection.configuration_reference == "/app/dander.yaml"
      ])
    )
    error_message = "Hosted pipelines require one matching validated GCP/Cloud Run execution projection."
  }
}

variable "failure_alert_email" {
  type        = string
  description = "Operator email receiving hosted-pipeline failure notifications; empty disables alerts."
  default     = ""
  sensitive   = true

  validation {
    condition     = var.failure_alert_email == "" || can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.failure_alert_email))
    error_message = "failure_alert_email must be empty or a valid email address."
  }
}

variable "secret_ids" {
  type        = set(string)
  description = "Secret Manager containers to create; secret values are never managed by Terraform."
  default     = []

  validation {
    condition = alltrue([
      for secret_id in var.secret_ids : can(regex("^[A-Za-z][A-Za-z0-9_-]{0,254}$", secret_id))
    ])
    error_message = "Secret ids must begin with a letter and contain only letters, numbers, '_' or '-'."
  }
}

variable "github_repository" {
  type        = string
  description = "GitHub owner/repository allowed to use deployment WIF; empty disables WIF."
  default     = ""

  validation {
    condition     = var.github_repository == "" || can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "GitHub repository must be empty or use owner/repository format."
  }
}

variable "github_ref" {
  type        = string
  description = "Exact Git ref allowed to use deployment WIF."
  default     = "refs/heads/main"

  validation {
    condition     = can(regex("^refs/(heads|tags)/[A-Za-z0-9._/-]+$", var.github_ref))
    error_message = "GitHub ref must be an exact refs/heads/... or refs/tags/... value."
  }
}

variable "enable_cost_guard" {
  type        = bool
  description = "Provision the project-scoped budget and simulation-first kill switch."
  default     = false
}

variable "cost_guard_budget_name" {
  type        = string
  description = "Display name expected by the budget verifier and kill-switch handler."
  default     = "dander-sbx-cap"
}

variable "cost_guard_budget_amount" {
  type        = number
  description = "Maximum configured USD budget; Dander rejects values above five."
  default     = 5

  validation {
    condition     = var.cost_guard_budget_amount > 0 && var.cost_guard_budget_amount <= 5
    error_message = "Cost-guard budget must be greater than zero and no greater than USD 5."
  }
}

variable "cost_guard_simulate" {
  type        = bool
  description = "Log an over-budget action without unlinking billing."
  default     = true
}

variable "cost_guard_source_bucket" {
  type        = string
  description = "Existing GCS bucket used to stage the Cloud Run function source archive."
  default     = ""
}
