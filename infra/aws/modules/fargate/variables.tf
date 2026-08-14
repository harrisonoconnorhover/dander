variable "name" {
  type        = string
  description = "Stable name prefix for this Dander Fargate deployment."
}

variable "aws_account_id" {
  type        = string
  description = "AWS account that owns the Fargate deployment."
}

variable "region" {
  type        = string
  description = "AWS region for all module resources."
}

variable "ecr_repository_name" {
  type        = string
  description = "ECR repository for immutable Dander runtime manifests."
  default     = "dander"
}

variable "execution_projections" {
  description = "Validated Fargate execution templates keyed by pipeline id."
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
      alert_target     = optional(string)
      retention_days   = number
    })
    extensions = map(string)
  }))

  validation {
    condition = length(var.execution_projections) > 0 && alltrue([
      for id, projection in var.execution_projections :
      can(regex("^[a-z][a-z0-9_-]{1,62}$", id)) &&
      projection.pipeline_id == id &&
      projection.schema == "io.dander.execution/v1" &&
      projection.contract == "io.dander.runtime/v1" &&
      projection.launcher == "fargate" &&
      can(regex("^[a-z][a-z0-9_-]{0,62}$", projection.profile_id)) &&
      can(regex("@sha256:[0-9a-f]{64}$", projection.image)) &&
      projection.configuration_reference == "/app/dander.yaml" &&
      length(projection.command) >= 8 &&
      projection.command[0] == "runtime" &&
      projection.command[1] == "execute" &&
      projection.resources.runtime_retry_count == 0 &&
      contains([1000, 2000, 4000, 8000, 16000], projection.resources.cpu_millis) &&
      projection.resources.deadline_seconds >= 1 &&
      projection.resources.deadline_seconds <= 86400 &&
      projection.resources.launcher_retry_count >= 0 &&
      projection.resources.launcher_retry_count <= 10 &&
      projection.resources.ephemeral_storage_mib >= 20480 &&
      projection.resources.ephemeral_storage_mib <= 204800 &&
      projection.schedule.task_count == 1 &&
      projection.schedule.maximum_parallelism == 1 &&
      can(regex("^cron\\(.+\\)$", projection.schedule.expression)) &&
      length(trimspace(projection.schedule.time_zone)) > 0 &&
      projection.network.placement == "awsvpc" &&
      contains(keys(projection.network.extensions), "fargate_subnet_ids") &&
      contains(keys(projection.network.extensions), "fargate_security_group_ids") &&
      length(setsubtract(toset(keys(projection.network.extensions)), toset(["fargate_subnet_ids", "fargate_security_group_ids"]))) == 0 &&
      contains(["ARM64", "X86_64"], lookup(projection.extensions, "fargate_architecture", "")) &&
      contains(["enabled", "disabled"], lookup(projection.extensions, "fargate_assign_public_ip", "")) &&
      can(tonumber(lookup(projection.extensions, "fargate_stop_timeout_seconds", ""))) &&
      tonumber(lookup(projection.extensions, "fargate_stop_timeout_seconds", "0")) >= 2 &&
      tonumber(lookup(projection.extensions, "fargate_stop_timeout_seconds", "0")) <= 120 &&
      length(setsubtract(toset(keys(projection.extensions)), toset(["fargate_architecture", "fargate_assign_public_ip", "fargate_stop_timeout_seconds"]))) == 0 &&
      projection.observability.log_destination == "cloudwatch_logs" &&
      projection.observability.metric_namespace == "Dander" &&
      projection.observability.retention_days >= 1 &&
      length(setintersection(toset(keys(projection.environment)), toset(keys(projection.secret_bindings)))) == 0 &&
      projection.labels.image_digest == element(reverse(split("@", projection.image)), 0) &&
      alltrue([
        for binding in values(projection.secret_bindings) :
        contains(["gcp_secret_manager", "aws_secret_manager"], binding.provider)
      ])
    ])
    error_message = "Every entry must be one complete, immutable, bounded Fargate execution projection."
  }
}

variable "aws_native_profile" {
  description = "Existing AWS-native Redshift, staging, and Glue coordinates; null for the GCP data plane."
  type = object({
    redshift_deployment         = string
    redshift_cluster_identifier = optional(string)
    redshift_workgroup_name     = optional(string)
    redshift_database           = string
    redshift_db_user            = optional(string)
    staging_bucket              = string
    staging_prefix              = string
    glue_catalog_id             = string
    glue_database_prefix        = string
  })
  default  = null
  nullable = true

  validation {
    condition = var.aws_native_profile == null || (
      contains(["provisioned", "serverless"], var.aws_native_profile.redshift_deployment) &&
      (
        var.aws_native_profile.redshift_deployment == "provisioned" ?
        var.aws_native_profile.redshift_cluster_identifier != null &&
        var.aws_native_profile.redshift_workgroup_name == null &&
        var.aws_native_profile.redshift_db_user != null :
        var.aws_native_profile.redshift_cluster_identifier == null &&
        var.aws_native_profile.redshift_workgroup_name != null &&
        var.aws_native_profile.redshift_db_user == null
      ) &&
      can(regex("^[a-z_][a-z0-9_]{0,126}$", var.aws_native_profile.redshift_database)) &&
      can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.aws_native_profile.staging_bucket)) &&
      length(trim(var.aws_native_profile.staging_prefix, "/")) > 0 &&
      !strcontains(var.aws_native_profile.staging_prefix, "*") &&
      !strcontains(var.aws_native_profile.staging_prefix, "?") &&
      var.aws_native_profile.glue_catalog_id == var.aws_account_id &&
      can(regex("^[a-z][a-z0-9_]{0,31}$", var.aws_native_profile.glue_database_prefix))
    )
    error_message = "aws_native_profile must name one exact Redshift target, staging prefix, and account-local Glue catalog."
  }
}

variable "scheduler_delivery_retry_count" {
  type        = number
  description = "Scheduler delivery retries, separate from launcher retries."
  default     = 2

  validation {
    condition     = var.scheduler_delivery_retry_count >= 0 && var.scheduler_delivery_retry_count <= 185 && floor(var.scheduler_delivery_retry_count) == var.scheduler_delivery_retry_count
    error_message = "scheduler_delivery_retry_count must be an integer from 0 through 185."
  }
}

variable "scheduler_delivery_max_age_seconds" {
  type        = number
  description = "Maximum age for an undelivered scheduled invocation."
  default     = 3600

  validation {
    condition     = var.scheduler_delivery_max_age_seconds >= 60 && var.scheduler_delivery_max_age_seconds <= 86400 && floor(var.scheduler_delivery_max_age_seconds) == var.scheduler_delivery_max_age_seconds
    error_message = "scheduler_delivery_max_age_seconds must be an integer from 60 through 86400."
  }
}

variable "tags" {
  type        = map(string)
  description = "Additional non-secret AWS tags."
  default     = {}
}
