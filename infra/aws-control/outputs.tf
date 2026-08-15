output "foundation" {
  description = "Provider-assigned identity used to close the complete hosted input."
  value = {
    cloudfront_distribution_id = aws_cloudfront_distribution.profile.id
    cloudfront_domain          = aws_cloudfront_distribution.profile.domain_name
    alb_arn                    = aws_lb.profile.arn
    graph_bucket               = aws_s3_bucket.graphs.id
  }
}

output "services" {
  description = "Exact active service identities; null during the foundation stage."
  value = nonsensitive(local.full_profile ? {
    cluster                 = aws_ecs_cluster.profile[0].name
    control_service         = aws_ecs_service.control[0].name
    control_task_definition = aws_ecs_task_definition.control[0].arn
    druff_service           = aws_ecs_service.druff[0].name
    druff_task_definition   = aws_ecs_task_definition.druff[0].arn
  } : null)
}
