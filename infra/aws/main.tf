module "fargate" {
  source = "./modules/fargate"

  name                               = var.name
  aws_account_id                     = var.aws_account_id
  region                             = var.region
  ecr_repository_name                = var.ecr_repository_name
  execution_projections              = var.execution_projections
  scheduler_delivery_retry_count     = var.scheduler_delivery_retry_count
  scheduler_delivery_max_age_seconds = var.scheduler_delivery_max_age_seconds
  tags                               = var.tags
}
