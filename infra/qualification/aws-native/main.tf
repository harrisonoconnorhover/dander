data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  availability_zones = slice(data.aws_availability_zones.available.names, 0, 3)
  subnets = {
    for index, zone in local.availability_zones : zone => index
  }
  staging_prefix = "phase8/rc22/staging"
  tags = merge(var.tags, {
    candidate = "0.9.0rc22"
    phase     = "8"
  })
}

check "authenticated_account_matches_authorization" {
  assert {
    condition     = data.aws_caller_identity.current.account_id == var.aws_account_id
    error_message = "Authenticated AWS account does not match the qualification authorization."
  }
}

resource "aws_vpc" "profile" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.tags, { Name = var.name })
}

resource "aws_internet_gateway" "profile" {
  vpc_id = aws_vpc.profile.id

  tags = merge(local.tags, { Name = var.name })
}

resource "aws_subnet" "profile" {
  for_each = local.subnets

  vpc_id                  = aws_vpc.profile.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, each.value)
  map_public_ip_on_launch = true

  tags = merge(local.tags, { Name = "${var.name}-${each.key}" })
}

resource "aws_route_table" "profile" {
  vpc_id = aws_vpc.profile.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.profile.id
  }

  tags = merge(local.tags, { Name = var.name })
}

resource "aws_route_table_association" "profile" {
  for_each = aws_subnet.profile

  subnet_id      = each.value.id
  route_table_id = aws_route_table.profile.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.profile.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.profile.id]

  tags = merge(local.tags, { Name = "${var.name}-s3" })
}

resource "aws_security_group" "profile" {
  name        = var.name
  description = "Phase 8 AWS-native qualification data plane"
  vpc_id      = aws_vpc.profile.id

  tags = merge(local.tags, { Name = var.name })
}

resource "aws_vpc_security_group_ingress_rule" "postgresql" {
  security_group_id            = aws_security_group.profile.id
  referenced_security_group_id = aws_security_group.profile.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL state from the manifest-bound Fargate task"
}

resource "aws_vpc_security_group_ingress_rule" "redshift" {
  security_group_id            = aws_security_group.profile.id
  referenced_security_group_id = aws_security_group.profile.id
  from_port                    = 5439
  to_port                      = 5439
  ip_protocol                  = "tcp"
  description                  = "Redshift from the manifest-bound Fargate task"
}

resource "aws_vpc_security_group_egress_rule" "postgresql" {
  security_group_id            = aws_security_group.profile.id
  referenced_security_group_id = aws_security_group.profile.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL state from the manifest-bound Fargate task"
}

resource "aws_vpc_security_group_egress_rule" "redshift" {
  security_group_id            = aws_security_group.profile.id
  referenced_security_group_id = aws_security_group.profile.id
  from_port                    = 5439
  to_port                      = 5439
  ip_protocol                  = "tcp"
  description                  = "Redshift from the manifest-bound Fargate task"
}

# The fixed HTTPS-only rule must reach both AWS service endpoints and the public qualification
# source; those destinations cannot share one narrower static CIDR or managed prefix list.
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "internet" {
  security_group_id = aws_security_group.profile.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "TLS access to AWS APIs and the public qualification source"
}

resource "aws_s3_bucket" "staging" {
  bucket        = "${var.name}-${var.aws_account_id}-staging"
  force_destroy = true

  tags = local.tags
}

resource "aws_s3_bucket_public_access_block" "staging" {
  bucket = aws_s3_bucket.staging.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# The qualification bucket holds only disposable public-source staging objects, expires them after
# one day, and is destroyed with the data plane. Reusing the retained stage-zero key would also
# require widening the runtime and Redshift COPY-role KMS contract without protecting new data.
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "staging" {
  bucket = aws_s3_bucket.staging.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "staging" {
  bucket = aws_s3_bucket.staging.id

  rule {
    id     = "expire-qualification-staging"
    status = "Enabled"

    filter {
      prefix = "${local.staging_prefix}/"
    }

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "redshift_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["redshift.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "redshift_copy" {
  name               = "${var.name}-redshift-copy"
  assume_role_policy = data.aws_iam_policy_document.redshift_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "redshift_copy" {
  statement {
    sid       = "InspectStagingBucket"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.staging.arn]
  }

  statement {
    sid       = "ListStagedObjects"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.staging.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.staging_prefix}/*"]
    }
  }

  statement {
    sid       = "ReadStagedObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.staging.arn}/${local.staging_prefix}/*"]
  }
}

resource "aws_iam_role_policy" "redshift_copy" {
  name   = "dander-read-staging"
  role   = aws_iam_role.redshift_copy.id
  policy = data.aws_iam_policy_document.redshift_copy.json
}

resource "aws_redshiftserverless_namespace" "profile" {
  namespace_name       = var.name
  db_name              = "analytics"
  default_iam_role_arn = aws_iam_role.redshift_copy.arn
  iam_roles            = [aws_iam_role.redshift_copy.arn]

  tags = local.tags
}

resource "aws_redshiftserverless_workgroup" "profile" {
  workgroup_name       = var.name
  namespace_name       = aws_redshiftserverless_namespace.profile.namespace_name
  base_capacity        = var.redshift_base_capacity_rpu
  enhanced_vpc_routing = true
  publicly_accessible  = false
  security_group_ids   = [aws_security_group.profile.id]
  subnet_ids           = [for subnet in aws_subnet.profile : subnet.id]

  tags = local.tags
}

resource "aws_redshiftserverless_usage_limit" "compute" {
  resource_arn  = aws_redshiftserverless_workgroup.profile.arn
  usage_type    = "serverless-compute"
  amount        = var.redshift_daily_usage_limit_rpu_hours
  period        = "daily"
  breach_action = "deactivate"
}

resource "aws_db_subnet_group" "profile" {
  name       = var.name
  subnet_ids = [for subnet in aws_subnet.profile : subnet.id]

  tags = merge(local.tags, { Name = var.name })
}

resource "random_password" "postgresql" {
  length           = 32
  special          = true
  override_special = "_-"
}

resource "aws_db_instance" "postgresql" {
  identifier = var.name

  engine                     = "postgres"
  engine_version             = "15"
  instance_class             = var.rds_instance_class
  allocated_storage          = 20
  storage_type               = "gp3"
  storage_encrypted          = true
  db_name                    = "dander_state"
  username                   = "dander_runtime"
  password                   = random_password.postgresql.result
  port                       = 5432
  db_subnet_group_name       = aws_db_subnet_group.profile.name
  vpc_security_group_ids     = [aws_security_group.profile.id]
  publicly_accessible        = false
  multi_az                   = false
  backup_retention_period    = 0
  auto_minor_version_upgrade = true
  deletion_protection        = false
  skip_final_snapshot        = true
  apply_immediately          = true

  tags = local.tags
}

resource "aws_secretsmanager_secret" "postgresql_dsn" {
  name_prefix             = "${var.name}/postgres-dsn-"
  recovery_window_in_days = 0

  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "postgresql_dsn" {
  secret_id = aws_secretsmanager_secret.postgresql_dsn.id
  secret_string = format(
    "postgresql://dander_runtime:%s@%s:%d/dander_state?sslmode=require",
    urlencode(random_password.postgresql.result),
    aws_db_instance.postgresql.address,
    aws_db_instance.postgresql.port,
  )
}
