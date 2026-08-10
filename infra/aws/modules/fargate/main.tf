locals {
  partition = startswith(var.region, "us-gov-") ? "aws-us-gov" : "aws"
  cpu_units = {
    1000  = 1024
    2000  = 2048
    4000  = 4096
    8000  = 8192
    16000 = 16384
  }
  resource_names = {
    for id in keys(var.execution_projections) : id => join("-", compact([
      substr(var.name, 0, 20),
      substr(replace(id, "_", "-"), 0, 20),
      substr(sha1(id), 0, 8),
    ]))
  }
  task_role_names = {
    for id, projection in var.execution_projections : id => element(
      reverse(split("/", projection.workload_identity)),
      0,
    )
  }
  gcp_secret_environment = {
    for id, projection in var.execution_projections : id => {
      for name, binding in projection.secret_bindings :
      name => trimprefix(binding.reference, "gcp-sm://")
      if binding.provider == "gcp_secret_manager"
    }
  }
  aws_secret_environment = {
    for id, projection in var.execution_projections : id => {
      for name, binding in projection.secret_bindings : name => binding.reference
      if binding.provider == "aws_secret_manager"
    }
  }
  aws_secret_arns = {
    for id, bindings in local.aws_secret_environment : id => toset([
      for reference in values(bindings) : trimprefix(reference, "aws-sm://")
    ])
  }
  container_environment = {
    for id, projection in var.execution_projections : id => merge(
      projection.environment,
      local.gcp_secret_environment[id],
      local.aws_secret_environment[id],
    )
  }
  all_schedule_arns = [
    for id in keys(var.execution_projections) :
    "arn:${local.partition}:scheduler:${var.region}:${var.aws_account_id}:schedule/default/${local.resource_names[id]}"
  ]
  tags = merge(var.tags, {
    component  = "fargate-launcher"
    managed-by = "dander"
  })
}

data "aws_caller_identity" "current" {}

check "authenticated_account_matches_projection" {
  assert {
    condition     = data.aws_caller_identity.current.account_id == var.aws_account_id
    error_message = "Authenticated AWS account does not match aws_account_id."
  }
}

check "secret_references_match_deployment" {
  assert {
    condition = alltrue(flatten([
      for projection in values(var.execution_projections) : [
        for binding in values(projection.secret_bindings) :
        binding.provider == "gcp_secret_manager" || startswith(
          binding.reference,
          "aws-sm://arn:${local.partition}:secretsmanager:${var.region}:${var.aws_account_id}:secret:",
        )
      ]
    ]))
    error_message = "AWS secret references must be full ARNs in this account and region."
  }
}

data "aws_ecr_repository" "runtime" {
  name = var.ecr_repository_name
}

resource "aws_ecs_cluster" "runtime" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = local.tags
}

