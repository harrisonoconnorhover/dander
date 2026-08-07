locals {
  any_pipeline_publishes_dataplex = anytrue([
    for pipeline in values(var.pipelines) : pipeline.publish_dataplex
  ])
  transform_bindings = {
    for binding in flatten([
      for pipeline_id, pipeline in var.pipelines : [
        for dataset_id in var.transform_dataset_ids : {
          key         = "${pipeline_id}|${dataset_id}"
          pipeline_id = pipeline_id
          dataset_id  = dataset_id
        }
      ]
    ]) : binding.key => binding
  }
  failure_alerts_enabled    = nonsensitive(var.failure_alert_email != "")
  failure_alert_pipelines   = local.failure_alerts_enabled ? var.pipelines : {}
  guarded_runtime_pipelines = var.require_guarded_free_tier ? var.pipelines : {}
}

# Preserve the original Greenhouse resources while the module evolves from one hard-coded job to
# an additive map. New pipelines receive independent resources; the live Greenhouse addresses move
# in state without replacement.
moved {
  from = google_service_account.runtime
  to   = google_service_account.runtime["greenhouse_jobs"]
}

moved {
  from = google_service_account.scheduler
  to   = google_service_account.scheduler["greenhouse_jobs"]
}

moved {
  from = google_project_iam_member.runtime_job_user
  to   = google_project_iam_member.runtime_job_user["greenhouse_jobs"]
}

moved {
  from = google_project_iam_member.runtime_pubsub_viewer
  to   = google_project_iam_member.runtime_pubsub_viewer["greenhouse_jobs"]
}

moved {
  from = google_bigquery_dataset_iam_member.runtime_writer
  to   = google_bigquery_dataset_iam_member.runtime_writer["greenhouse_jobs"]
}

moved {
  from = google_bigquery_dataset_iam_member.runtime_transform_writer["staging"]
  to   = google_bigquery_dataset_iam_member.runtime_transform_writer["greenhouse_jobs|staging"]
}

moved {
  from = google_bigquery_dataset_iam_member.runtime_transform_writer["marts"]
  to   = google_bigquery_dataset_iam_member.runtime_transform_writer["greenhouse_jobs|marts"]
}

moved {
  from = google_project_iam_member.runtime_catalog_editor[0]
  to   = google_project_iam_member.runtime_catalog_editor["greenhouse_jobs"]
}

moved {
  from = google_billing_account_iam_member.runtime_budget_viewer
  to   = google_billing_account_iam_member.runtime_budget_viewer["greenhouse_jobs"]
}

moved {
  from = google_cloud_run_v2_job.ingestion
  to   = google_cloud_run_v2_job.ingestion["greenhouse_jobs"]
}

moved {
  from = google_cloud_run_v2_job_iam_member.scheduler_invoker
  to   = google_cloud_run_v2_job_iam_member.scheduler_invoker["greenhouse_jobs"]
}

moved {
  from = google_cloud_scheduler_job.ingestion
  to   = google_cloud_scheduler_job.ingestion["greenhouse_jobs"]
}

resource "google_project_service" "required" {
  for_each = toset(concat([
    "artifactregistry.googleapis.com",
    "cloudscheduler.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
  ], local.any_pipeline_publishes_dataplex ? ["dataplex.googleapis.com"] : []))

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

data "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "dander"
  depends_on    = [google_project_service.required]
}

resource "google_service_account" "runtime" {
  for_each = var.pipelines

  project      = var.project_id
  account_id   = each.value.runtime_service_account_id
  display_name = "Dander runtime: ${each.key}"
}

resource "google_service_account" "scheduler" {
  for_each = var.pipelines

  project      = var.project_id
  account_id   = each.value.scheduler_service_account_id
  display_name = "Dander scheduler: ${each.key}"
}

resource "google_project_iam_member" "runtime_job_user" {
  for_each = var.pipelines

  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_project_iam_member" "runtime_pubsub_viewer" {
  for_each = local.guarded_runtime_pipelines

  project = var.project_id
  role    = "roles/pubsub.viewer"
  member  = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_bigquery_dataset_iam_member" "runtime_writer" {
  for_each = var.pipelines

  project    = var.project_id
  dataset_id = var.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_bigquery_dataset_iam_member" "runtime_transform_writer" {
  for_each = local.transform_bindings

  project    = var.project_id
  dataset_id = each.value.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime[each.value.pipeline_id].email}"
}

