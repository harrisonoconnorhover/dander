output "cluster_name" {
  value = aws_ecs_cluster.probe.name
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.probe.arn
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "subnet_id" {
  value = aws_subnet.public.id
}

output "security_group_id" {
  value = aws_security_group.task.id
}

output "workload_identity_provider_name" {
  value = google_iam_workload_identity_pool_provider.aws.name
}

output "google_service_account_email" {
  value = google_service_account.probe.email
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.probe.name
}
