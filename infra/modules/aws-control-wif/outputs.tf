output "provider_resource_name" {
  description = "Google resource name of the exact AWS workload identity provider."
  value       = google_iam_workload_identity_pool_provider.aws_control.name
}

output "audience" {
  description = "Audience consumed by Dander's renewable AWS-to-Google credentials."
  value       = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.aws_control.name}"
}

output "service_account_email" {
  description = "GCP service account impersonated by AWS-hosted Control."
  value       = google_service_account.control.email
}
