output "runtime_repository_url" {
  description = "ECR repository URL used for immutable Dander runtime manifests."
  value       = module.fargate.runtime_repository_url
}

output "cluster_arn" {
  description = "ECS cluster hosting the Dander Fargate tasks."
  value       = module.fargate.cluster_arn
}

output "pipelines" {
  description = "Controller, task-definition, schedule, and task-role identifiers by pipeline."
  value       = module.fargate.pipelines
}

output "failure_queue_url" {
  description = "SQS queue receiving exhausted controller and delivery failures."
  value       = module.fargate.failure_queue_url
}

output "failure_topic_arn" {
  description = "SNS topic receiving failed, timed-out, or aborted controller events."
  value       = module.fargate.failure_topic_arn
}