resource "aws_cloudwatch_log_group" "task" {
  for_each = var.execution_projections

  name              = "/dander/${var.name}/${each.key}"
  retention_in_days = each.value.observability.retention_days
  skip_destroy      = false
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "controller" {
  for_each = var.execution_projections

  name              = "/aws/vendedlogs/states/${local.resource_names[each.key]}"
  retention_in_days = each.value.observability.retention_days
  skip_destroy      = false
  tags              = local.tags
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

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid       = "EcrAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullRuntimeImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [data.aws_ecr_repository.runtime.arn]
  }

  statement {
    sid    = "WriteTaskLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [for group in aws_cloudwatch_log_group.task : "${group.arn}:*"]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "dander-runtime-pull-and-logs"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

resource "aws_iam_role" "task" {
  for_each = var.execution_projections

  name                 = local.task_role_names[each.key]
  max_session_duration = 3600
  assume_role_policy   = data.aws_iam_policy_document.ecs_assume.json
  tags                 = merge(local.tags, { pipeline = each.key })

  lifecycle {
    precondition {
      condition = each.value.workload_identity == (
        "arn:${local.partition}:iam::${var.aws_account_id}:role/${local.task_role_names[each.key]}"
      )
      error_message = "Fargate workload_identity must be one path-free task role in this AWS account."
    }
  }
}

data "aws_iam_policy_document" "task_secrets" {
  for_each = {
    for id, arns in local.aws_secret_arns : id => arns if length(arns) > 0
  }

  statement {
    sid       = "ReadDeclaredSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = each.value
  }
}

resource "aws_iam_role_policy" "task_secrets" {
  for_each = data.aws_iam_policy_document.task_secrets

  name   = "dander-declared-secrets"
  role   = aws_iam_role.task[each.key].id
  policy = each.value.json
}

resource "aws_ecs_task_definition" "pipeline" {
  for_each = var.execution_projections

  family                   = local.resource_names[each.key]
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(local.cpu_units[each.value.resources.cpu_millis])
  memory                   = tostring(each.value.resources.memory_mib)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task[each.key].arn

  runtime_platform {
    cpu_architecture        = each.value.extensions.fargate_architecture
    operating_system_family = "LINUX"
  }

  dynamic "ephemeral_storage" {
    for_each = each.value.resources.ephemeral_storage_mib > 20480 ? [1] : []
    content {
      size_in_gib = each.value.resources.ephemeral_storage_mib / 1024
    }
  }

  volume {
    name = "dander-tmp"
  }

  container_definitions = jsonencode([
    {
      name                   = "dander"
      image                  = each.value.image
      essential              = true
      readonlyRootFilesystem = true
      user                   = "65532:65532"
      command                = each.value.command
      stopTimeout            = tonumber(each.value.extensions.fargate_stop_timeout_seconds)
      environment = [
        for name in sort(keys(local.container_environment[each.key])) : {
          name  = name
          value = local.container_environment[each.key][name]
        }
      ]
      linuxParameters = {
        initProcessEnabled = true
      }
      mountPoints = [
        {
          sourceVolume  = "dander-tmp"
          containerPath = "/tmp"
          readOnly      = false
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.task[each.key].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "runtime"
        }
      }
    }
  ])

  tags = merge(local.tags, { pipeline = each.key })

  lifecycle {
    precondition {
      condition = startswith(
        each.value.image,
        "${data.aws_ecr_repository.runtime.repository_url}@sha256:",
      )
      error_message = "Fargate images must use an immutable digest in this stack's ECR repository."
    }
    precondition {
      condition = (
        each.value.resources.ephemeral_storage_mib == 20480 ||
        (
          each.value.resources.ephemeral_storage_mib % 1024 == 0 &&
          each.value.resources.ephemeral_storage_mib >= 21504
        )
      )
      error_message = "Fargate ephemeral storage must be the 20 GiB default or a whole GiB from 21-200."
    }
  }
}

resource "aws_sqs_queue" "failures" {
  name                      = "${var.name}-failures"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = local.tags
}

data "aws_iam_policy_document" "failure_topic_key" {
  statement {
    sid       = "AccountAdministration"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${var.aws_account_id}:root"]
    }
  }

  statement {
    sid    = "EventBridgeFailurePublication"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_kms_key" "failure_topic" {
  description             = "Encrypt Dander Fargate controller failure notifications"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.failure_topic_key.json
  tags                    = local.tags
}

resource "aws_kms_alias" "failure_topic" {
  name          = "alias/${var.name}-failures"
  target_key_id = aws_kms_key.failure_topic.key_id
}

resource "aws_sns_topic" "failures" {
  name              = "${var.name}-failures"
  kms_master_key_id = aws_kms_key.failure_topic.arn
  tags              = local.tags
}

data "aws_iam_policy_document" "states_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "controller" {
  for_each = var.execution_projections

  name               = "${local.resource_names[each.key]}-controller"
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
  tags               = merge(local.tags, { pipeline = each.key })
}

data "aws_iam_policy_document" "controller" {
  for_each = var.execution_projections

  statement {
    sid       = "RunPinnedTaskDefinition"
    effect    = "Allow"
    actions   = ["ecs:RunTask"]
    resources = [aws_ecs_task_definition.pipeline[each.key].arn]
  }

  statement {
    sid    = "ObserveAndStopOwnedTasks"
    effect = "Allow"
    actions = [
      "ecs:DescribeTasks",
      "ecs:StopTask",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "PassExactTaskRoles"
    effect  = "Allow"
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.execution.arn,
      aws_iam_role.task[each.key].arn,
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid    = "ObserveSynchronousTask"
    effect = "Allow"
    actions = [
      "events:DescribeRule",
      "events:PutRule",
      "events:PutTargets",
    ]
    resources = [
      "arn:${local.partition}:events:${var.region}:${var.aws_account_id}:rule/StepFunctionsGetEventsForECSTaskRule"
    ]
  }

  statement {
    sid       = "RecordExhaustedFailure"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.failures.arn]
  }

  # Step Functions log-delivery APIs do not support resource-level permissions.
  statement {
    sid    = "DeliverControllerLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:DescribeLogGroups",
      "logs:DescribeResourcePolicies",
      "logs:GetLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:UpdateLogDelivery",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "controller" {
  for_each = var.execution_projections

  name   = "dander-fargate-controller"
  role   = aws_iam_role.controller[each.key].id
  policy = data.aws_iam_policy_document.controller[each.key].json
}

resource "aws_sfn_state_machine" "pipeline" {
  for_each = var.execution_projections

  name     = local.resource_names[each.key]
  role_arn = aws_iam_role.controller[each.key].arn
  type     = "STANDARD"

  logging_configuration {
    include_execution_data = false
    level                  = "ERROR"
    log_destination        = "${aws_cloudwatch_log_group.controller[each.key].arn}:*"
  }

  definition = jsonencode({
    Comment        = "Dander bounded whole-task Fargate controller"
    StartAt        = "Initialize"
    TimeoutSeconds = each.value.resources.deadline_seconds
    States = {
      Initialize = {
        Type = "Pass"
        Parameters = {
          "run_id.$"                 = "States.Format('${each.key}:{}', $.scheduled_time)"
          "scheduled_time.$"         = "$.scheduled_time"
          "scheduler_attempt.$"      = "$.scheduler_attempt"
          "scheduler_execution_id.$" = "$.scheduler_execution_id"
          launcher_attempt           = 1
        }
        Next = "Run task"
      }
      "Run task" = {
        Type           = "Task"
        Resource       = "arn:${local.partition}:states:::ecs:runTask.sync"
        TimeoutSeconds = each.value.resources.deadline_seconds
        Parameters = {
          Cluster              = aws_ecs_cluster.runtime.arn
          TaskDefinition       = aws_ecs_task_definition.pipeline[each.key].arn
          LaunchType           = "FARGATE"
          PlatformVersion      = "LATEST"
          EnableExecuteCommand = false
          PropagateTags        = "TASK_DEFINITION"
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              AssignPublicIp = upper(each.value.extensions.fargate_assign_public_ip)
              SecurityGroups = split(",", each.value.network.extensions.fargate_security_group_ids)
              Subnets        = split(",", each.value.network.extensions.fargate_subnet_ids)
            }
          }
          Overrides = {
            ContainerOverrides = [
              {
                Name = "dander"
                Environment = [
                  {
                    Name      = "DANDER_RUN_ID"
                    "Value.$" = "$.run_id"
                  },
                  {
                    Name      = "DANDER_LAUNCHER_EXECUTION_ID"
                    "Value.$" = "$$.Execution.Id"
                  },
                  {
                    Name      = "DANDER_ATTEMPT"
                    "Value.$" = "States.Format('{}', $.launcher_attempt)"
                  },
                ]
              }
            ]
          }
        }
        ResultSelector = {
          "task_arn.$"  = "$.TaskArn"
          "exit_code.$" = "$.Containers[0].ExitCode"
        }
        ResultPath = "$.task"
        Retry = [
          {
            ErrorEquals = [
              "ECS.ServerException",
              "ECS.ThrottlingException",
            ]
            IntervalSeconds = 2
            BackoffRate     = 2
            MaxAttempts     = 3
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.Timeout"]
            ResultPath  = "$.controller_failure"
            Next        = "Deadline failure"
          },
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.controller_failure"
            Next        = "Classify task failure"
          },
        ]
        Next = "Classify task"
      }
      "Classify task failure" = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.controller_failure.Error"
            StringEquals = "States.TaskFailed"
            Next         = "Decode runtime failure"
          },
        ]
        Default = "Controller failure"
      }
      "Decode runtime failure" = {
        Type = "Pass"
        Parameters = {
          "details.$" = "States.StringToJson($.controller_failure.Cause)"
        }
        ResultPath = "$.runtime_failure"
        Next       = "Validate runtime failure"
      }
      "Validate runtime failure" = {
        Type = "Choice"
        Choices = [
          {
            And = [
              {
                Variable  = "$.runtime_failure.details.TaskArn"
                IsPresent = true
              },
              {
                Variable  = "$.runtime_failure.details.Containers[0].ExitCode"
                IsPresent = true
              },
            ]
            Next = "Normalize runtime failure"
          },
        ]
        Default = "Controller failure"
      }
      "Normalize runtime failure" = {
        Type = "Pass"
        Parameters = {
          "task_arn.$"  = "$.runtime_failure.details.TaskArn"
          "exit_code.$" = "$.runtime_failure.details.Containers[0].ExitCode"
        }
        ResultPath = "$.task"
        Next       = "Classify task"
      }
      "Classify task" = {
        Type = "Choice"
        Choices = [
          {
            Variable      = "$.task.exit_code"
            NumericEquals = 0
            Next          = "Success"
          },
          {
            Variable      = "$.task.exit_code"
            NumericEquals = 75
            Next          = "Can retry"
          },
        ]
        Default = "Permanent failure"
      }
      "Can retry" = {
        Type = "Choice"
        Choices = [
          {
            Variable              = "$.launcher_attempt"
            NumericLessThanEquals = each.value.resources.launcher_retry_count
            Next                  = "Increment attempt"
          }
        ]
        Default = "Retry exhausted"
      }
      "Increment attempt" = {
        Type = "Pass"
        Parameters = {
          "run_id.$"                 = "$.run_id"
          "scheduled_time.$"         = "$.scheduled_time"
          "scheduler_attempt.$"      = "$.scheduler_attempt"
          "scheduler_execution_id.$" = "$.scheduler_execution_id"
          "launcher_attempt.$"       = "States.MathAdd($.launcher_attempt, 1)"
        }
        Next = "Run task"
      }
      Success = {
        Type = "Pass"
        Parameters = {
          contract                = "io.dander.runtime/v1"
          status                  = "succeeded"
          pipeline_id             = each.key
          "run_id.$"              = "$.run_id"
          "task_arn.$"            = "$.task.task_arn"
          "container_exit_code.$" = "$.task.exit_code"
          "launcher_attempt.$"    = "$.launcher_attempt"
          "scheduler_attempt.$"   = "$.scheduler_attempt"
        }
        End = true
      }
      "Permanent failure" = {
        Type = "Pass"
        Parameters = {
          failure = {
            contract                = "io.dander.runtime/v1"
            status                  = "failed"
            failure_code            = "runtime_permanent_failure"
            pipeline_id             = each.key
            "run_id.$"              = "$.run_id"
            "task_arn.$"            = "$.task.task_arn"
            "container_exit_code.$" = "$.task.exit_code"
            "launcher_attempt.$"    = "$.launcher_attempt"
          }
        }
        Next = "Record failure"
      }
      "Retry exhausted" = {
        Type = "Pass"
        Parameters = {
          failure = {
            contract                = "io.dander.runtime/v1"
            status                  = "failed"
            failure_code            = "runtime_retry_exhausted"
            pipeline_id             = each.key
            "run_id.$"              = "$.run_id"
            "task_arn.$"            = "$.task.task_arn"
            "container_exit_code.$" = "$.task.exit_code"
            "launcher_attempt.$"    = "$.launcher_attempt"
          }
        }
        Next = "Record failure"
      }
      "Deadline failure" = {
        Type = "Pass"
        Parameters = {
          failure = {
            contract             = "io.dander.runtime/v1"
            status               = "failed"
            failure_code         = "launcher_deadline_exceeded"
            pipeline_id          = each.key
            "run_id.$"           = "$.run_id"
            "launcher_attempt.$" = "$.launcher_attempt"
          }
        }
        Next = "Record failure"
      }
      "Controller failure" = {
        Type = "Pass"
        Parameters = {
          failure = {
            contract             = "io.dander.runtime/v1"
            status               = "failed"
            failure_code         = "launcher_control_plane_failed"
            pipeline_id          = each.key
            "run_id.$"           = "$.run_id"
            "launcher_attempt.$" = "$.launcher_attempt"
            "error_code.$"       = "$.controller_failure.Error"
          }
        }
        Next = "Record failure"
      }
      "Record failure" = {
        Type     = "Task"
        Resource = "arn:${local.partition}:states:::sqs:sendMessage"
        Parameters = {
          QueueUrl        = aws_sqs_queue.failures.url
          "MessageBody.$" = "States.JsonToString($.failure)"
        }
        ResultPath = null
        Next       = "Fail execution"
      }
      "Fail execution" = {
        Type  = "Fail"
        Error = "Dander.RuntimeFailure"
        Cause = "The Dander Fargate runtime did not succeed."
      }
    }
  })

  tags = merge(local.tags, { pipeline = each.key })

  depends_on = [aws_iam_role_policy.controller]
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  for_each = var.execution_projections

  name               = "${local.resource_names[each.key]}-schedule"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = merge(local.tags, { pipeline = each.key })
}

