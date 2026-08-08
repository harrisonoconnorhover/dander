output "runtime_repository_url" {
  description = "ECR repository URL for immutable runtime manifests."
  value       = aws_ecr_repository.runtime.repository_url
}

output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.runtime.arn
}

output "pipelines" {
  description = "Per-pipeline runtime and controller resources."
  value = {
    for id in keys(var.execution_projections) : id => {
      task_definition_arn = aws_ecs_task_definition.pipeline[id].arn
      task_role_arn       = aws_iam_role.task[id].arn
      state_machine_arn   = aws_sfn_state_machine.pipeline[id].arn
      schedule_arn        = aws_scheduler_schedule.pipeline[id].arn
      schedule_state      = aws_scheduler_schedule.pipeline[id].state
      log_group_name      = aws_cloudwatch_log_group.task[id].name
    }
  }
}

output "failure_queue_url" {
  description = "Encrypted queue for undelivered schedules and exhausted controller failures."
  value       = aws_sqs_queue.failures.url
}

output "failure_topic_arn" {
  description = "Notification topic for failed, timed-out, and aborted controller executions."
  value       = aws_sns_topic.failures.arn
}
