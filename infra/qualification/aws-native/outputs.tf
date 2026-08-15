output "network" {
  description = "Non-secret network coordinates consumed by the Fargate qualification profile."
  value = {
    security_group_id = aws_security_group.profile.id
    subnet_ids        = sort([for subnet in aws_subnet.profile : subnet.id])
    assign_public_ip  = true
  }
}

output "postgresql_secret_arn" {
  description = "Secret reference for the runtime-injected PostgreSQL state DSN."
  value       = aws_secretsmanager_secret.postgresql_dsn.arn
}

output "redshift" {
  description = "Non-secret Serverless coordinates consumed by the AWS-native profile."
  value = {
    database       = aws_redshiftserverless_namespace.profile.db_name
    database_role  = local.runtime_database_role
    host           = aws_redshiftserverless_workgroup.profile.endpoint[0].address
    port           = aws_redshiftserverless_workgroup.profile.endpoint[0].port
    workgroup_name = aws_redshiftserverless_workgroup.profile.workgroup_name
    copy_role_arn  = aws_iam_role.redshift_copy.arn
    staging_bucket = aws_s3_bucket.staging.id
    staging_prefix = local.staging_prefix
  }
}

output "qualification_boundary" {
  description = "Non-secret cost and ownership controls to retain with the reviewed plan."
  value = {
    name_prefix                         = var.name
    redshift_base_capacity_rpu          = var.redshift_base_capacity_rpu
    redshift_daily_usage_limit_rpu_hour = var.redshift_daily_usage_limit_rpu_hours
    redshift_breach_action              = aws_redshiftserverless_usage_limit.compute.breach_action
    rds_instance_class                  = var.rds_instance_class
    rds_storage_gib                     = aws_db_instance.postgresql.allocated_storage
  }
}