data "aws_iam_policy_document" "scheduler" {
  for_each = var.execution_projections

  statement {
    sid       = "StartExactStateMachine"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pipeline[each.key].arn]
  }

  statement {
    sid       = "DeliverScheduleFailures"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.failures.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  for_each = var.execution_projections

  name   = "dander-start-controller"
  role   = aws_iam_role.scheduler[each.key].id
  policy = data.aws_iam_policy_document.scheduler[each.key].json
}

resource "aws_scheduler_schedule" "pipeline" {
  for_each = var.execution_projections

  name                         = local.resource_names[each.key]
  state                        = each.value.schedule.paused ? "DISABLED" : "ENABLED"
  schedule_expression          = each.value.schedule.expression
  schedule_expression_timezone = each.value.schedule.time_zone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:${local.partition}:scheduler:::aws-sdk:sfn:startExecution"
    role_arn = aws_iam_role.scheduler[each.key].arn
    input = replace(replace(jsonencode({
      StateMachineArn = aws_sfn_state_machine.pipeline[each.key].arn
      Input = jsonencode({
        deployment_revision    = each.value.labels.image_digest
        scheduled_time         = "<aws.scheduler.scheduled-time>"
        scheduler_attempt      = "<aws.scheduler.attempt-number>"
        scheduler_execution_id = "<aws.scheduler.execution-id>"
      })
    }), "\\\\u003c", "<"), "\\\\u003e", ">")

    dead_letter_config {
      arn = aws_sqs_queue.failures.arn
    }

    retry_policy {
      maximum_event_age_in_seconds = var.scheduler_delivery_max_age_seconds
      maximum_retry_attempts       = var.scheduler_delivery_retry_count
    }
  }

  lifecycle {
    precondition {
      condition = (
        each.value.observability.alert_target == null ||
        each.value.observability.alert_target == aws_sns_topic.failures.arn
      )
      error_message = "A declared Fargate alert target must equal this stack's failure topic ARN."
    }
  }
}

