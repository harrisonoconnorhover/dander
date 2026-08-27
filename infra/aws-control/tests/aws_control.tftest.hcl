mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"sts:AssumeRole\",\"Principal\":{\"Service\":\"ecs-tasks.amazonaws.com\"}}]}"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }

  mock_data "aws_vpc" {
    defaults = {
      id         = "vpc-12345678"
      cidr_block = "10.0.0.0/16"
    }
  }

  mock_data "aws_subnet" {
    defaults = {
      vpc_id            = "vpc-12345678"
      availability_zone = "us-east-1a"
    }
  }

  mock_data "aws_ec2_managed_prefix_list" {
    defaults = {
      id   = "pl-12345678"
      name = "com.amazonaws.global.cloudfront.origin-facing"
    }
  }

  mock_resource "aws_cloudfront_distribution" {
    defaults = {
      id          = "E123456789ABC"
      domain_name = "d123456789abc.cloudfront.net"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/dander-d7-unit"
    }
  }

  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/dander-d7-control/1234567890abcdef"
      dns_name = "dander-d7-control.us-east-1.elb.amazonaws.com"
    }
  }

  mock_resource "aws_lb_target_group" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/dander-d7-unit/1234567890abcdef"
    }
  }

  mock_resource "aws_lb_listener" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/dander-d7-control/1234567890abcdef/abcdef1234567890"
    }
  }

  mock_resource "aws_ecs_task_definition" {
    defaults = {
      arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/dander-d7-unit:1"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:/dander/dander/d7/unit"
    }
  }

  mock_resource "aws_sqs_queue" {
    defaults = {
      arn = "arn:aws:sqs:us-east-1:123456789012:dander-d7-unit"
      url = "https://sqs.us-east-1.amazonaws.com/123456789012/dander-d7-unit"
      id  = "https://sqs.us-east-1.amazonaws.com/123456789012/dander-d7-unit"
    }
  }
}

variables {
  aws_account_id             = "123456789012"
  region                     = "us-east-1"
  name                       = "dander"
  deployment_role_arn        = "arn:aws:iam::123456789012:role/dander-bootstrap"
  graph_bucket               = "dander-d7-unit-graphs"
  ecr_repository_url         = "123456789012.dkr.ecr.us-east-1.amazonaws.com/dander"
  vpc_id                     = "vpc-12345678"
  subnet_ids                 = ["subnet-11111111", "subnet-22222222"]
  foundation_only            = false
  cloudfront_distribution_id = "E123456789ABC"
  cloudfront_domain          = "d123456789abc.cloudfront.net"
  dander_image               = "123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  druff_image                = "123456789012.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  control_args               = ["control", "serve", "--host", "0.0.0.0", "--port", "8770", "--oidc-config", "/etc/dander/oidc/control-oidc.json", "--graph-store-config", "/etc/dander/graph-store/control-graph-store.json", "--platforms-config", "/etc/dander/dander.platforms.yaml"]
  control_oidc_json          = "{\"api_url\":\"https://d123456789abc.cloudfront.net\"}\n"
  graph_store_json           = "{\"bucket\":\"dander-d7-unit-graphs\",\"kind\":\"s3\"}\n"
  platforms_config_yaml      = "{\"version\":1}\n"
  bootstrap_json             = "{\"api_url\":\"https://d123456789abc.cloudfront.net\"}\n"
  druff_caddyfile            = ":8080 { root * /app file_server }\n"
  execution_plan_json = {
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" = "{\"schema\":\"io.dander.control.execution-plan/v1\"}"
  }
  control_fargate_bindings = {
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" = {
      execution_arn_prefix = "arn:aws:states:us-east-1:123456789012:execution:dander-hosted-graph-fad90246:"
      log_group_arn        = "arn:aws:logs:us-east-1:123456789012:log-group:/dander/dander/hosted_graph:*"
      state_machine_arn    = "arn:aws:states:us-east-1:123456789012:stateMachine:dander-hosted-graph-fad90246"
    }
  }
  trigger_spec_json = {
    "daily-redshift" = "{\"schema\":\"io.dander.control.trigger-spec/v1\"}"
  }
  control_schedules = {
    "daily-redshift" = {
      expression    = "cron(0 6 * * ? *)"
      time_zone     = "America/New_York"
      plan_revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      enabled       = true
      message       = "{\"plan_revision\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"scheduled_occurrence\":\"<aws.scheduler.scheduled-time>\",\"schema\":\"io.dander.control.schedule-wakeup/v1\",\"trigger_id\":\"daily-redshift\"}"
    }
  }
}

override_data {
  target = data.aws_subnet.selected["subnet-11111111"]
  values = {
    vpc_id            = "vpc-12345678"
    availability_zone = "us-east-1a"
  }
}

