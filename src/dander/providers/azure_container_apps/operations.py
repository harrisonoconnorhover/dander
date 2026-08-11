"""Provider-native Azure Container Apps Job lifecycle operations."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol, cast

from dander.identity.refresh_probe import GoogleRefreshProbeError, validate_probe_target

if TYPE_CHECKING:
    from pathlib import Path

    from dander.providers.azure_container_apps.verification import AzureDeploymentBinding

_EXECUTION_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_RUNNING_STATUSES = frozenset({"Pending", "Processing", "Running"})
_FAILED_STATUSES = frozenset({"Failed", "Degraded"})
_CANCELLED_STATUSES = frozenset({"Cancelled", "Canceled", "Stopped"})


class AzureContainerAppsOperationError(RuntimeError):
    """Raised when an Azure lifecycle operation fails or returns invalid data."""


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
class AzureContainerAppsExecution:
    """Small sanitized view of one Container Apps Job execution."""

    name: str
    state: str
    started_at: str | None = None
    stopped_at: str | None = None
    failure_code: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {"succeeded", "failed", "cancelled"}

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AzureContainerAppsLogEvent:
    """One bounded Log Analytics event from a selected job execution."""

    timestamp: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class AzureContainerAppsOperations:
    """Start, observe, stop, replay, and read logs for one manifest-bound Azure job."""

    def __init__(
        self,
        binding: AzureDeploymentBinding,
        *,
        runner: _Runner | None = None,
    ) -> None:
        self.binding = binding
        self._runner = runner or _subprocess_runner

    def start(self) -> AzureContainerAppsExecution:
        """Start one on-demand execution and return its provider-assigned identity."""
        return self._start()

    def start_identity_refresh_probe(
        self,
        *,
        project: str,
        dataset: str,
        table: str,
        max_wait_seconds: int = 900,
        refresh_margin_seconds: int = 15,
    ) -> AzureContainerAppsExecution:
        """Start one bounded Google refresh probe through the immutable job image."""
        try:
            validate_probe_target(project=project, dataset=dataset, table=table)
        except GoogleRefreshProbeError as error:
            raise AzureContainerAppsOperationError(str(error)) from error
        if not 1 <= max_wait_seconds <= 1_800 or not 0 <= refresh_margin_seconds <= 60:
            raise AzureContainerAppsOperationError("Credential refresh proof bounds are invalid")
        return self._start(
            "--args",
            "runtime",
            "identity-refresh-probe",
            "--project",
            project,
            "--dataset",
            dataset,
            "--table",
            table,
            "--max-wait-seconds",
            str(max_wait_seconds),
            "--refresh-margin-seconds",
            str(refresh_margin_seconds),
        )

    def _start(self, *overrides: str) -> AzureContainerAppsExecution:
        payload = self._json(
            "containerapp",
            "job",
            "start",
            "--name",
            self.binding.job_name,
            "--resource-group",
            self.binding.resource_group_name,
            *overrides,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            raise AzureContainerAppsOperationError(
                "Azure did not return an execution name after starting the job"
            )
        execution_name = cast("str", payload["name"])
        self._validate_execution_name(execution_name)
        return self.describe(execution_name)

    def replay(self, execution_name: str) -> AzureContainerAppsExecution:
        """Start a fresh run after one terminal execution using the persisted inclusive cursor."""
        previous = self.describe(execution_name)
        if not previous.terminal:
            raise AzureContainerAppsOperationError("Only a terminal execution can be replayed")
        return self.start()

    def latest(self) -> AzureContainerAppsExecution | None:
        """Return the most recently started execution, if one exists."""
        payload = self._json(
            "containerapp",
            "job",
            "execution",
            "list",
            "--name",
            self.binding.job_name,
            "--resource-group",
            self.binding.resource_group_name,
        )
        if not isinstance(payload, list):
            raise AzureContainerAppsOperationError("Azure returned an invalid execution list")
        if not payload:
            return None
        executions = [self._parse_execution(item) for item in payload]
        return max(executions, key=lambda item: item.started_at or "")

    def describe(self, execution_name: str) -> AzureContainerAppsExecution:
        """Return normalized status for one execution owned by the selected job."""
        self._validate_execution_name(execution_name)
        payload = self._json(
            "containerapp",
            "job",
            "execution",
            "show",
            "--name",
            self.binding.job_name,
            "--resource-group",
            self.binding.resource_group_name,
            "--job-execution-name",
            execution_name,
        )
        execution = self._parse_execution(payload)
        if execution.name != execution_name:
            raise AzureContainerAppsOperationError("Azure returned an unexpected job execution")
        return execution

    def logs(
        self,
        execution_name: str,
        *,
        limit: int = 100,
    ) -> tuple[AzureContainerAppsLogEvent, ...]:
        """Read bounded console logs correlated to one exact execution."""
        if not 1 <= limit <= 10_000:
            raise AzureContainerAppsOperationError("Log limit must be between 1 and 10000")
        self.describe(execution_name)
        environment = self._json(
            "containerapp",
            "env",
            "show",
            "--name",
            self.binding.environment_name,
            "--resource-group",
            self.binding.resource_group_name,
        )
        if not isinstance(environment, dict):
            raise AzureContainerAppsOperationError(
                "Azure returned an invalid Container Apps environment"
            )
        workspace = _nested_string(
            environment,
            "properties",
            "appLogsConfiguration",
            "logAnalyticsConfiguration",
            "customerId",
        ) or _nested_string(
            environment,
            "appLogsConfiguration",
            "logAnalyticsConfiguration",
            "customerId",
        )
        if workspace is None:
            raise AzureContainerAppsOperationError(
                "Container Apps environment has no Log Analytics destination"
            )
        query = (
            "ContainerAppConsoleLogs_CL "
            f"| where ContainerGroupName_s startswith '{execution_name}' "
            "| order by _timestamp_d asc "
            f"| take {limit} "
            "| project timestamp=tostring(_timestamp_d), message=Log_s"
        )
        payload = self._json(
            "monitor",
            "log-analytics",
            "query",
            "--workspace",
            workspace,
            "--analytics-query",
            query,
            "--timespan",
            "P30D",
        )
        if not isinstance(payload, list):
            raise AzureContainerAppsOperationError("Azure returned an invalid log response")
        events: list[AzureContainerAppsLogEvent] = []
        for item in payload:
            if not isinstance(item, dict):
                raise AzureContainerAppsOperationError("Azure returned an invalid log event")
            timestamp = item.get("timestamp")
            message = item.get("message")
            if not isinstance(timestamp, str) or not isinstance(message, str):
                raise AzureContainerAppsOperationError("Azure returned an invalid log event")
            events.append(AzureContainerAppsLogEvent(timestamp=timestamp, message=message))
        return tuple(events)

    def cancel(self, execution_name: str) -> AzureContainerAppsExecution:
        """Request a stop for one running Container Apps Job execution."""
        execution = self.describe(execution_name)
        if execution.state != "running":
            raise AzureContainerAppsOperationError("Only a running execution can be cancelled")
        self._execute(
            "containerapp",
            "job",
            "stop",
            "--name",
            self.binding.job_name,
            "--resource-group",
            self.binding.resource_group_name,
            "--job-execution-name",
            execution_name,
        )
        return AzureContainerAppsExecution(
            name=execution.name,
            state="cancellation_requested",
            started_at=execution.started_at,
        )

    def _execute(self, *args: str) -> None:
        command = (
            "az",
            *args,
            "--subscription",
            self.binding.subscription_id,
            "--only-show-errors",
        )
        self._run(command)

    def _parse_execution(self, payload: object) -> AzureContainerAppsExecution:
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            raise AzureContainerAppsOperationError("Azure returned an invalid job execution")
        name = cast("str", payload["name"])
        self._validate_execution_name(name)
        properties = payload.get("properties")
        if not isinstance(properties, dict) or not isinstance(properties.get("status"), str):
            raise AzureContainerAppsOperationError("Azure returned an invalid execution status")
        provider_status = cast("str", properties["status"])
        state = _normalized_state(provider_status)
        failure_code = "launcher_execution_failed" if state == "failed" else None
        if state == "cancelled":
            failure_code = "operator_cancelled"
        return AzureContainerAppsExecution(
            name=name,
            state=state,
            started_at=_optional_string(properties.get("startTime")),
            stopped_at=_optional_string(properties.get("endTime")),
            failure_code=failure_code,
        )

    def _validate_execution_name(self, execution_name: str) -> None:
        if _EXECUTION_NAME.fullmatch(execution_name) is None or not execution_name.startswith(
            self.binding.job_name + "-"
        ):
            raise AzureContainerAppsOperationError(
                "Execution does not belong to the selected Azure pipeline"
            )

    def _json(self, *args: str) -> object:
        command = (
            "az",
            *args,
            "--subscription",
            self.binding.subscription_id,
            "--output",
            "json",
            "--only-show-errors",
        )
        result = self._run(command)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AzureContainerAppsOperationError("Azure CLI returned invalid JSON") from error

    def _run(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                command,
                cwd=self.binding.project_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as error:
            raise AzureContainerAppsOperationError(
                "Azure CLI is not installed or is not available on PATH"
            ) from error
        except subprocess.CalledProcessError as error:
            raise AzureContainerAppsOperationError(
                f"Azure lifecycle operation failed with exit code {error.returncode}"
            ) from error


def _normalized_state(status: str) -> str:
    if status in _RUNNING_STATUSES:
        return "running"
    if status == "Succeeded":
        return "succeeded"
    if status in _FAILED_STATUSES:
        return "failed"
    if status in _CANCELLED_STATUSES:
        return "cancelled"
    raise AzureContainerAppsOperationError("Azure returned an unknown execution status")


def _nested_string(document: dict[str, object], *path: str) -> str | None:
    value: object = document
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _optional_string(value)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "AzureContainerAppsExecution",
    "AzureContainerAppsLogEvent",
    "AzureContainerAppsOperationError",
    "AzureContainerAppsOperations",
]