resource "aws_cloudwatch_event_rule" "controller_failure" {
  name        = "${var.name}-controller-failures"
  description = "Dander Fargate controllers that failed, timed out, or were aborted."
  event_pattern = jsonencode({
    source      = ["aws.states"]
    detail-type = ["Step Functions Execution Status Change"]
    detail = {
      status = ["FAILED", "TIMED_OUT", "ABORTED"]
      stateMachineArn = [
        for machine in aws_sfn_state_machine.pipeline : machine.arn
      ]
    }
  })
  tags = local.tags
}

resource "aws_cloudwatch_event_target" "failure_queue" {
  rule = aws_cloudwatch_event_rule.controller_failure.name
  arn  = aws_sqs_queue.failures.arn
}

resource "aws_cloudwatch_event_target" "failure_topic" {
  rule = aws_cloudwatch_event_rule.controller_failure.name
  arn  = aws_sns_topic.failures.arn
}

data "aws_iam_policy_document" "failure_queue" {
  statement {
    sid       = "SchedulerDeadLetters"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.failures.arn]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = local.all_schedule_arns
    }
  }

  statement {
    sid       = "ControllerFailureEvents"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.failures.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.controller_failure.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "failures" {
  queue_url = aws_sqs_queue.failures.url
  policy    = data.aws_iam_policy_document.failure_queue.json
}

data "aws_iam_policy_document" "failure_topic" {
  statement {
    sid    = "AccountOwner"
    effect = "Allow"
    actions = [
      "sns:AddPermission",
      "sns:DeleteTopic",
      "sns:GetTopicAttributes",
      "sns:ListSubscriptionsByTopic",
      "sns:Publish",
      "sns:RemovePermission",
      "sns:SetTopicAttributes",
      "sns:Subscribe",
    ]
    resources = [aws_sns_topic.failures.arn]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${var.aws_account_id}:root"]
    }
  }

  statement {
    sid       = "ControllerFailureEvents"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.failures.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.controller_failure.arn]
    }
  }
}

resource "aws_sns_topic_policy" "failures" {
  arn    = aws_sns_topic.failures.arn
  policy = data.aws_iam_policy_document.failure_topic.json
}
