data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_vpc" "selected" {
  id = var.vpc_id
}

data "aws_subnet" "selected" {
  for_each = toset(var.subnet_ids)
  id       = each.value
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

locals {
  prefix              = "${var.name}-d7"
  cluster_name        = "${local.prefix}-control"
  control_name        = "${local.prefix}-control"
  druff_name          = "${local.prefix}-druff"
  ecr_repository_name = split("/", var.ecr_repository_url)[1]
  ecr_repository_arn  = "arn:${data.aws_partition.current.partition}:ecr:${var.region}:${var.aws_account_id}:repository/${local.ecr_repository_name}"
  full_profile = (
    !var.foundation_only &&
    var.cloudfront_distribution_id != null &&
    var.cloudfront_domain != null &&
    var.dander_image != null &&
    var.druff_image != null &&
    length(var.control_args) > 0 &&
    var.control_oidc_json != "" &&
    var.graph_store_json != "" &&
    (length(var.execution_plan_json) == 0 || var.platforms_config_yaml != "") &&
    var.bootstrap_json != "" &&
    var.druff_caddyfile != ""
  )
  schedule_profile = local.full_profile && length(var.control_schedules) > 0
  tags = {
    managed-by = "dander"
    phase      = "d7"
    profile    = "aws-control"
  }
  control_config_script = <<-PYTHON
    import base64
    import json
    import os
    from pathlib import Path

    for variable, destination in (
        ("CONTROL_OIDC_B64", "/config/oidc/control-oidc.json"),
        ("GRAPH_STORE_B64", "/config/graph-store/control-graph-store.json"),
        ("PLATFORMS_CONFIG_B64", "/config/dander.platforms.yaml"),
    ):
        path = Path(destination)
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(os.environ[variable], validate=True))
        path.chmod(0o444)

    for variable, destination in (
        ("EXECUTION_PLANS_B64", "/config/orchestration/plans"),
        ("TRIGGER_SPECS_B64", "/config/orchestration/triggers"),
    ):
        documents = json.loads(base64.b64decode(os.environ[variable], validate=True))
        if not isinstance(documents, dict):
            raise ValueError("Control orchestration config must be an object")
        root = Path(destination)
        root.mkdir(mode=0o755, parents=True, exist_ok=True)
        for name, contents in documents.items():
            if not isinstance(name, str) or not isinstance(contents, str):
                raise ValueError("Control orchestration config entry is invalid")
            path = root / f"{name}.json"
            path.write_text(contents, encoding="utf-8")
            path.chmod(0o444)
  PYTHON
  druff_config_script   = <<-PYTHON
    import base64
    import os
    from pathlib import Path

    for variable, destination in (
        ("DRUFF_BOOTSTRAP_B64", "/config/bootstrap/bootstrap.json"),
        ("DRUFF_CADDY_B64", "/config/caddy/Caddyfile"),
    ):
        path = Path(destination)
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(os.environ[variable], validate=True))
        path.chmod(0o444)
  PYTHON
}

check "authenticated_account_matches_projection" {
  assert {
    condition     = data.aws_caller_identity.current.account_id == var.aws_account_id
    error_message = "Authenticated AWS account does not match aws_account_id."
  }
}

check "deployment_role_matches_projection" {
  assert {
    condition     = startswith(var.deployment_role_arn, "arn:${data.aws_partition.current.partition}:iam::${var.aws_account_id}:role/")
    error_message = "deployment_role_arn does not belong to this partition and account."
  }
}

check "selected_subnets_match_vpc" {
  assert {
    condition = (
      length(toset([for subnet in data.aws_subnet.selected : subnet.availability_zone])) >= 2 &&
      alltrue([for subnet in data.aws_subnet.selected : subnet.vpc_id == var.vpc_id])
    )
    error_message = "The selected subnets must span two availability zones in the selected VPC."
  }
}

check "full_profile_is_complete" {
  assert {
    condition     = var.foundation_only || local.full_profile
    error_message = "A complete AWS profile requires CloudFront identity, images, and all startup configuration."
  }
}

