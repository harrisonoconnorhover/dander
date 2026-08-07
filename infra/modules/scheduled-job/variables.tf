variable "project_id" {
  type        = string
  description = "GCP project that owns the runtime resources."
}

variable "region" {
  type        = string
  description = "GCP region for Artifact Registry, Cloud Run, and Cloud Scheduler."
}

variable "billing_account_id" {
  type        = string
  description = "Billing account inspected by the guarded free-tier preflight."
}

variable "container_image" {
  type        = string
  description = "Immutable Artifact Registry image reference, including its sha256 digest."

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.container_image))
    error_message = "container_image must be an immutable image reference ending in @sha256:<64 lowercase hex characters>."
  }
}

variable "runtime_cpu" {
  type        = number
  description = "Cloud Run CPU count shared by every hosted pipeline."

  validation {
    condition     = contains([1, 2, 4, 6, 8], var.runtime_cpu)
    error_message = "runtime_cpu must be one of 1, 2, 4, 6, or 8."
  }
}

variable "runtime_memory" {
  type        = string
  description = "Cloud Run memory limit shared by every hosted pipeline."

  validation {
    condition     = can(regex("^[1-9][0-9]*(Mi|Gi)$", var.runtime_memory))
    error_message = "runtime_memory must be a positive Mi or Gi quantity."
  }
}

variable "runtime_timeout_seconds" {
  type        = number
  description = "Per-task timeout in seconds shared by every hosted pipeline."

  validation {
    condition     = var.runtime_timeout_seconds >= 1 && var.runtime_timeout_seconds <= 86400 && floor(var.runtime_timeout_seconds) == var.runtime_timeout_seconds
    error_message = "runtime_timeout_seconds must be an integer from 1 through 86400."
  }
}

variable "runtime_max_retries" {
  type        = number
  description = "Task retries after the initial attempt for every hosted pipeline."

  validation {
    condition     = var.runtime_max_retries >= 0 && var.runtime_max_retries <= 10 && floor(var.runtime_max_retries) == var.runtime_max_retries
    error_message = "runtime_max_retries must be an integer from 0 through 10."
  }
}

variable "runtime_batch_rows" {
  type        = number
  description = "Maximum rows sent in one BigQuery writer request by hosted pipelines."

  validation {
    condition     = var.runtime_batch_rows >= 1 && var.runtime_batch_rows <= 100000 && floor(var.runtime_batch_rows) == var.runtime_batch_rows
    error_message = "runtime_batch_rows must be an integer from 1 through 100000."
  }
}

variable "require_guarded_free_tier" {
  type        = bool
  description = "Require hosted pipelines to run Dander's guarded-free-tier preflight."
}

variable "dataset_id" {
  type        = string
  description = "BigQuery dataset every runtime may edit for ingestion and control state."
  default     = "raw"
}

variable "transform_dataset_ids" {
  type        = set(string)
  description = "Additional datasets hosted transforms may edit."
  default     = ["staging", "marts"]
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

  validation {
    condition = alltrue([
      for id, pipeline in var.pipelines :
      can(regex("^[a-z][a-z0-9_-]{1,62}$", id)) &&
      can(regex("^[a-z][a-z0-9-]{0,61}[a-z0-9]$", pipeline.job_name)) &&
      can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", pipeline.runtime_service_account_id)) &&
      can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", pipeline.scheduler_service_account_id)) &&
      can(regex("^[A-Za-z_][A-Za-z0-9_-]*$", pipeline.source)) &&
      (!pipeline.build_models || length(pipeline.models) > 0) &&
      alltrue([for model in pipeline.models : can(regex("^[A-Za-z_][A-Za-z0-9_-]*$", model))]) &&
      length(trimspace(pipeline.schedule)) > 0 &&
      length(trimspace(pipeline.time_zone)) > 0 &&
      alltrue([
        for env_name, secret_id in pipeline.secret_env :
        can(regex("^[A-Z][A-Z0-9_]*$", env_name)) &&
        can(regex("^[A-Za-z][A-Za-z0-9_-]{0,254}$", secret_id))
      ])
    ])
    error_message = "Every pipeline must use safe ids, models when builds are enabled, a non-empty schedule, and valid secret bindings."
  }
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

  validation {
    condition = (
      toset(keys(var.execution_projections)) == toset(keys(var.pipelines)) &&
      alltrue([
        for id, projection in var.execution_projections :
        projection.schema == "io.dander.execution/v1" &&
        projection.contract == "io.dander.runtime/v1" &&
        projection.pipeline_id == id &&
        projection.profile_id == "gcp" &&
        projection.launcher == "cloud_run" &&
        can(regex("@sha256:[0-9a-f]{64}$", projection.image)) &&
        projection.resources.ephemeral_storage_mib == null &&
        projection.resources.runtime_retry_count == 0 &&
        projection.schedule.task_count == 1 &&
        projection.schedule.maximum_parallelism == 1 &&
        projection.network.placement == null &&
        length(projection.network.extensions) == 0 &&
        length(projection.extensions) == 0
      ])
    )
    error_message = "Every pipeline requires one supported GCP/Cloud Run execution projection."
  }
}

variable "failure_alert_email" {
  type        = string
  description = "Operator email receiving Cloud Run Job failure notifications; empty disables alerting."
  default     = ""
  sensitive   = true

  validation {
    condition     = var.failure_alert_email == "" || can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.failure_alert_email))
    error_message = "failure_alert_email must be empty or a valid email address."
  }
}
