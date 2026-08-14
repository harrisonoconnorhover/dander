output "control_service" {
  description = "Exact deployed Dander Control service."
  value = {
    name                      = google_cloud_run_v2_service.control.name
    url                       = google_cloud_run_v2_service.control.uri
    expected_url              = local.control_url
    service_account           = google_service_account.control.email
    image                     = var.dander_image
    graph_bucket              = google_storage_bucket.graphs.name
    graph_soft_delete_seconds = google_storage_bucket.graphs.soft_delete_policy[0].retention_duration_seconds
  }
}

output "druff_service" {
  description = "Exact deployed Druff service."
  value = {
    name            = google_cloud_run_v2_service.druff.name
    url             = google_cloud_run_v2_service.druff.uri
    expected_url    = local.druff_url
    service_account = google_service_account.druff.email
    image           = var.druff_image
  }
}

output "config_versions" {
  description = "Numeric startup-config versions pinned into Cloud Run revisions."
  value       = { for name, version in google_secret_manager_secret_version.config : name => version.version }
}