check "control_orchestration_is_consistent" {
  assert {
    condition = (
      length(var.trigger_spec_json) == length(var.control_schedules) &&
      length(setsubtract(toset(keys(var.trigger_spec_json)), toset(keys(var.control_schedules)))) == 0 &&
      length(setsubtract(toset(keys(var.control_schedules)), toset(keys(var.trigger_spec_json)))) == 0 &&
      alltrue([for schedule in values(var.control_schedules) :
        contains(keys(var.execution_plan_json), schedule.plan_revision)
      ])
    )
    error_message = "Control schedules require matching trigger specs and registered plan revisions."
  }
}

resource "aws_s3_bucket" "graphs" {
  bucket        = var.graph_bucket
  force_destroy = true
  tags          = merge(local.tags, { purpose = "control-graph-store" })
}

resource "aws_s3_bucket_ownership_controls" "graphs" {
  bucket = aws_s3_bucket.graphs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "graphs" {
  bucket = aws_s3_bucket.graphs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# The disposable graph bucket uses AWS-owned SSE-S3 as allowed by the GraphStore contract; a
# customer key would add a separate retained key and IAM boundary without protecting new data.
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "graphs" {
  bucket = aws_s3_bucket.graphs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "graphs" {
  bucket = aws_s3_bucket.graphs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_sqs_queue" "control_schedule_dlq" {
  count                      = local.schedule_profile ? 1 : 0
  name                       = "${local.prefix}-control-schedule-dlq"
  message_retention_seconds  = 1209600
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 120
  receive_wait_time_seconds  = 20
  tags                       = merge(local.tags, { purpose = "control-schedule-dlq" })
}

resource "aws_sqs_queue" "control_schedule" {
  count                      = local.schedule_profile ? 1 : 0
  name                       = "${local.prefix}-control-schedules"
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 120
  receive_wait_time_seconds  = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.control_schedule_dlq[0].arn
    maxReceiveCount     = 5
  })
  tags = merge(local.tags, { purpose = "control-schedule-wakeups" })
}

resource "aws_sqs_queue_redrive_allow_policy" "control_schedule_dlq" {
  count     = local.schedule_profile ? 1 : 0
  queue_url = aws_sqs_queue.control_schedule_dlq[0].id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.control_schedule[0].arn]
  })
}

resource "aws_sqs_queue_policy" "control_schedule" {
  count     = local.schedule_profile ? 1 : 0
  queue_url = aws_sqs_queue.control_schedule[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "sqs:*"
      Resource  = aws_sqs_queue.control_schedule[0].arn
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_sqs_queue_policy" "control_schedule_dlq" {
  count     = local.schedule_profile ? 1 : 0
  queue_url = aws_sqs_queue.control_schedule_dlq[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "sqs:*"
      Resource  = aws_sqs_queue.control_schedule_dlq[0].arn
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_security_group" "alb" {
  name        = "${local.prefix}-alb"
  description = "CloudFront-only ingress for the disposable D7 ALB"
  vpc_id      = var.vpc_id
  tags        = merge(local.tags, { Name = "${local.prefix}-alb" })
}

resource "aws_security_group" "tasks" {
  name        = "${local.prefix}-tasks"
  description = "ALB ingress and bounded egress for D7 Fargate tasks"
  vpc_id      = var.vpc_id
  tags        = merge(local.tags, { Name = "${local.prefix}-tasks" })
}

resource "aws_vpc_security_group_ingress_rule" "alb_cloudfront" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP only from the AWS-managed CloudFront origin prefix list"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
}

resource "aws_vpc_security_group_egress_rule" "alb_control" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to the Control target group"
  from_port                    = 8770
  to_port                      = 8770
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_egress_rule" "alb_druff" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to the Druff target group"
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_control" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Control traffic only from the profile ALB"
  from_port                    = 8770
  to_port                      = 8770
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_druff" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Druff traffic only from the profile ALB"
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.alb.id
}

