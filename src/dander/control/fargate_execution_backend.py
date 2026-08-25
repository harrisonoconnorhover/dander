"""Hosted Control adapter for the existing AWS Fargate execution controller.

The direct operator CLI remains independent. This adapter uses lazy AWS SDK clients with ambient
identity, binds only explicitly registered canonical plans, and addresses each Step Functions
execution by a deterministic logical run/attempt identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

from dander.control.orchestration import (
    BackendExecutionState,
    BackendHandle,
    BackendLogPage,
    BackendLogRecord,
    BackendObservation,
    CleanupState,
    ExecutionBackendError,
    ExecutionPlan,
    ResultsState,
    RunOutcome,
    RunTrigger,
)
from dander.providers.fargate.operations import FargateBinding, FargateOperationError

if TYPE_CHECKING:
    from collections.abc import Callable

_BACKEND_ID = "fargate"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AWS_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")
_AWS_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+$")
_TASK_ID = re.compile(r"^[0-9a-f]{32}$")
_ECR_IMAGE = re.compile(
    r"^(?P<account>[0-9]{12})\.dkr\.ecr\.(?P<region>[a-z]{2}(?:-gov)?-[a-z]+-[0-9]+)"
    r"\.amazonaws\.com/[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$"
)
_MAX_LOG_MESSAGE_LENGTH = 16_384
_MAX_CURSOR_LENGTH = 1_024
_DEFAULT_TIMEOUT_SECONDS = 30.0


class _StepFunctionsClient(Protocol):
    def start_execution(self, **kwargs: object) -> Mapping[str, object]: ...

    def describe_execution(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_execution_history(self, **kwargs: object) -> Mapping[str, object]: ...

    def stop_execution(self, **kwargs: object) -> Mapping[str, object]: ...


class _LogsClient(Protocol):
    def filter_log_events(self, **kwargs: object) -> Mapping[str, object]: ...


class _EcsClient(Protocol):
    def describe_tasks(self, **kwargs: object) -> Mapping[str, object]: ...


class _AwsCallError(RuntimeError):
    def __init__(self, operation: str, code: str | None) -> None:
        super().__init__(f"AWS {operation} failed")
        self.code = code


class FargateExecutionBackend:
    """Execute explicitly registered Control plans through existing Fargate state machines."""

    def __init__(
        self,
        plan_bindings: Mapping[str, FargateBinding],
        *,
        step_functions_client: _StepFunctionsClient | None = None,
        logs_client: _LogsClient | None = None,
        ecs_client: _EcsClient | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        bindings = dict(plan_bindings)
        if not bindings or any(_SHA256.fullmatch(revision) is None for revision in bindings):
            raise ExecutionBackendError("Fargate plan bindings are missing or invalid.")
        coordinates = {(binding.account_id, binding.region) for binding in bindings.values()}
        if len(coordinates) != 1:
            raise ExecutionBackendError(
                "Fargate plan bindings must share one AWS account and region."
            )
        account_id, region = next(iter(coordinates))
        if _AWS_ACCOUNT_ID.fullmatch(account_id) is None or _AWS_REGION.fullmatch(region) is None:
            raise ExecutionBackendError("Fargate plan bindings have invalid AWS coordinates.")
        for binding in bindings.values():
            _validate_binding(binding)
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ExecutionBackendError("Fargate backend timeout is invalid.")

        if step_functions_client is None or logs_client is None or ecs_client is None:
            import boto3  # type: ignore
            from botocore.config import Config  # type: ignore

            config = Config(
                connect_timeout=float(timeout_seconds),
                read_timeout=float(timeout_seconds),
                retries={"max_attempts": 3, "mode": "standard"},
            )
            if step_functions_client is None:
                step_functions_client = cast(
                    "_StepFunctionsClient",
                    boto3.client("stepfunctions", region_name=region, config=config),
                )
            if logs_client is None:
                logs_client = cast(
                    "_LogsClient",
                    boto3.client("logs", region_name=region, config=config),
                )
            if ecs_client is None:
                ecs_client = cast(
                    "_EcsClient",
                    boto3.client("ecs", region_name=region, config=config),
                )

        self._plan_bindings = bindings
        self._step_functions = step_functions_client
        self._logs = logs_client
        self._ecs = ecs_client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._closed = False

    def submit_or_adopt(
        self,
        plan: ExecutionPlan,
        *,
        run_id: str,
        attempt_id: str,
        trigger: RunTrigger,
    ) -> BackendHandle:
        """Start or adopt the one deterministic Standard Workflow execution for an attempt."""
        binding = self._binding_for(plan)
        name = _execution_name(run_id, attempt_id)
        execution_arn = _execution_arn(binding, name)
        correlation = f"control:{run_id}:{attempt_id}"
        existing = self._try_describe_execution(
            execution_arn,
            plan_revision=plan.revision,
            correlation=correlation,
        )
        if existing is not None:
            return BackendHandle(backend_id=_BACKEND_ID, execution_id=execution_arn)

        occurred_at = trigger.scheduled_occurrence or self._now()
        request = {
            "deployment_revision": plan.revision,
            "scheduled_time": _timestamp(occurred_at),
            "scheduler_attempt": 1,
            "scheduler_execution_id": correlation,
        }
        try:
            response = self._call(
                "start execution",
                self._step_functions.start_execution,
                stateMachineArn=binding.state_machine_arn,
                name=name,
                input=json.dumps(request, separators=(",", ":"), sort_keys=True),
            )
        except _AwsCallError as start_error:
            adopted = self._try_describe_execution(
                execution_arn,
                plan_revision=plan.revision,
                correlation=correlation,
            )
            if adopted is not None:
                return BackendHandle(backend_id=_BACKEND_ID, execution_id=execution_arn)
            raise ExecutionBackendError(
                "Fargate execution could not be created or adopted."
            ) from start_error
        if response.get("executionArn") != execution_arn:
            raise ExecutionBackendError("Fargate returned an unexpected execution identity.")
        return BackendHandle(backend_id=_BACKEND_ID, execution_id=execution_arn)

    def observe(self, plan: ExecutionPlan, handle: BackendHandle) -> BackendObservation:
        """Return outcome, result availability, and independently verified task cleanup."""
        binding = self._binding_and_handle(plan, handle)
        response = self._describe_execution(handle.execution_id, plan_revision=plan.revision)
        status = response.get("status")
        if status == "RUNNING":
            return BackendObservation(
                execution_state=BackendExecutionState.RUNNING,
                outcome=RunOutcome.UNKNOWN,
                results_state=ResultsState.PENDING,
                cleanup_state=CleanupState.PENDING,
                observed_at=self._now(),
                stage="running",
            )
        if status not in {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED", "PENDING_REDRIVE"}:
            raise ExecutionBackendError("Fargate returned an unknown execution status.")

        output = _json_document(response.get("output"))
        history: list[object] = []
        if status != "SUCCEEDED":
            try:
                history = self._execution_history(handle.execution_id)
            except ExecutionBackendError:
                history = []
        details: object = [output, history]
        task_arn = _deep_string(details, "task_arn", "taskArn", "TaskArn")
        failure_code = _normalized_failure_code(status, details)
        outcome = {
            "SUCCEEDED": RunOutcome.SUCCEEDED,
            "ABORTED": RunOutcome.CANCELED,
        }.get(status, RunOutcome.FAILED)
        results_state = (
            ResultsState.AVAILABLE if outcome is RunOutcome.SUCCEEDED else ResultsState.UNAVAILABLE
        )
        return BackendObservation(
            execution_state=BackendExecutionState.TERMINAL,
            outcome=outcome,
            results_state=results_state,
            cleanup_state=self._cleanup_state(binding, task_arn),
            observed_at=self._now(),
            stage=_normalized_stage(status),
            failure_code=failure_code,
        )

    def logs(
        self,
        plan: ExecutionPlan,
        handle: BackendHandle,
        *,
        cursor: str | None,
        limit: int,
    ) -> BackendLogPage:
        """Return one bounded CloudWatch page for the latest task in the execution."""
        binding = self._binding_and_handle(plan, handle)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ExecutionBackendError("Fargate log page size is invalid.")
        if cursor is not None and (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > _MAX_CURSOR_LENGTH
            or "\n" in cursor
        ):
            raise ExecutionBackendError("Fargate log cursor is invalid.")

        execution = self._describe_execution(handle.execution_id, plan_revision=plan.revision)
        output = _json_document(execution.get("output"))
        task_arn = _deep_string(output, "task_arn", "taskArn", "TaskArn")
        if task_arn is None:
            task_arn = _deep_string(
                self._execution_history(handle.execution_id),
                "task_arn",
                "taskArn",
                "TaskArn",
            )
        if task_arn is None:
            return BackendLogPage(records=())
        task_id = _validated_task_id(binding, task_arn)
        arguments: dict[str, object] = {
            "logGroupName": binding.log_group_name,
            "logStreamNamePrefix": f"runtime/dander/{task_id}",
            "limit": limit,
        }
        if cursor is not None:
            arguments["nextToken"] = cursor
        response = self._call("read logs", self._logs.filter_log_events, **arguments)
        raw_events = response.get("events")
        if not isinstance(raw_events, list) or len(raw_events) > limit:
            raise ExecutionBackendError("Fargate returned an invalid log page.")
        records: list[BackendLogRecord] = []
        for event in raw_events:
            if not isinstance(event, Mapping):
                raise ExecutionBackendError("Fargate returned an invalid log event.")
            raw_timestamp = event.get("timestamp")
            raw_message = event.get("message")
            if (
                isinstance(raw_timestamp, bool)
                or not isinstance(raw_timestamp, int)
                or not isinstance(raw_message, str)
            ):
                raise ExecutionBackendError("Fargate returned an invalid log event.")
            message = raw_message or "(empty log event)"
            if len(message) > _MAX_LOG_MESSAGE_LENGTH:
                message = message[: _MAX_LOG_MESSAGE_LENGTH - 3] + "..."
            records.append(
                BackendLogRecord(
                    occurred_at=datetime.fromtimestamp(raw_timestamp / 1_000, tz=UTC),
                    message=message,
                )
            )
        next_cursor = response.get("nextToken")
        if next_cursor is not None and (
            not isinstance(next_cursor, str)
            or not next_cursor
            or len(next_cursor) > _MAX_CURSOR_LENGTH
            or "\n" in next_cursor
        ):
            raise ExecutionBackendError("Fargate returned an invalid log cursor.")
        if next_cursor == cursor:
            next_cursor = None
        return BackendLogPage(records=tuple(records), next_cursor=next_cursor)

    def cancel(self, plan: ExecutionPlan, handle: BackendHandle) -> None:
        """Idempotently request cancellation of one owned running execution."""
        self._binding_and_handle(plan, handle)
        execution = self._describe_execution(handle.execution_id, plan_revision=plan.revision)
        if execution.get("status") != "RUNNING":
            return
        try:
            self._call(
                "cancel execution",
                self._step_functions.stop_execution,
                executionArn=handle.execution_id,
                error="Dander.OperatorCancelled",
                cause="Cancelled by Dander Control",
            )
        except _AwsCallError as stop_error:
            reconciled = self._describe_execution(
                handle.execution_id,
                plan_revision=plan.revision,
            )
            if reconciled.get("status") != "RUNNING":
                return
            raise ExecutionBackendError(
                "Fargate cancellation could not be reconciled."
            ) from stop_error

    def close(self) -> None:
        """Close any SDK transports exactly once."""
        if self._closed:
            return
        try:
            seen: set[int] = set()
            for client in (self._step_functions, self._logs, self._ecs):
                if id(client) in seen:
                    continue
                seen.add(id(client))
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception as error:
            raise ExecutionBackendError("Fargate backend shutdown failed.") from error
        self._closed = True

    def _binding_for(self, plan: ExecutionPlan) -> FargateBinding:
        if plan.backend_id != _BACKEND_ID:
            raise ExecutionBackendError("The execution plan does not select Fargate.")
        binding = self._plan_bindings.get(plan.revision)
        if binding is None:
            raise ExecutionBackendError("The execution plan is not registered with Fargate.")
        template = plan.execution_template
        if (
            template.pipeline_id != binding.pipeline_id
            or plan.profile_id != binding.deployment_name
            or template.schedule.task_count != 1
            or template.schedule.maximum_parallelism != 1
        ):
            raise ExecutionBackendError("The execution plan does not match its Fargate binding.")
        image = _ECR_IMAGE.fullmatch(plan.image)
        if (
            image is None
            or image.group("account") != binding.account_id
            or image.group("region") != binding.region
        ):
            raise ExecutionBackendError("The execution plan image does not match Fargate.")
        return binding

    def _binding_and_handle(
        self,
        plan: ExecutionPlan,
        handle: BackendHandle,
    ) -> FargateBinding:
        binding = self._binding_for(plan)
        if handle.backend_id != _BACKEND_ID:
            raise ExecutionBackendError("The execution handle does not belong to Fargate.")
        try:
            binding.validate_execution_arn(handle.execution_id)
        except FargateOperationError as error:
            raise ExecutionBackendError(
                "The execution handle is outside its Fargate binding."
            ) from error
        return binding

    def _try_describe_execution(
        self,
        execution_arn: str,
        *,
        plan_revision: str,
        correlation: str,
    ) -> Mapping[str, object] | None:
        try:
            response = self._call(
                "describe execution",
                self._step_functions.describe_execution,
                executionArn=execution_arn,
            )
        except _AwsCallError as error:
            if error.code == "ExecutionDoesNotExist":
                return None
            raise ExecutionBackendError("Fargate execution lookup failed.") from error
        self._validate_described_execution(
            response,
            execution_arn=execution_arn,
            plan_revision=plan_revision,
            correlation=correlation,
        )
        return response

    def _describe_execution(
        self,
        execution_arn: str,
        *,
        plan_revision: str,
    ) -> Mapping[str, object]:
        try:
            response = self._call(
                "describe execution",
                self._step_functions.describe_execution,
                executionArn=execution_arn,
            )
        except _AwsCallError as error:
            raise ExecutionBackendError("Fargate execution observation failed.") from error
        self._validate_described_execution(
            response,
            execution_arn=execution_arn,
            plan_revision=plan_revision,
            correlation=None,
        )
        return response

    @staticmethod
    def _validate_described_execution(
        response: Mapping[str, object],
        *,
        execution_arn: str,
        plan_revision: str,
        correlation: str | None,
    ) -> None:
        if response.get("executionArn") != execution_arn or not isinstance(
            response.get("status"), str
        ):
            raise ExecutionBackendError("Fargate returned an unexpected execution.")
        request = _json_document(response.get("input"))
        if not isinstance(request, Mapping) or request.get("deployment_revision") != plan_revision:
            raise ExecutionBackendError("Fargate execution input does not match its plan.")
        if correlation is not None and request.get("scheduler_execution_id") != correlation:
            raise ExecutionBackendError("Fargate execution input does not match its attempt.")

    def _execution_history(self, execution_arn: str) -> list[object]:
        try:
            response = self._call(
                "read execution history",
                self._step_functions.get_execution_history,
                executionArn=execution_arn,
                reverseOrder=True,
                maxResults=100,
                includeExecutionData=True,
            )
        except _AwsCallError as error:
            raise ExecutionBackendError("Fargate execution history is unavailable.") from error
        events = response.get("events")
        if not isinstance(events, list) or len(events) > 100:
            raise ExecutionBackendError("Fargate returned invalid execution history.")
        return cast("list[object]", events)

    def _cleanup_state(self, binding: FargateBinding, task_arn: str | None) -> CleanupState:
        if task_arn is None:
            return CleanupState.UNCERTAIN
        try:
            _validated_task_id(binding, task_arn)
            response = self._call(
                "describe task cleanup",
                self._ecs.describe_tasks,
                cluster=binding.cluster_name,
                tasks=[task_arn],
            )
        except (ExecutionBackendError, _AwsCallError):
            return CleanupState.UNCERTAIN
        tasks = response.get("tasks")
        failures = response.get("failures")
        if failures not in (None, []) or not isinstance(tasks, list) or len(tasks) != 1:
            return CleanupState.UNCERTAIN
        task = tasks[0]
        if not isinstance(task, Mapping) or task.get("taskArn") != task_arn:
            return CleanupState.UNCERTAIN
        return (
            CleanupState.CONFIRMED if task.get("lastStatus") == "STOPPED" else CleanupState.PENDING
        )

    @staticmethod
    def _call(
        operation: str,
        method: Callable[..., Mapping[str, object]],
        **kwargs: object,
    ) -> Mapping[str, object]:
        try:
            response = method(**kwargs)
        except Exception as error:
            raise _AwsCallError(operation, _aws_error_code(error)) from error
        if not isinstance(response, Mapping):
            raise ExecutionBackendError("Fargate returned an invalid provider response.")
        return response

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ExecutionBackendError("Fargate backend clock must return an aware datetime.")
        return now.astimezone(UTC)


def _validate_binding(binding: FargateBinding) -> None:
    partition = "aws-us-gov" if binding.region.startswith("us-gov-") else "aws"
    prefix = f"arn:{partition}:states:{binding.region}:{binding.account_id}:stateMachine:"
    if (
        not binding.state_machine_arn.startswith(prefix)
        or not binding.log_group_name.startswith("/dander/")
        or "\n" in binding.log_group_name
    ):
        raise ExecutionBackendError("A Fargate plan binding is invalid.")


def _execution_name(run_id: str, attempt_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{attempt_id}".encode()).hexdigest()[:48]
    return f"control-{digest}"


def _execution_arn(binding: FargateBinding, name: str) -> str:
    return binding.state_machine_arn.replace(":stateMachine:", ":execution:") + f":{name}"


def _validated_task_id(binding: FargateBinding, task_arn: str) -> str:
    partition = "aws-us-gov" if binding.region.startswith("us-gov-") else "aws"
    prefix = f"arn:{partition}:ecs:{binding.region}:{binding.account_id}:task/"
    if not task_arn.startswith(prefix):
        raise ExecutionBackendError("Fargate returned a task outside its binding.")
    resource = task_arn.removeprefix(prefix)
    parts = resource.split("/")
    if len(parts) == 2 and parts[0] != binding.cluster_name:
        raise ExecutionBackendError("Fargate returned a task outside its cluster.")
    if len(parts) not in {1, 2} or _TASK_ID.fullmatch(parts[-1]) is None:
        raise ExecutionBackendError("Fargate returned an invalid task identity.")
    return parts[-1]


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionBackendError("Fargate trigger time must be aware.")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalized_stage(status: str) -> str:
    return {
        "SUCCEEDED": "succeeded",
        "FAILED": "failed",
        "TIMED_OUT": "timed_out",
        "ABORTED": "canceled",
        "PENDING_REDRIVE": "pending_redrive",
    }[status]


def _normalized_failure_code(status: str, details: object) -> str | None:
    if status == "SUCCEEDED":
        return None
    if status == "ABORTED":
        return "operator_cancelled"
    failure_code = _deep_string(details, "failure_code")
    if failure_code is not None and re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", failure_code):
        return failure_code
    if status == "TIMED_OUT":
        return "launcher_deadline_exceeded"
    return "launcher_execution_failed"


def _json_document(value: object) -> object:
    if not isinstance(value, str) or len(value) > 262_144:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _deep_string(value: object, *keys: str) -> str | None:
    wanted = frozenset(keys)
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in wanted and isinstance(child, str):
                return child
            found = _deep_string(child, *keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _deep_string(child, *keys)
            if found is not None:
                return found
    elif isinstance(value, str) and value[:1] in {"{", "["}:
        nested = _json_document(value)
        if nested is not None:
            return _deep_string(nested, *keys)
    return None


def _aws_error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    details = response.get("Error")
    if not isinstance(details, Mapping):
        return None
    code = details.get("Code")
    return code if isinstance(code, str) else None


__all__ = ["FargateExecutionBackend"]
