"""Provider-native ECS/Fargate execution controls through the AWS CLI."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

from dander.project import ProjectConfigError, load_project_config

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_AWS_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")
_AWS_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_AWS_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
_DEPLOYMENT_NAME = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
_EXECUTION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_PIPELINE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_TASK_ID = re.compile(r"^[0-9a-f]{32}$")
_ECR_IMAGE = re.compile(
    r"^(?P<account>[0-9]{12})\.dkr\.ecr\.(?P<region>[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+)"
    r"\.amazonaws\.com/(?P<repository>[a-z0-9]+(?:[._/-][a-z0-9]+)*)"
    r"@sha256:(?P<digest>[0-9a-f]{64})$"
)
_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"})


class FargateOperationError(RuntimeError):
    """Raised when an AWS execution operation cannot complete or returns invalid data."""


class _Runner(Protocol):
    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]: ...


def _subprocess_runner(
    args: tuple[str, ...],
    *,
    cwd: Path,
    check: bool,
    capture_output: bool,
    text: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=text,
    )


@dataclass(frozen=True, slots=True)
class FargateBinding:
    """Exact manifest pipeline to AWS controller-resource binding."""

    account_id: str
    region: str
    deployment_name: str
    pipeline_id: str
    resource_name: str
    state_machine_arn: str
    cluster_name: str
    log_group_name: str
    schedule_paused: bool
    project_dir: Path

    @classmethod
    def from_project(
        cls,
        *,
        config: Path,
        deployment: str,
        pipeline_id: str,
        name: str = "dander",
    ) -> FargateBinding:
        """Resolve one Fargate pipeline from the validated project manifest."""
        resolved_config = config.expanduser().resolve()
        if not _DEPLOYMENT_NAME.fullmatch(name):
            raise FargateOperationError("Invalid AWS deployment name")
        if not _PIPELINE_ID.fullmatch(pipeline_id):
            raise FargateOperationError("Invalid pipeline identifier")
        try:
            manifest = load_project_config(resolved_config, deployment=deployment)
            if manifest.launcher_provider != "fargate":
                raise ProjectConfigError(
                    f"Deployment {deployment!r} does not select launcher.provider='fargate'"
                )
            pipeline = manifest.pipelines[pipeline_id]
            manifest.validate_references(resolved_config.parent)
            launcher = manifest.resolved_launcher_config()
        except KeyError as error:
            raise FargateOperationError(
                f"Pipeline {pipeline_id!r} is not declared in the project manifest"
            ) from error
        except ProjectConfigError as error:
            raise FargateOperationError(str(error)) from error
        account_id = launcher.get("aws_account_id")
        region = launcher.get("region")
        if not isinstance(account_id, str) or _AWS_ACCOUNT_ID.fullmatch(account_id) is None:
            raise FargateOperationError("Fargate deployment has an invalid AWS account")
        if not isinstance(region, str) or _AWS_REGION.fullmatch(region) is None:
            raise FargateOperationError("Fargate deployment has an invalid AWS region")
        resource_name = _resource_name(name=name, pipeline_id=pipeline_id)
        partition = "aws-us-gov" if region.startswith("us-gov-") else "aws"
        return cls(
            account_id=account_id,
            region=region,
            deployment_name=deployment,
            pipeline_id=pipeline_id,
            resource_name=resource_name,
            state_machine_arn=(
                f"arn:{partition}:states:{region}:{account_id}:stateMachine:{resource_name}"
            ),
            cluster_name=name,
            log_group_name=f"/dander/{name}/{pipeline_id}",
            schedule_paused=pipeline.paused,
            project_dir=resolved_config.parent,
        )

    def validate_execution_arn(self, execution_arn: str) -> str:
        """Require an execution owned by this exact state machine."""
        state_machine_prefix = self.state_machine_arn.replace(":stateMachine:", ":execution:")
        prefix = state_machine_prefix + ":"
        if not execution_arn.startswith(prefix):
            raise FargateOperationError("Execution does not belong to the selected pipeline")
        name = execution_arn.removeprefix(prefix)
        if _EXECUTION_NAME.fullmatch(name) is None:
            raise FargateOperationError("Execution ARN has an invalid name")
        return name


@dataclass(frozen=True, slots=True)
class FargateExecution:
    """Small sanitized view of one Step Functions controller execution."""

    execution_arn: str
    name: str
    state: str
    started_at: str | None = None
    stopped_at: str | None = None
    run_id: str | None = None
    task_arn: str | None = None
    container_exit_code: int | None = None
    failure_code: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {"succeeded", "failed", "cancelled"}

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FargateLogEvent:
    """One CloudWatch event correlated to the selected ECS task."""

    timestamp: int
    message: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FargateDeploymentVerification:
    """Read-only verification result for one deployed pipeline."""

    state_machine: str
    cluster: str
    schedule: str
    schedule_state: str
    task_definition: str
    image: str
    log_group: str
    repository: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class FargateOperations:
    """Start, observe, cancel, replay, and verify one bound Fargate pipeline."""

    def __init__(
        self,
        binding: FargateBinding,
        *,
        aws_profile: str = "",
        runner: _Runner | None = None,
        clock: Callable[[], datetime] | None = None,
        nonce: Callable[[], str] | None = None,
    ) -> None:
        if aws_profile and _AWS_PROFILE.fullmatch(aws_profile) is None:
            raise FargateOperationError("Invalid AWS profile")
        self.binding = binding
        self._aws_prefix = ("aws", "--profile", aws_profile) if aws_profile else ("aws",)
        self._runner = runner or _subprocess_runner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._nonce = nonce or (lambda: uuid4().hex[:8])

    def start(self, *, execution_name: str | None = None) -> FargateExecution:
        """Start one manual controller execution."""
        now = self._clock().astimezone(UTC)
        name = execution_name or _new_execution_name("manual", now, self._nonce())
        return self._start(name=name, now=now, correlation=f"manual:{name}")

    def replay(
        self,
        execution_arn: str,
        *,
        execution_name: str | None = None,
    ) -> FargateExecution:
        """Start a fresh inclusive-boundary run after one terminal execution."""
        previous = self.describe(execution_arn)
        if not previous.terminal:
            raise FargateOperationError("Only a terminal execution can be replayed")
        now = self._clock().astimezone(UTC)
        name = execution_name or _new_execution_name("replay", now, self._nonce())
        return self._start(name=name, now=now, correlation=f"replay:{previous.name}")

    def latest(self) -> FargateExecution | None:
        """Return the latest controller execution, if one exists."""
        payload = self._json(
            "stepfunctions",
            "list-executions",
            "--state-machine-arn",
            self.binding.state_machine_arn,
            "--max-results",
            "1",
        )
        executions = payload.get("executions")
        if not isinstance(executions, list):
            raise FargateOperationError("AWS returned an invalid execution list")
        if not executions:
            return None
        summary = executions[0]
        if not isinstance(summary, dict) or not isinstance(summary.get("executionArn"), str):
            raise FargateOperationError("AWS returned an invalid execution summary")
        return self.describe(cast("str", summary["executionArn"]))

    def describe(self, execution_arn: str) -> FargateExecution:
        """Return normalized status for one owned execution."""
        name = self.binding.validate_execution_arn(execution_arn)
        payload = self._json(
            "stepfunctions", "describe-execution", "--execution-arn", execution_arn
        )
        if payload.get("executionArn") != execution_arn:
            raise FargateOperationError("AWS returned an unexpected execution")
        status = payload.get("status")
        if not isinstance(status, str):
            raise FargateOperationError("AWS returned an invalid execution status")
        output = _json_document(payload.get("output"))
        state = _normalized_state(status)
        details: object = output
        if state == "failed":
            details = [output, self._execution_history(execution_arn)]
        failure_code = _deep_string(details, "failure_code")
        if failure_code is None and status == "TIMED_OUT":
            failure_code = "launcher_deadline_exceeded"
        elif failure_code is None and state == "failed":
            failure_code = "launcher_execution_failed"
        return FargateExecution(
            execution_arn=execution_arn,
            name=name,
            state=state,
            started_at=_optional_string(payload.get("startDate")),
            stopped_at=_optional_string(payload.get("stopDate")),
            run_id=_deep_string(details, "run_id"),
            task_arn=_deep_string(details, "task_arn", "taskArn", "TaskArn"),
            container_exit_code=_deep_integer(details, "container_exit_code", "exit_code"),
            failure_code="operator_cancelled" if state == "cancelled" else failure_code,
        )

    def logs(self, execution_arn: str, *, limit: int = 100) -> tuple[FargateLogEvent, ...]:
        """Read task logs correlated through the controller execution history."""
        if not 1 <= limit <= 10_000:
            raise FargateOperationError("Log limit must be between 1 and 10000")
        execution = self.describe(execution_arn)
        task_arn = execution.task_arn or self._task_arn_from_history(execution_arn)
        if task_arn is None:
            raise FargateOperationError("The execution has not exposed an ECS task yet")
        task_id = task_arn.rsplit("/", maxsplit=1)[-1]
        if _TASK_ID.fullmatch(task_id) is None:
            raise FargateOperationError("AWS returned an invalid ECS task ARN")
        payload = self._json(
            "logs",
            "filter-log-events",
            "--log-group-name",
            self.binding.log_group_name,
            "--log-stream-name-prefix",
            f"runtime/dander/{task_id}",
            "--limit",
            str(limit),
        )
        events = payload.get("events")
        if not isinstance(events, list):
            raise FargateOperationError("AWS returned an invalid CloudWatch log response")
        result: list[FargateLogEvent] = []
        for event in events:
            if not isinstance(event, dict):
                raise FargateOperationError("AWS returned an invalid CloudWatch log event")
            timestamp = event.get("timestamp")
            message = event.get("message")
            if (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, int)
                or not isinstance(message, str)
            ):
                raise FargateOperationError("AWS returned an invalid CloudWatch log event")
            result.append(FargateLogEvent(timestamp=timestamp, message=message))
        return tuple(result)

    def cancel(self, execution_arn: str) -> FargateExecution:
        """Stop one running controller; the synchronous ECS integration stops its task."""
        execution = self.describe(execution_arn)
        if execution.state != "running":
            raise FargateOperationError("Only a running execution can be cancelled")
        payload = self._json(
            "stepfunctions",
            "stop-execution",
            "--execution-arn",
            execution_arn,
            "--error",
            "Dander.OperatorCancelled",
            "--cause",
            "Cancelled by the Dander operator",
        )
        return FargateExecution(
            execution_arn=execution_arn,
            name=execution.name,
            state="cancelled",
            started_at=execution.started_at,
            stopped_at=_optional_string(payload.get("stopDate")),
            run_id=execution.run_id,
            task_arn=execution.task_arn,
            failure_code="operator_cancelled",
        )

    def verify(self, *, expected_image: str) -> FargateDeploymentVerification:
        """Verify immutable image, controller, scheduler, logs, and cluster read-only."""
        image = _ECR_IMAGE.fullmatch(expected_image)
        if (
            image is None
            or image.group("account") != self.binding.account_id
            or image.group("region") != self.binding.region
        ):
            raise FargateOperationError(
                "Expected image is not an immutable ECR image in this deployment"
            )
        machine = self._json(
            "stepfunctions",
            "describe-state-machine",
            "--state-machine-arn",
            self.binding.state_machine_arn,
        )
        if machine.get("status") != "ACTIVE":
            raise FargateOperationError("Fargate controller is not active")
        clusters = self._json("ecs", "describe-clusters", "--clusters", self.binding.cluster_name)
        cluster_items = clusters.get("clusters")
        if (
            not isinstance(cluster_items, list)
            or len(cluster_items) != 1
            or not isinstance(cluster_items[0], dict)
            or cluster_items[0].get("status") != "ACTIVE"
            or clusters.get("failures") not in ([], None)
        ):
            raise FargateOperationError("Fargate cluster is unavailable")
        schedule = self._json("scheduler", "get-schedule", "--name", self.binding.resource_name)
        expected_schedule = "DISABLED" if self.binding.schedule_paused else "ENABLED"
        if schedule.get("State") != expected_schedule:
            raise FargateOperationError("Fargate schedule does not match the manifest")
        log_groups = self._json(
            "logs",
            "describe-log-groups",
            "--log-group-name-prefix",
            self.binding.log_group_name,
        ).get("logGroups")
        if not isinstance(log_groups, list) or not any(
            isinstance(group, dict) and group.get("logGroupName") == self.binding.log_group_name
            for group in log_groups
        ):
            raise FargateOperationError("Fargate log group is unavailable")
        task = self._json(
            "ecs",
            "describe-task-definition",
            "--task-definition",
            self.binding.resource_name,
        ).get("taskDefinition")
        if not isinstance(task, dict) or task.get("status") != "ACTIVE":
            raise FargateOperationError("Fargate task definition is unavailable")
        containers = task.get("containerDefinitions")
        if (
            not isinstance(containers, list)
            or len(containers) != 1
            or not isinstance(containers[0], dict)
        ):
            raise FargateOperationError("Fargate task definition has invalid containers")
        container = containers[0]
        if (
            container.get("image") != expected_image
            or container.get("readonlyRootFilesystem") is not True
            or container.get("user") != "65532:65532"
        ):
            raise FargateOperationError(
                "Fargate task does not match the immutable runtime contract"
            )
        repository = self._json(
            "ecr",
            "describe-repositories",
            "--repository-names",
            image.group("repository"),
        ).get("repositories")
        if (
            not isinstance(repository, list)
            or len(repository) != 1
            or not isinstance(repository[0], dict)
        ):
            raise FargateOperationError("ECR repository is unavailable")
        repository_item = repository[0]
        encryption = repository_item.get("encryptionConfiguration")
        scanning = repository_item.get("imageScanningConfiguration")
        if (
            repository_item.get("imageTagMutability") != "IMMUTABLE"
            or not isinstance(encryption, dict)
            or encryption.get("encryptionType") != "KMS"
            or not isinstance(scanning, dict)
            or scanning.get("scanOnPush") is not True
        ):
            raise FargateOperationError(
                "ECR repository does not meet the runtime artifact contract"
            )
        task_arn = task.get("taskDefinitionArn")
        cluster_arn = cluster_items[0].get("clusterArn")
        schedule_arn = schedule.get("Arn")
        repository_arn = repository_item.get("repositoryArn")
        if not all(
            isinstance(value, str)
            for value in (task_arn, cluster_arn, schedule_arn, repository_arn)
        ):
            raise FargateOperationError("AWS verification response is incomplete")
        return FargateDeploymentVerification(
            state_machine=self.binding.state_machine_arn,
            cluster=cast("str", cluster_arn),
            schedule=cast("str", schedule_arn),
            schedule_state=expected_schedule,
            task_definition=cast("str", task_arn),
            image=expected_image,
            log_group=self.binding.log_group_name,
            repository=cast("str", repository_arn),
        )

    def _start(self, *, name: str, now: datetime, correlation: str) -> FargateExecution:
        if _EXECUTION_NAME.fullmatch(name) is None:
            raise FargateOperationError(
                "Execution name must use 1-80 letters, numbers, hyphens, or underscores"
            )
        scheduled_time = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        request = {
            "deployment_revision": "manual",
            "scheduled_time": scheduled_time,
            "scheduler_attempt": 1,
            "scheduler_execution_id": correlation,
        }
        payload = self._json(
            "stepfunctions",
            "start-execution",
            "--state-machine-arn",
            self.binding.state_machine_arn,
            "--name",
            name,
            "--input",
            json.dumps(request, separators=(",", ":"), sort_keys=True),
        )
        execution_arn = payload.get("executionArn")
        if not isinstance(execution_arn, str):
            raise FargateOperationError("AWS did not return an execution ARN")
        self.binding.validate_execution_arn(execution_arn)
        return FargateExecution(
            execution_arn=execution_arn,
            name=name,
            state="running",
            started_at=_optional_string(payload.get("startDate")) or scheduled_time,
        )

    def _task_arn_from_history(self, execution_arn: str) -> str | None:
        self.binding.validate_execution_arn(execution_arn)
        return _deep_string(
            self._execution_history(execution_arn), "task_arn", "taskArn", "TaskArn"
        )

    def _execution_history(self, execution_arn: str) -> list[object]:
        """Return one owned execution's bounded history for allow-listed field extraction."""
        self.binding.validate_execution_arn(execution_arn)
        payload = self._json(
            "stepfunctions",
            "get-execution-history",
            "--execution-arn",
            execution_arn,
            "--reverse-order",
            "--max-results",
            "100",
        )
        events = payload.get("events")
        if not isinstance(events, list):
            raise FargateOperationError("AWS returned invalid execution history")
        return events

    def _json(self, service: str, operation: str, *arguments: str) -> dict[str, object]:
        command = (
            *self._aws_prefix,
            service,
            operation,
            "--region",
            self.binding.region,
            *arguments,
            "--output",
            "json",
        )
        try:
            completed = self._runner(
                command,
                cwd=self.binding.project_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            payload: object = json.loads(completed.stdout)
        except FileNotFoundError as error:
            raise FargateOperationError("The AWS CLI is not available") from error
        except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
            raise FargateOperationError(
                f"AWS {service} {operation} did not complete successfully"
            ) from error
        if not isinstance(payload, dict):
            raise FargateOperationError(f"AWS {service} returned an invalid response")
        return cast("dict[str, object]", payload)


def _resource_name(*, name: str, pipeline_id: str) -> str:
    normalized = pipeline_id.replace("_", "-")[:20]
    digest = hashlib.sha1(pipeline_id.encode(), usedforsecurity=False).hexdigest()[:8]
    return "-".join(part for part in (name[:20], normalized, digest) if part)


def _new_execution_name(prefix: str, now: datetime, nonce: str) -> str:
    if re.fullmatch(r"[0-9a-f]{8}", nonce) is None:
        raise FargateOperationError("Execution nonce must contain eight lowercase hex characters")
    timestamp = now.strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{timestamp}-{nonce}"


def _normalized_state(status: str) -> str:
    if status == "RUNNING":
        return "running"
    if status == "SUCCEEDED":
        return "succeeded"
    if status == "ABORTED":
        return "cancelled"
    if status in _TERMINAL_STATUSES:
        return "failed"
    raise FargateOperationError("AWS returned an unknown execution status")


def _json_document(value: object) -> object:
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _deep_string(value: object, *keys: str) -> str | None:
    found = _deep_value(value, frozenset(keys), expected=str)
    return cast("str | None", found)


def _deep_integer(value: object, *keys: str) -> int | None:
    found = _deep_value(value, frozenset(keys), expected=int)
    return cast("int | None", found)


def _deep_value(value: object, keys: frozenset[str], *, expected: type[object]) -> object:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and isinstance(child, expected) and not isinstance(child, bool):
                return child
            found = _deep_value(child, keys, expected=expected)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _deep_value(child, keys, expected=expected)
            if found is not None:
                return found
    elif isinstance(value, str) and value[:1] in {"{", "["}:
        nested = _json_document(value)
        if nested is not None:
            return _deep_value(nested, keys, expected=expected)
    return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "FargateBinding",
    "FargateDeploymentVerification",
    "FargateExecution",
    "FargateLogEvent",
    "FargateOperationError",
    "FargateOperations",
]
