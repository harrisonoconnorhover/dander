mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"sts:AssumeRole\",\"Principal\":{\"Service\":\"ecs-tasks.amazonaws.com\"}}]}"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "184463061564"
      arn        = "arn:aws:iam::184463061564:root"
      user_id    = "184463061564"
    }
  }

  mock_data "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:us-east-1:184463061564:repository/dander"
      name           = "dander"
      registry_id    = "184463061564"
      repository_url = "184463061564.dkr.ecr.us-east-1.amazonaws.com/dander"
    }
  }

  mock_data "aws_redshift_cluster" {
    defaults = {
      arn                = "arn:aws:redshift:us-east-1:184463061564:cluster:dander-phase8"
      cluster_identifier = "dander-phase8"
    }
  }

  mock_data "aws_redshiftserverless_workgroup" {
    defaults = {
      arn            = "arn:aws:redshift-serverless:us-east-1:184463061564:workgroup/unit"
      workgroup_name = "dander-phase8"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::184463061564:role/dander-test-role"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:184463061564:log-group:/dander/test"
    }
  }

  mock_resource "aws_sqs_queue" {
    defaults = {
      arn = "arn:aws:sqs:us-east-1:184463061564:dander-test-failures"
      url = "https://sqs.us-east-1.amazonaws.com/184463061564/dander-test-failures"
    }
  }

  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-1:184463061564:dander-test-failures"
    }
  }
}

variables {
  name                               = "dander-portability"
  aws_account_id                     = "184463061564"
  region                             = "us-east-1"
  ecr_repository_name                = "dander"
  scheduler_delivery_retry_count     = 2
  scheduler_delivery_max_age_seconds = 3600
  tags                               = {}

  execution_projections = {
    greenhouse_jobs = {
      schema                  = "io.dander.execution/v1"
      contract                = "io.dander.runtime/v1"
      pipeline_id             = "greenhouse_jobs"
      profile_id              = "gcp"
      launcher                = "fargate"
      image                   = "184463061564.dkr.ecr.us-east-1.amazonaws.com/dander@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      command                 = ["runtime", "execute", "--contract", "io.dander.runtime/v1", "--pipeline", "greenhouse_jobs", "--platform", "gcp", "--config", "/app/dander.yaml"]
      configuration_reference = "/app/dander.yaml"
      environment = {
        DANDER_LAUNCHER = "fargate"
        GCP_PROJECT_ID  = "unit-project"
        HOME            = "/tmp"
        TMPDIR          = "/tmp"
      }
      secret_bindings = {
        DANDER_POSTGRES_DSN = {
          provider  = "aws_secret_manager"
          reference = "aws-sm://arn:aws:secretsmanager:us-east-1:184463061564:secret:dander/postgres-dsn-AbCdEf"
        }
      }
      workload_identity = "arn:aws:iam::184463061564:role/dander-runtime"
      resources = {
        cpu_millis            = 1000
        memory_mib            = 2048
        ephemeral_storage_mib = 20480
        deadline_seconds      = 900
        runtime_retry_count   = 0
        launcher_retry_count  = 1
      }
      schedule = {
        task_count          = 1
        maximum_parallelism = 1
        expression          = "cron(0 9 * * ? *)"
        time_zone           = "America/New_York"
        paused              = true
      }
      network = {
        placement = "awsvpc"
        extensions = {
          fargate_security_group_ids = "sg-0123456789abcdef0"
          fargate_subnet_ids         = "subnet-0123456789abcdef0"
        }
      }
      labels = {
        dander_version = "0.7.0"
        image_digest   = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        pipeline       = "greenhouse_jobs"
        profile        = "gcp"
      }
      observability = {
        log_destination  = "cloudwatch_logs"
        metric_namespace = "Dander"
        alert_target     = null
        retention_days   = 30
      }
      extensions = {
        fargate_architecture         = "ARM64"
        fargate_assign_public_ip     = "disabled"
        fargate_stop_timeout_seconds = "120"
      }
    }
  }
}

run "paused_bounded_controller" {
  command = plan

  assert {
    condition     = aws_scheduler_schedule.pipeline["greenhouse_jobs"].state == "DISABLED"
    error_message = "A paused Dander projection must create a disabled AWS schedule."
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.pipeline["greenhouse_jobs"].container_definitions)[0].readonlyRootFilesystem == true &&
      jsondecode(aws_ecs_task_definition.pipeline["greenhouse_jobs"].container_definitions)[0].user == "65532:65532" &&
      contains(jsondecode(aws_ecs_task_definition.pipeline["greenhouse_jobs"].container_definitions)[0].environment, { name = "HOME", value = "/tmp" }) &&
      contains(jsondecode(aws_ecs_task_definition.pipeline["greenhouse_jobs"].container_definitions)[0].environment, { name = "TMPDIR", value = "/tmp" }) &&
      contains(jsondecode(aws_ecs_task_definition.pipeline["greenhouse_jobs"].container_definitions)[0].environment, {
        name = "DANDER_SECRET_BINDINGS_JSON"
        value = jsonencode({
          DANDER_POSTGRES_DSN = {
            provider  = "aws_secret_manager"
            reference = "aws-sm://arn:aws:secretsmanager:us-east-1:184463061564:secret:dander/postgres-dsn-AbCdEf"
          }
        })
      }) &&
      !contains([for item in jsondecode(aws_ecs_task_definition.pipeline["greenhouse_jobs"].container_definitions)[0].environment : item.name], "DANDER_POSTGRES_DSN") &&
      contains(jsondecode(aws_ecs_task_definition.pipeline["greenhouse_jobs"].container_definitions)[0].mountPoints, { sourceVolume = "dander-tmp", containerPath = "/tmp", readOnly = false }) &&
      jsondecode(aws_ecs_task_definition.pipeline["greenhouse_jobs"].container_definitions)[0].stopTimeout == 120
    )
    error_message = "The Fargate task must remain non-root, read-only, and explicitly stoppable."
  }

  assert {
    condition = (
      aws_iam_role.execution.name !=
      aws_iam_role.task["greenhouse_jobs"].name
    )
    error_message = "ECS image-pull/log identity and runtime identity must be separate."
  }

  assert {
    condition     = data.aws_ecr_repository.runtime.name == "dander"
    error_message = "The Fargate stack must consume the stage-zero ECR repository."
  }
}