resource "google_project_iam_member" "runtime_catalog_editor" {
  for_each = {
    for id, pipeline in var.pipelines : id => pipeline if pipeline.publish_dataplex
  }

  project = var.project_id
  role    = "roles/dataplex.catalogEditor"
  member  = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_billing_account_iam_member" "runtime_budget_viewer" {
  for_each = local.guarded_runtime_pipelines

  billing_account_id = var.billing_account_id
  role               = "roles/billing.viewer"
  member             = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_cloud_run_v2_job" "ingestion" {
  for_each = var.pipelines

  project             = var.project_id
  name                = each.value.job_name
  location            = var.region
  deletion_protection = false
  labels = {
    module         = "scheduled-job"
    owner          = "dander"
    pipeline       = replace(each.key, "_", "-")
    profile        = var.execution_projections[each.key].labels.profile
    dander_version = replace(var.execution_projections[each.key].labels.dander_version, ".", "-")
    image_revision = substr(trimprefix(var.execution_projections[each.key].labels.image_digest, "sha256:"), 0, 12)
  }

  template {
    task_count  = var.execution_projections[each.key].schedule.task_count
    parallelism = var.execution_projections[each.key].schedule.maximum_parallelism

    template {
      service_account = google_service_account.runtime[each.key].email
      timeout         = "${var.execution_projections[each.key].resources.deadline_seconds}s"
      max_retries     = var.execution_projections[each.key].resources.launcher_retry_count

      containers {
        image = var.execution_projections[each.key].image
        args  = var.execution_projections[each.key].command

        resources {
          limits = {
            cpu    = tostring(var.execution_projections[each.key].resources.cpu_millis / 1000)
            memory = "${var.execution_projections[each.key].resources.memory_mib}Mi"
          }
        }

        dynamic "env" {
          for_each = var.execution_projections[each.key].environment
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {
          for_each = var.execution_projections[each.key].secret_bindings
          content {
            name  = env.key
            value = trimprefix(env.value.reference, "gcp-sm://")
          }
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition = (
        var.execution_projections[each.key].workload_identity == google_service_account.runtime[each.key].email &&
        var.execution_projections[each.key].observability.log_destination == "cloud_logging" &&
        var.execution_projections[each.key].observability.metric_namespace == "run.googleapis.com" &&
        var.execution_projections[each.key].observability.alert_target == (local.failure_alerts_enabled ? var.failure_alert_email : null) &&
        var.execution_projections[each.key].observability.retention_days == null &&
        toset(keys(var.execution_projections[each.key].secret_bindings)) == toset(keys(each.value.secret_env)) &&
        alltrue([
          for env_name, binding in var.execution_projections[each.key].secret_bindings :
          binding.provider == "gcp_secret_manager" &&
          binding.reference == "gcp-sm://projects/${var.project_id}/secrets/${each.value.secret_env[env_name]}/versions/latest"
        ])
      )
      error_message = "Cloud Run cannot honor this execution projection exactly."
    }
  }

  depends_on = [
    data.google_artifact_registry_repository.images,
    google_bigquery_dataset_iam_member.runtime_writer,
    google_bigquery_dataset_iam_member.runtime_transform_writer,
    google_billing_account_iam_member.runtime_budget_viewer,
    google_project_iam_member.runtime_job_user,
    google_project_iam_member.runtime_pubsub_viewer,
    google_project_iam_member.runtime_catalog_editor,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  for_each = var.pipelines

  project  = var.project_id
  location = google_cloud_run_v2_job.ingestion[each.key].location
  name     = google_cloud_run_v2_job.ingestion[each.key].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler[each.key].email}"
}

resource "google_cloud_scheduler_job" "ingestion" {
  for_each = var.pipelines

  project          = var.project_id
  region           = var.region
  name             = "${each.value.job_name}-daily"
  description      = "Run Dander pipeline ${each.key}"
  schedule         = var.execution_projections[each.key].schedule.expression
  time_zone        = var.execution_projections[each.key].schedule.time_zone
  paused           = var.execution_projections[each.key].schedule.paused
  attempt_deadline = "180s"

  retry_config {
    retry_count          = 1
    min_backoff_duration = "30s"
    max_backoff_duration = "60s"
    max_doublings        = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.ingestion[each.key].name}:run"
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = google_service_account.scheduler[each.key].email
    }
  }

  depends_on = [
    google_cloud_run_v2_job_iam_member.scheduler_invoker,
    google_project_service.required,
  ]
}

resource "google_monitoring_notification_channel" "pipeline_failures" {
  count = local.failure_alerts_enabled ? 1 : 0

  project      = var.project_id
  display_name = "Dander pipeline failures"
  description  = "Operator-owned email channel for hosted Dander pipeline failures."
  type         = "email"
  enabled      = true
  labels = {
    email_address = var.failure_alert_email
  }
  user_labels = {
    managed_by = "dander"
  }

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "pipeline_failure" {
  for_each = local.failure_alert_pipelines

  project      = var.project_id
  display_name = "Dander pipeline failure: ${each.key}"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "${each.value.job_name} execution failed"

    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloud_run_job\"",
        "resource.label.\"job_name\" = \"${each.value.job_name}\"",
        "metric.type = \"run.googleapis.com/job/completed_execution_count\"",
        "metric.label.\"result\" = \"failed\"",
      ])
      comparison      = "COMPARISON_GT"
      duration        = "0s"
      threshold_value = 0

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    content   = "Cloud Run reported a failed execution for `${each.value.job_name}`. Inspect the execution logs and `dander_meta._dander_runs` before replaying the pipeline."
    mime_type = "text/markdown"
    subject   = "Dander pipeline failed: ${each.key}"
  }

  alert_strategy {
    auto_close           = "1800s"
    notification_prompts = ["OPENED"]
  }

  notification_channels = [google_monitoring_notification_channel.pipeline_failures[0].name]
  user_labels = {
    managed_by = "dander"
    pipeline   = replace(each.key, "_", "-")
  }

  depends_on = [
    google_cloud_run_v2_job.ingestion,
    google_monitoring_notification_channel.pipeline_failures,
  ]
}
