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
      secret_bindings   = {}
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
