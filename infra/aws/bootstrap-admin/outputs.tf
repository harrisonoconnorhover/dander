output "state_bucket" {
  description = "Encrypted S3 bucket for Dander Terraform state."
  value       = aws_s3_bucket.terraform_state.bucket
}

output "lock_table" {
  description = "DynamoDB table used for Dander Terraform locking."
  value       = aws_dynamodb_table.terraform_locks.name
}

output "runtime_repository_url" {
  description = "Private ECR repository receiving promoted Dander artifacts."
  value       = aws_ecr_repository.runtime.repository_url
}

output "deployment_role_arn" {
  description = "Dedicated role for later Dander platform plans and applies."
  value       = aws_iam_role.deployment.arn
}