override_data {
  target = data.aws_subnet.selected["subnet-22222222"]
  values = {
    vpc_id            = "vpc-12345678"
    availability_zone = "us-east-1b"
  }
}

run "private_versioned_graphs_and_cloudfront_only_alb" {
  command = apply

  assert {
    condition = (
      aws_s3_bucket.graphs.force_destroy &&
      aws_s3_bucket_versioning.graphs.versioning_configuration[0].status == "Enabled" &&
      aws_s3_bucket_public_access_block.graphs.block_public_acls &&
      aws_s3_bucket_public_access_block.graphs.block_public_policy &&
      aws_s3_bucket_public_access_block.graphs.ignore_public_acls &&
      aws_s3_bucket_public_access_block.graphs.restrict_public_buckets
    )
    error_message = "The disposable S3 GraphStore must be private, versioned, and destroyable."
  }

  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.alb_cloudfront.prefix_list_id == data.aws_ec2_managed_prefix_list.cloudfront.id &&
      aws_vpc_security_group_ingress_rule.alb_cloudfront.from_port == 80 &&
      aws_vpc_security_group_ingress_rule.tasks_control.referenced_security_group_id == aws_security_group.alb.id &&
      aws_vpc_security_group_ingress_rule.tasks_druff.referenced_security_group_id == aws_security_group.alb.id
    )
    error_message = "The ALB must accept only CloudFront origins and tasks must accept only the ALB."
  }
}

run "api_is_never_cached_and_forwards_viewer_inputs_without_cookies" {
  command = plan

  assert {
    condition = (
      aws_cloudfront_cache_policy.api.default_ttl == 0 &&
      aws_cloudfront_cache_policy.api.min_ttl == 0 &&
      aws_cloudfront_cache_policy.api.max_ttl == 0 &&
      aws_cloudfront_origin_request_policy.api.headers_config[0].header_behavior == "allViewer" &&
      aws_cloudfront_origin_request_policy.api.cookies_config[0].cookie_behavior == "none" &&
      aws_cloudfront_origin_request_policy.api.query_strings_config[0].query_string_behavior == "all" &&
      aws_cloudfront_cache_policy.static.min_ttl == 0 &&
      aws_cloudfront_distribution.profile.viewer_certificate[0].minimum_protocol_version == "TLSv1"
    )
    error_message = "CloudFront must preserve authenticated mutable API semantics and origin cache controls."
  }

  assert {
    condition = toset([
      for behavior in aws_cloudfront_distribution.profile.ordered_cache_behavior : behavior.path_pattern
    ]) == toset(["/v1/*", "/healthz", "/readyz"])
    error_message = "Control API and both public probes must use explicit zero-cache behaviors."
  }
}

run "fargate_tasks_are_nonroot_readonly_and_config_init_is_ordered" {
  command = plan

  assert {
    condition = (
      alltrue([for volume in aws_ecs_task_definition.control[0].volume : !volume.configure_at_launch]) &&
      alltrue([for volume in aws_ecs_task_definition.druff[0].volume : !volume.configure_at_launch]) &&
      alltrue([
        for container in jsondecode(aws_ecs_task_definition.control[0].container_definitions) :
        container.linuxParameters.capabilities.add == []
      ]) &&
      alltrue([
        for container in jsondecode(aws_ecs_task_definition.druff[0].container_definitions) :
        container.linuxParameters.capabilities.add == []
      ])
    )
    error_message = "Fargate provider defaults must be explicit so repeat plans remain stable."
  }

  assert {
    condition = (
      sum([
        for container in jsondecode(aws_ecs_task_definition.control[0].container_definitions) :
        try(container.cpu, 0)
      ]) < tonumber(aws_ecs_task_definition.control[0].cpu) &&
      sum([
        for container in jsondecode(aws_ecs_task_definition.druff[0].container_definitions) :
        try(container.cpu, 0)
      ]) < tonumber(aws_ecs_task_definition.druff[0].cpu)
    )
    error_message = "Container CPU reservations must remain below each Fargate task CPU allocation."
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.control[0].container_definitions)[0].name == "config-init" &&
      jsondecode(aws_ecs_task_definition.control[0].container_definitions)[0].readonlyRootFilesystem &&
      jsondecode(aws_ecs_task_definition.control[0].container_definitions)[0].user == "0:0" &&
      length([
        for variable in jsondecode(aws_ecs_task_definition.control[0].container_definitions)[0].environment : variable
        if variable.name == "PLATFORMS_CONFIG_B64" && variable.value == base64encode(var.platforms_config_yaml)
      ]) == 1 &&
      jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].name == "control" &&
      jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].readonlyRootFilesystem &&
      jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].user == "65532:65532" &&
      jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].dependsOn[0].condition == "SUCCESS" &&
      contains(
        jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].command,
        "/etc/dander/dander.platforms.yaml",
      )
    )
    error_message = "Control must wait for bounded config init and then run nonroot on a read-only root."
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.druff[0].container_definitions)[1].name == "druff" &&
      jsondecode(aws_ecs_task_definition.druff[0].container_definitions)[1].readonlyRootFilesystem &&
      jsondecode(aws_ecs_task_definition.druff[0].container_definitions)[1].user == "65532:65532" &&
      jsondecode(aws_ecs_task_definition.druff[0].container_definitions)[1].dependsOn[0].condition == "SUCCESS" &&
      aws_ecs_service.control[0].desired_count == 1 &&
      aws_ecs_service.druff[0].desired_count == 1
    )
    error_message = "Druff must share the same bounded startup and single-instance experimental profile."
  }
}

