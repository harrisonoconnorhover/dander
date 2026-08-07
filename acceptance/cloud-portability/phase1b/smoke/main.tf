data "aws_caller_identity" "current" {}

locals {
  name = "dander-phase1b"
}

resource "aws_cloudwatch_log_group" "probe" {
  name              = "/dander/phase1b"
  retention_in_days = 7
}

resource "aws_ecs_cluster" "probe" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
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
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  # Deliberately no AWS permission policy: temporary task-role identity is exchanged by Google WIF.
}

resource "aws_vpc" "probe" {
  cidr_block           = "10.77.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
}

resource "aws_internet_gateway" "probe" {
  vpc_id = aws_vpc.probe.id
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.probe.id
  cidr_block              = "10.77.1.0/24"
  map_public_ip_on_launch = true
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.probe.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.probe.id
  }
}

resource "aws_route_table_association" "public" {
  route_table_id = aws_route_table.public.id
  subnet_id      = aws_subnet.public.id
}

resource "aws_security_group" "task" {
  name        = local.name
  description = "Outbound-only network access for the Dander Phase 1B probe"
  vpc_id      = aws_vpc.probe.id

  egress {
    description = "HTTPS access to AWS, Google STS, and BigQuery APIs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "google_iam_workload_identity_pool" "aws" {
  workload_identity_pool_id = "dander-phase1b-aws"
  display_name              = "Dander Phase 1B AWS"
  description               = "Disposable keyless Fargate-to-Google identity proof"
}

resource "google_iam_workload_identity_pool_provider" "aws" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws.workload_identity_pool_id
  workload_identity_pool_provider_id = "fargate"
  display_name                       = "Dander Phase 1B Fargate"
  description                        = "Trusts only the isolated Dander ECS task role"

  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.extract('assumed-role/{role}/')"
  }
  attribute_condition = "assertion.arn.startsWith('arn:aws:sts::${data.aws_caller_identity.current.account_id}:assumed-role/${aws_iam_role.task.name}/')"

  aws {
    account_id = data.aws_caller_identity.current.account_id
  }
}

resource "google_service_account" "probe" {
  account_id   = "dander-phase1b-aws"
  display_name = "Dander Phase 1B AWS probe"
  description  = "Disposable BigQuery reader impersonated by the isolated Fargate task"
}

resource "google_service_account_iam_member" "aws_workload_identity" {
  service_account_id = google_service_account.probe.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.aws.name}/attribute.aws_role/${aws_iam_role.task.name}"
}

resource "google_project_iam_member" "bigquery_job_user" {
  project = var.gcp_project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.probe.email}"
}

resource "google_bigquery_dataset_iam_member" "proof_reader" {
  project    = var.gcp_project_id
  dataset_id = var.proof_dataset
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.probe.email}"
}

resource "aws_ecs_task_definition" "probe" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = var.cpu_architecture
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name                   = "probe"
      image                  = var.ecr_image
      essential              = true
      readonlyRootFilesystem = true
      entryPoint             = ["python", "/app/phase1b_probe.py"]
      command = [
        "--project",
        var.gcp_project_id,
        "--dataset",
        var.proof_dataset,
        "--table",
        var.proof_table,
      ]
      environment = [
        {
          name  = "GOOGLE_APPLICATION_CREDENTIALS"
          value = "/app/gcp-wif.json"
        },
        {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.gcp_project_id
        },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.probe.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "fargate"
        }
      }
    }
  ])

  depends_on = [
    aws_iam_role_policy_attachment.execution,
    google_service_account_iam_member.aws_workload_identity,
    google_project_iam_member.bigquery_job_user,
    google_bigquery_dataset_iam_member.proof_reader,
  ]
}
