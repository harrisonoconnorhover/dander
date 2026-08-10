"""Static safety and lifecycle contracts for the construction-ready AWS stack."""

from pathlib import Path

ROOT = Path(__file__).parents[2]
AWS_ROOT = ROOT / "infra/aws"
MODULE = AWS_ROOT / "modules/fargate/main.tf"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_aws_root_is_separate_and_uses_native_remote_state() -> None:
    versions = _source(AWS_ROOT / "versions.tf")
    root = _source(AWS_ROOT / "main.tf")

    assert 'backend "s3" {}' in versions
    assert 'source = "./modules/fargate"' in root
    assert "google" not in versions
    assert "azurerm" not in versions


def test_aws_stage_zero_uses_customer_encryption_and_scoped_role_passing() -> None:
    stage_zero = _source(AWS_ROOT / "bootstrap-admin/main.tf")

    assert 'resource "aws_kms_key" "stage_zero"' in stage_zero
    assert "enable_key_rotation     = true" in stage_zero
    assert 'sse_algorithm     = "aws:kms"' in stage_zero
    assert 'sid    = "ManageDanderRoles"' in stage_zero
    assert '"arn:${local.partition}:iam::${var.aws_account_id}:role/dander-*"' in stage_zero
    assert 'variable = "aws:PrincipalArn"' in stage_zero


def test_fargate_task_is_immutable_nonroot_and_uses_separate_roles() -> None:
    module = _source(MODULE)
    stage_zero = _source(AWS_ROOT / "bootstrap-admin/main.tf")

    assert 'data "aws_ecr_repository" "runtime"' in module
    assert 'image_tag_mutability = "IMMUTABLE"' in stage_zero
    assert "scan_on_push = true" in stage_zero
    assert "readonlyRootFilesystem = true" in module
    assert 'user                   = "65532:65532"' in module
    assert 'containerPath = "/tmp"' in module
    assert "execution_role_arn       = aws_iam_role.execution.arn" in module
    assert "task_role_arn            = aws_iam_role.task[each.key].arn" in module
    assert "secretsmanager:GetSecretValue" in module
    assert "resources = each.value" in module


def test_controller_owns_deadline_and_only_runtime_exit_75_retries() -> None:
    module = _source(MODULE)
    variables = _source(AWS_ROOT / "modules/fargate/variables.tf")

    assert module.count('Resource       = "arn:${local.partition}:states:::ecs:runTask.sync"') == 1
    assert "TimeoutSeconds = each.value.resources.deadline_seconds" in module
    assert "projection.resources.deadline_seconds <= 86400" in variables
    assert "NumericEquals = 75" in module
    assert 'StringEquals = "States.TaskFailed"' in module
    assert '"details.$" = "States.StringToJson($.controller_failure.Cause)"' in module
    assert 'Next        = "Classify task failure"' in module
    assert 'Default = "Controller failure"' in module
    assert 'failure_code            = "runtime_retry_exhausted"' in module
    assert 'failure_code         = "launcher_deadline_exceeded"' in module
    assert '"ecs:StopTask"' in module
    assert '"ecs:DescribeTasks"' in module
    assert '"iam:PassRole"' in module
    assert 'variable = "iam:PassedToService"' in module
    assert 'values   = ["ecs-tasks.amazonaws.com"]' in module


def test_scheduler_delivery_is_separate_paused_aware_and_dead_lettered() -> None:
    module = _source(MODULE)

    assert (
        'state                        = each.value.schedule.paused ? "DISABLED" : "ENABLED"'
        in module
    )
    assert 'arn      = "arn:${local.partition}:scheduler:::aws-sdk:sfn:startExecution"' in module
    assert 'Name            = "<aws.scheduler.execution-id>"' in module
    assert 'scheduled_time         = "<aws.scheduler.scheduled-time>"' in module
    assert 'scheduler_attempt      = "<aws.scheduler.attempt-number>"' in module
    assert "maximum_retry_attempts       = var.scheduler_delivery_retry_count" in module
    assert "launcher_retry_count" in module
    assert "dead_letter_config" in module
    assert 'status = ["FAILED", "TIMED_OUT", "ABORTED"]' in module
    assert 'resource "aws_kms_key" "failure_topic"' in module
    assert "enable_key_rotation     = true" in module
    assert "kms_master_key_id = aws_kms_key.failure_topic.arn" in module
    assert '"sns:*"' not in module.lower()
    for topic_action in (
        "sns:AddPermission",
        "sns:DeleteTopic",
        "sns:GetTopicAttributes",
        "sns:ListSubscriptionsByTopic",
        "sns:Publish",
        "sns:RemovePermission",
        "sns:SetTopicAttributes",
        "sns:Subscribe",
    ):
        assert f'"{topic_action}"' in module


def test_aws_stack_contains_no_static_cloud_credentials_or_apply_path() -> None:
    stack = "\n".join(
        _source(path)
        for path in AWS_ROOT.rglob("*")
        if path.is_file() and ".terraform" not in path.parts and path.name != ".terraform.lock.hcl"
    )

    assert "AWS_ACCESS_KEY_ID" not in stack
    assert "AWS_SECRET_ACCESS_KEY" not in stack
    assert "terraform apply" not in stack
    assert "private_key" not in stack.lower()


def test_fargate_remains_outside_the_public_supported_launcher_manifest() -> None:
    capabilities = _source(ROOT / "src/dander/runtime-capabilities.json")

    assert '"launchers": ["cloud_run", "local"]' in capabilities
    assert '"fargate"' not in capabilities
