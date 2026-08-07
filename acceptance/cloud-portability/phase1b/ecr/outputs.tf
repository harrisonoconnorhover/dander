output "repository_url" {
  description = "ECR repository to receive the digest-preserving OCI copy."
  value       = aws_ecr_repository.runtime.repository_url
}

output "registry_id" {
  description = "AWS registry identifier used during ECR login."
  value       = aws_ecr_repository.runtime.registry_id
}