# The fixed HTTPS-only rule must reach both public OIDC endpoints and AWS service endpoints; those
# destinations cannot share one narrower static CIDR or managed prefix list in this profile.
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "tasks_https" {
  security_group_id = aws_security_group.tasks.id
  description       = "HTTPS for OIDC discovery, JWKS, ECR, logs, and S3"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "tasks_dns_udp" {
  security_group_id = aws_security_group.tasks.id
  description       = "VPC DNS over UDP"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
  cidr_ipv4         = data.aws_vpc.selected.cidr_block
}

resource "aws_vpc_security_group_egress_rule" "tasks_dns_tcp" {
  security_group_id = aws_security_group.tasks.id
  description       = "VPC DNS over TCP"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
  cidr_ipv4         = data.aws_vpc.selected.cidr_block
}

# CloudFront needs a public ALB origin without a custom domain; ingress is separately restricted to
# AWS's managed CloudFront origin-facing prefix list rather than the general internet.
#trivy:ignore:AVD-AWS-0053
resource "aws_lb" "profile" {
  name                       = "${local.prefix}-control"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = var.subnet_ids
  enable_deletion_protection = false
  drop_invalid_header_fields = true
  preserve_host_header       = false

  # ALB access logging intentionally remains at the provider default (disabled):
  # callback query values must not be persisted by the front proxy.
  tags = local.tags
}

resource "aws_lb_target_group" "control" {
  name        = "${local.prefix}-control"
  port        = 8770
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/readyz"
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }

  tags = local.tags
}

resource "aws_lb_target_group" "druff" {
  name        = "${local.prefix}-druff"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/readyz"
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }

  tags = local.tags
}

# The provider-generated CloudFront URL terminates viewer TLS. Without a custom domain/certificate,
# this CloudFront-only origin uses HTTP and is unreachable outside the managed prefix-list rule.
#trivy:ignore:AVD-AWS-0054
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.profile.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.druff.arn
  }

  tags = local.tags
}

resource "aws_lb_listener_rule" "control" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.control.arn
  }

  condition {
    path_pattern {
      values = ["/v1/*", "/healthz", "/readyz"]
    }
  }

  tags = local.tags
}

resource "aws_cloudfront_cache_policy" "api" {
  name        = "${local.prefix}-api-no-cache"
  comment     = "Never cache authenticated Dander Control responses"
  default_ttl = 0
  max_ttl     = 0
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = false
    enable_accept_encoding_gzip   = false

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_cache_policy" "static" {
  name        = "${local.prefix}-static-origin-controlled"
  comment     = "Honor Caddy no-store and immutable asset cache controls"
  default_ttl = 0
  max_ttl     = 31536000
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_origin_request_policy" "api" {
  name    = "${local.prefix}-api-viewer-request"
  comment = "Forward auth, CORS, conditional, idempotency, correlation, and cursor inputs"

  cookies_config {
    cookie_behavior = "none"
  }

  headers_config {
    # CloudFront does not reliably accept Authorization as an individual allow-list
    # member. Forward all viewer headers while the zero-TTL cache policy prevents
    # cross-viewer reuse; the application keeps its own closed CORS header set.
    header_behavior = "allViewer"
  }

  query_strings_config {
    query_string_behavior = "all"
  }
}