run "only_control_receives_graph_permissions" {
  command = plan

  assert {
    condition = (
      aws_iam_role_policy.control_graphs[0].role == aws_iam_role.control_task[0].id &&
      aws_ecs_task_definition.control[0].task_role_arn == aws_iam_role.control_task[0].arn &&
      aws_ecs_task_definition.druff[0].task_role_arn == aws_iam_role.druff_task[0].arn
    )
    error_message = "S3 GraphStore authority must remain confined to the Control task role."
  }
}

run "schedules_use_encrypted_at_least_once_control_wakeups" {
  command = plan

  assert {
    condition = (
      aws_sqs_queue.control_schedule[0].sqs_managed_sse_enabled &&
      aws_sqs_queue.control_schedule_dlq[0].sqs_managed_sse_enabled &&
      aws_sqs_queue.control_schedule[0].receive_wait_time_seconds == 20 &&
      aws_sqs_queue.control_schedule[0].visibility_timeout_seconds == 120 &&
      jsondecode(aws_sqs_queue.control_schedule[0].redrive_policy).maxReceiveCount == 5 &&
      aws_sqs_queue.control_schedule_dlq[0].message_retention_seconds == 1209600
    )
    error_message = "Control schedule wakeups must use encrypted long-polling SQS with bounded redrive."
  }

  assert {
    condition = (
      aws_scheduler_schedule.control["daily-redshift"].state == "ENABLED" &&
      aws_scheduler_schedule.control["daily-redshift"].schedule_expression == "cron(0 6 * * ? *)" &&
      aws_scheduler_schedule.control["daily-redshift"].schedule_expression_timezone == "America/New_York" &&
      aws_scheduler_schedule.control["daily-redshift"].target[0].arn == aws_sqs_queue.control_schedule[0].arn &&
      aws_scheduler_schedule.control["daily-redshift"].target[0].dead_letter_config[0].arn == aws_sqs_queue.control_schedule_dlq[0].arn &&
      aws_scheduler_schedule.control["daily-redshift"].target[0].retry_policy[0].maximum_retry_attempts == 3
    )
    error_message = "EventBridge Scheduler must deliver exact occurrences to Control with a DLQ."
  }

  assert {
    condition = (
      aws_iam_role_policy.control_schedules[0].role == aws_iam_role.control_task[0].id &&
      aws_iam_role_policy.control_fargate[0].role == aws_iam_role.control_task[0].id &&
      aws_iam_role_policy.scheduler_send[0].role == aws_iam_role.scheduler[0].id &&
      endswith(
        jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].command[
          length(jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].command) - 1
        ],
        "dander-d7-unit",
      )
    )
    error_message = "Only Control may consume wakeups and only Scheduler may send them."
  }
}

run "managed_spark_plans_share_the_single_gcp_identity_handoff" {
  command = plan

  variables {
    execution_plan_json = {
      "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" = "{\"schema\":\"io.dander.control.execution-plan/v2\"}"
    }
    control_fargate_bindings         = {}
    control_cloud_run_plan_revisions = []
    control_dataproc_plan_revisions  = ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
    trigger_spec_json                = {}
    control_schedules                = {}
    gcp_control_service_account      = "dander-control@dander-unit-project.iam.gserviceaccount.com"
    gcp_wif_audience                 = "//iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/dander-control/providers/aws-control"
  }

  assert {
    condition = (
      length(jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].environment) == 2 &&
      contains(
        jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].environment[*].name,
        "DANDER_GCP_SERVICE_ACCOUNT",
      ) &&
      contains(
        jsondecode(aws_ecs_task_definition.control[0].container_definitions)[1].environment[*].name,
        "DANDER_GCP_WIF_AUDIENCE",
      )
    )
    error_message = "Managed Spark plans must reuse the one reviewed Google identity handoff."
  }
}