run "controller_result_selector" {
  command = apply

  assert {
    condition = (
      !contains(keys(jsondecode(aws_scheduler_schedule.pipeline["greenhouse_jobs"].target[0].input)), "Name") &&
      jsondecode(jsondecode(aws_scheduler_schedule.pipeline["greenhouse_jobs"].target[0].input).Input).scheduled_time == "<aws.scheduler.scheduled-time>" &&
      jsondecode(jsondecode(aws_scheduler_schedule.pipeline["greenhouse_jobs"].target[0].input).Input).scheduler_attempt == "<aws.scheduler.attempt-number>" &&
      jsondecode(jsondecode(aws_scheduler_schedule.pipeline["greenhouse_jobs"].target[0].input).Input).scheduler_execution_id == "<aws.scheduler.execution-id>"
    )
    error_message = "The schedule target must omit the optional execution name and preserve literal Scheduler context attributes in valid nested JSON."
  }

  assert {
    condition = (
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Run task"].ResultSelector["task_arn.$"] == "$.TaskArn" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Run task"].ResultSelector["exit_code.$"] == "$.Containers[0].ExitCode"
    )
    error_message = "The controller must normalize the top-level ecs:runTask.sync task ARN and container exit code."
  }

  assert {
    condition = (
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Run task"].Catch[0].ErrorEquals == ["States.Timeout"] &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Run task"].Catch[0].Next == "Deadline failure" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Run task"].Catch[1].ErrorEquals == ["States.ALL"] &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Run task"].Catch[1].Next == "Classify task failure"
    )
    error_message = "The controller must preserve timeout handling and inspect other task failures before classification."
  }

  assert {
    condition = (
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Classify task failure"].Choices[0].StringEquals == "States.TaskFailed" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Classify task failure"].Choices[0].Next == "Decode runtime failure" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Classify task failure"].Default == "Controller failure" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Decode runtime failure"].Parameters["details.$"] == "States.StringToJson($.controller_failure.Cause)" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Validate runtime failure"].Choices[0].And[0].Variable == "$.runtime_failure.details.TaskArn" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Validate runtime failure"].Choices[0].And[0].IsPresent == true &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Validate runtime failure"].Choices[0].And[1].Variable == "$.runtime_failure.details.Containers[0].ExitCode" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Validate runtime failure"].Choices[0].And[1].IsPresent == true &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Validate runtime failure"].Choices[0].Next == "Normalize runtime failure" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Validate runtime failure"].Default == "Controller failure" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Normalize runtime failure"].Parameters["task_arn.$"] == "$.runtime_failure.details.TaskArn" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Normalize runtime failure"].Parameters["exit_code.$"] == "$.runtime_failure.details.Containers[0].ExitCode" &&
      jsondecode(aws_sfn_state_machine.pipeline["greenhouse_jobs"].definition).States["Normalize runtime failure"].Next == "Classify task"
    )
    error_message = "Only genuine ECS runtime failures may be decoded and normalized into the existing exit-code classifier."
  }
}

run "aws_native_scoped_task_policy" {
  command = plan

  variables {
    aws_native_profile = {
      redshift_deployment         = "provisioned"
      redshift_cluster_identifier = "dander-phase8"
      redshift_workgroup_name     = null
      redshift_database           = "analytics"
      redshift_db_user            = "dander_runtime"
      staging_bucket              = "dander-phase8-staging"
      staging_prefix              = "dander/staging"
      glue_catalog_id             = "184463061564"
      glue_database_prefix        = "dander"
    }
  }

  assert {
    condition = (
      data.aws_redshift_cluster.native[0].cluster_identifier == "dander-phase8" &&
      aws_iam_role_policy.task_aws_native["greenhouse_jobs"].name == "dander-declared-aws-data-plane"
    )
    error_message = "The AWS-native profile must resolve its declared Redshift target and attach one scoped task policy."
  }
}

run "aws_native_rejects_wildcard_staging_prefix" {
  command = plan

  variables {
    aws_native_profile = {
      redshift_deployment         = "provisioned"
      redshift_cluster_identifier = "dander-phase8"
      redshift_workgroup_name     = null
      redshift_database           = "analytics"
      redshift_db_user            = "dander_runtime"
      staging_bucket              = "dander-phase8-staging"
      staging_prefix              = "dander/*"
      glue_catalog_id             = "184463061564"
      glue_database_prefix        = "dander"
    }
  }

  expect_failures = [var.aws_native_profile]
}
