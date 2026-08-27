locals {
  aws_account_id = split(":", var.aws_control_role_arn)[4]
  aws_role_name  = split("/", var.aws_control_role_arn)[1]
  required_services = toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "sts.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "aws_control" {
  project                   = var.project_id
  workload_identity_pool_id = "dander-aws-control"
  display_name              = "Dander AWS Control"

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "aws_control" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws_control.workload_identity_pool_id
  workload_identity_pool_provider_id = "aws-control"
  display_name                       = "Dander AWS Control task"

  attribute_mapping = {
    "google.subject" = "assertion.arn"
  }
  attribute_condition = "assertion.arn.startsWith('arn:aws:sts::${local.aws_account_id}:assumed-role/${local.aws_role_name}/')"

  aws {
    account_id = local.aws_account_id
  }
}

resource "google_service_account" "control" {
  project      = var.project_id
  account_id   = "dander-aws-control"
  display_name = "Dander AWS Control"

  depends_on = [google_project_service.required]
}

resource "google_service_account_iam_member" "aws_impersonation" {
  service_account_id = google_service_account.control.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.aws_control.name}/*"
}

resource "google_project_iam_member" "cloud_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.control.email}"
}

resource "google_project_iam_member" "artifact_registry_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.control.email}"
}

resource "google_project_iam_member" "logging_viewer" {
  project = var.project_id
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${google_service_account.control.email}"
}

resource "google_service_account_iam_member" "act_as_runtime" {
  for_each = var.runtime_service_account_names

  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.control.email}"
}