# WAF is outside the bounded disposable D7 acceptance profile; OIDC authorization remains the API
# boundary, and adding WAF would not narrow the deliberately public static-site surface.
#trivy:ignore:AVD-AWS-0011
resource "aws_cloudfront_distribution" "profile" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "Disposable Dander D7 Control and Druff profile"
  price_class     = "PriceClass_100"
  http_version    = "http2and3"

  origin {
    domain_name = aws_lb.profile.dns_name
    origin_id   = "dander-d7-alb"

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_keepalive_timeout = 5
      origin_protocol_policy   = "http-only"
      origin_read_timeout      = 30
      origin_ssl_protocols     = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "dander-d7-alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD", "OPTIONS"]
    cache_policy_id        = aws_cloudfront_cache_policy.static.id
    compress               = true
  }

  ordered_cache_behavior {
    path_pattern             = "/v1/*"
    target_origin_id         = "dander-d7-alb"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD", "OPTIONS"]
    cache_policy_id          = aws_cloudfront_cache_policy.api.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.api.id
    compress                 = false
  }

  ordered_cache_behavior {
    path_pattern             = "/healthz"
    target_origin_id         = "dander-d7-alb"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD", "OPTIONS"]
    cache_policy_id          = aws_cloudfront_cache_policy.api.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.api.id
    compress                 = false
  }

  ordered_cache_behavior {
    path_pattern             = "/readyz"
    target_origin_id         = "dander-d7-alb"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD", "OPTIONS"]
    cache_policy_id          = aws_cloudfront_cache_policy.api.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.api.id
    compress                 = false
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1"
  }

  tags = local.tags
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "control" {
  count             = local.full_profile ? 1 : 0
  name              = "/dander/${var.name}/d7/control"
  retention_in_days = 1
  skip_destroy      = false
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "druff" {
  count             = local.full_profile ? 1 : 0
  name              = "/dander/${var.name}/d7/druff"
  retention_in_days = 1
  skip_destroy      = false
  tags              = local.tags
}

resource "aws_iam_role" "execution" {
  count              = local.full_profile ? 1 : 0
  name               = "${local.prefix}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "execution" {
  count = local.full_profile ? 1 : 0

  statement {
    sid       = "EcrAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullAcceptedImages"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [local.ecr_repository_arn]
  }

  statement {
    sid    = "WriteTaskLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.control[0].arn}:*",
      "${aws_cloudwatch_log_group.druff[0].arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "execution" {
  count  = local.full_profile ? 1 : 0
  name   = "dander-d7-pull-and-logs"
  role   = aws_iam_role.execution[0].id
  policy = data.aws_iam_policy_document.execution[0].json
}

resource "aws_iam_role" "control_task" {
  count              = local.full_profile ? 1 : 0
  name               = "${local.prefix}-control-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = merge(local.tags, { component = "control" })
}

resource "aws_iam_role" "druff_task" {
  count              = local.full_profile ? 1 : 0
  name               = "${local.prefix}-druff-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = merge(local.tags, { component = "druff" })
}

data "aws_iam_policy_document" "control_graphs" {
  count = local.full_profile ? 1 : 0

  statement {
    sid    = "InspectGraphBucket"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:ListBucket",
      "s3:ListBucketVersions",
    ]
    resources = [aws_s3_bucket.graphs.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["dander-control/v1", "dander-control/v1/*"]
    }
  }

  statement {
    sid    = "OperateGraphObjects"
    effect = "Allow"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.graphs.arn}/dander-control/v1/*"]
  }
}

resource "aws_iam_role_policy" "control_graphs" {
  count  = local.full_profile ? 1 : 0
  name   = "dander-d7-graph-store"
  role   = aws_iam_role.control_task[0].id
  policy = data.aws_iam_policy_document.control_graphs[0].json
}

data "aws_iam_policy_document" "control_schedules" {
  count = local.schedule_profile ? 1 : 0

  statement {
    sid    = "ConsumeScheduleWakeups"
    effect = "Allow"
    actions = [
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage",
    ]
    resources = [aws_sqs_queue.control_schedule[0].arn]
  }
}

resource "aws_iam_role_policy" "control_schedules" {
  count  = local.schedule_profile ? 1 : 0
  name   = "dander-d7-control-schedules"
  role   = aws_iam_role.control_task[0].id
  policy = data.aws_iam_policy_document.control_schedules[0].json
}

data "aws_iam_policy_document" "scheduler_assume" {
  count = local.schedule_profile ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values = [
        "arn:${data.aws_partition.current.partition}:scheduler:${var.region}:${var.aws_account_id}:schedule-group/default"
      ]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count              = local.schedule_profile ? 1 : 0
  name               = "${local.prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume[0].json
  tags               = merge(local.tags, { component = "scheduler" })
}

data "aws_iam_policy_document" "scheduler_send" {
  count = local.schedule_profile ? 1 : 0

  statement {
    sid     = "SendScheduleWakeups"
    effect  = "Allow"
    actions = ["sqs:SendMessage"]
    resources = [
      aws_sqs_queue.control_schedule[0].arn,
      aws_sqs_queue.control_schedule_dlq[0].arn,
    ]
  }
}

resource "aws_iam_role_policy" "scheduler_send" {
  count  = local.schedule_profile ? 1 : 0
  name   = "dander-d7-send-schedule-wakeups"
  role   = aws_iam_role.scheduler[0].id
  policy = data.aws_iam_policy_document.scheduler_send[0].json
}

resource "aws_scheduler_schedule" "control" {
  for_each = var.foundation_only ? {} : var.control_schedules

  name                         = "${local.prefix}-schedule-${substr(sha256(each.key), 0, 12)}"
  description                  = "Dander Control trigger ${each.key}"
  state                        = each.value.enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = each.value.expression
  schedule_expression_timezone = each.value.time_zone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sqs_queue.control_schedule[0].arn
    role_arn = aws_iam_role.scheduler[0].arn
    input    = each.value.message

    dead_letter_config {
      arn = aws_sqs_queue.control_schedule_dlq[0].arn
    }

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }
}

resource "aws_ecs_cluster" "profile" {
  count = local.full_profile ? 1 : 0
  name  = local.cluster_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = local.tags
}

resource "aws_ecs_task_definition" "control" {
  count                    = local.full_profile ? 1 : 0
  family                   = local.control_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.execution[0].arn
  task_role_arn            = aws_iam_role.control_task[0].arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  volume {
    name                = "dander-tmp"
    configure_at_launch = false
  }

  volume {
    name                = "dander-config"
    configure_at_launch = false
  }

  container_definitions = jsonencode([
    {
      name                   = "config-init"
      image                  = var.dander_image
      essential              = false
      memory                 = 256
      readonlyRootFilesystem = true
      user                   = "0:0"
      entryPoint             = ["/app/.venv/bin/python", "-c"]
      command                = [local.control_config_script]
      environment = [
        { name = "CONTROL_OIDC_B64", value = base64encode(var.control_oidc_json) },
        { name = "GRAPH_STORE_B64", value = base64encode(var.graph_store_json) },
        { name = "PLATFORMS_CONFIG_B64", value = base64encode(var.platforms_config_yaml) },
        { name = "EXECUTION_PLANS_B64", value = base64encode(jsonencode(var.execution_plan_json)) },
        { name = "TRIGGER_SPECS_B64", value = base64encode(jsonencode(var.trigger_spec_json)) },
      ]
      linuxParameters = {
        initProcessEnabled = true
        capabilities       = { add = [], drop = ["ALL"] }
      }
      mountPoints = [
        { sourceVolume = "dander-config", containerPath = "/config", readOnly = false },
        { sourceVolume = "dander-tmp", containerPath = "/tmp", readOnly = false },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.control[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "config-init"
        }
      }
    },
    {
      name                   = "control"
      image                  = var.dander_image
      essential              = true
      memory                 = 1024
      readonlyRootFilesystem = true
      user                   = "65532:65532"
      command = concat(
        var.control_args,
        local.schedule_profile ? ["--schedule-queue-url", aws_sqs_queue.control_schedule[0].url] : [],
      )
      stopTimeout  = 30
      dependsOn    = [{ containerName = "config-init", condition = "SUCCESS" }]
      portMappings = [{ containerPort = 8770, hostPort = 8770, protocol = "tcp" }]
      linuxParameters = {
        initProcessEnabled = true
        capabilities       = { add = [], drop = ["ALL"] }
      }
      mountPoints = [
        { sourceVolume = "dander-config", containerPath = "/etc/dander", readOnly = true },
        { sourceVolume = "dander-tmp", containerPath = "/tmp", readOnly = false },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.control[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "control"
        }
      }
    },
  ])

  tags = local.tags
}

resource "aws_ecs_task_definition" "druff" {
  count                    = local.full_profile ? 1 : 0
  family                   = local.druff_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution[0].arn
  task_role_arn            = aws_iam_role.druff_task[0].arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  volume {
    name                = "dander-tmp"
    configure_at_launch = false
  }

  volume {
    name                = "dander-config"
    configure_at_launch = false
  }

  container_definitions = jsonencode([
    {
      name                   = "config-init"
      image                  = var.dander_image
      essential              = false
      memory                 = 256
      readonlyRootFilesystem = true
      user                   = "0:0"
      entryPoint             = ["/app/.venv/bin/python", "-c"]
      command                = [local.druff_config_script]
      environment = [
        { name = "DRUFF_BOOTSTRAP_B64", value = base64encode(var.bootstrap_json) },
        { name = "DRUFF_CADDY_B64", value = base64encode(var.druff_caddyfile) },
      ]
      linuxParameters = {
        initProcessEnabled = true
        capabilities       = { add = [], drop = ["ALL"] }
      }
      mountPoints = [
        { sourceVolume = "dander-config", containerPath = "/config", readOnly = false },
        { sourceVolume = "dander-tmp", containerPath = "/tmp", readOnly = false },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.druff[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "config-init"
        }
      }
    },
    {
      name                   = "druff"
      image                  = var.druff_image
      essential              = true
      memory                 = 512
      readonlyRootFilesystem = true
      user                   = "65532:65532"
      entryPoint             = ["/usr/bin/caddy"]
      command                = ["run", "--config", "/etc/dander/caddy/Caddyfile", "--adapter", "caddyfile"]
      stopTimeout            = 30
      dependsOn              = [{ containerName = "config-init", condition = "SUCCESS" }]
      portMappings           = [{ containerPort = 8080, hostPort = 8080, protocol = "tcp" }]
      linuxParameters = {
        initProcessEnabled = true
        capabilities       = { add = [], drop = ["ALL"] }
      }
      mountPoints = [
        { sourceVolume = "dander-config", containerPath = "/etc/dander", readOnly = true },
        { sourceVolume = "dander-tmp", containerPath = "/tmp", readOnly = false },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.druff[0].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "druff"
        }
      }
    },
  ])

  tags = local.tags
}

resource "aws_ecs_service" "control" {
  count                             = local.full_profile ? 1 : 0
  name                              = local.control_name
  cluster                           = aws_ecs_cluster.profile[0].id
  task_definition                   = aws_ecs_task_definition.control[0].arn
  desired_count                     = 1
  launch_type                       = "FARGATE"
  platform_version                  = "LATEST"
  health_check_grace_period_seconds = 60
  wait_for_steady_state             = true

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    assign_public_ip = true
    security_groups  = [aws_security_group.tasks.id]
    subnets          = var.subnet_ids
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.control.arn
    container_name   = "control"
    container_port   = 8770
  }

  depends_on = [aws_lb_listener.http]
  tags       = local.tags
}

resource "aws_ecs_service" "druff" {
  count                             = local.full_profile ? 1 : 0
  name                              = local.druff_name
  cluster                           = aws_ecs_cluster.profile[0].id
  task_definition                   = aws_ecs_task_definition.druff[0].arn
  desired_count                     = 1
  launch_type                       = "FARGATE"
  platform_version                  = "LATEST"
  health_check_grace_period_seconds = 60
  wait_for_steady_state             = true

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    assign_public_ip = true
    security_groups  = [aws_security_group.tasks.id]
    subnets          = var.subnet_ids
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.druff.arn
    container_name   = "druff"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.http]
  tags       = local.tags
}
