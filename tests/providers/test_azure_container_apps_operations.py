"""Azure Container Apps execution, logs, stop, and replay contract tests."""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict, deque
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dander.providers.azure_container_apps import (
    AzureContainerAppsOperationError,
    AzureContainerAppsOperations,
    AzureDeploymentBinding,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_SUBSCRIPTION_ID = "11111111-1111-4111-8111-111111111111"
_CLIENT_ID = "22222222-2222-4222-8222-222222222222"
_ROOT = f"/subscriptions/{_SUBSCRIPTION_ID}/resourceGroups/dander-phase6"
_JOB = "dander-00626d3b5f01"
_EXECUTION = f"{_JOB}-abc1234"


def _binding() -> AzureDeploymentBinding:
    return AzureDeploymentBinding(
        subscription_id=_SUBSCRIPTION_ID,
        location="eastus",
        resource_group_name="dander-phase6",
        environment_name="dander-phase6-env",
        environment_id=f"{_ROOT}/providers/Microsoft.App/managedEnvironments/dander-phase6-env",
        acr_name="danderphase6",
        acr_login_server="danderphase6.azurecr.io",
        key_vault_name="dander-phase6-kv",
        key_vault_uri="https://dander-phase6-kv.vault.azure.net",
        managed_identity_id=(
            f"{_ROOT}/providers/Microsoft.ManagedIdentity/"
            "userAssignedIdentities/dander-phase6-runtime"
        ),
        managed_identity_client_id=_CLIENT_ID,
        pipeline_id="warehouse_fixture",
        job_name=_JOB,
        schedule_paused=True,
        runtime_timeout_seconds=900,
        runtime_max_retries=1,
        secret_provider="azure_key_vault",
        secret_bindings=(("DANDER_POSTGRES_DSN", "postgres-dsn"),),
        secret_ids=("postgres-dsn",),
        google_project=None,
        google_workload_identity_audience=None,
        google_application_id_uri=None,
        google_service_account=None,
        project_dir=Path("/tmp/dander-azure-operations-test"),
    )


class _Runner:
    def __init__(self, responses: Mapping[tuple[str, ...], list[object]]) -> None:
        self.responses: defaultdict[tuple[str, ...], deque[object]] = defaultdict(deque)
        for key, values in responses.items():
            self.responses[key].extend(values)
        self.commands: list[tuple[str, ...]] = []
        self.execution_templates: list[object] = []
        self.execution_template_paths: list[Path] = []

    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == Path("/tmp/dander-azure-operations-test")
        assert check and capture_output and text
        self.commands.append(args)
        key_parts = list(args[1 : args.index("--subscription")])
        if "--yaml" in key_parts:
            path_index = key_parts.index("--yaml") + 1
            template_path = Path(key_parts[path_index])
            self.execution_template_paths.append(template_path)
            self.execution_templates.append(json.loads(template_path.read_text(encoding="utf-8")))
            key_parts[path_index] = "<execution-template>"
        key = tuple(key_parts)
        if not self.responses[key]:
            raise AssertionError(f"No fake Azure response for {key}")
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(self.responses[key].popleft()),
        )


def _execution(*, status: str = "Succeeded", name: str = _EXECUTION) -> dict[str, object]:
    properties: dict[str, object] = {
        "status": status,
        "startTime": "2026-08-11T12:00:00+00:00",
    }
    if status not in {"Pending", "Processing", "Running"}:
        properties["endTime"] = "2026-08-11T12:01:00+00:00"
    return {"name": name, "properties": properties}


def test_start_uses_exact_manifest_job_and_returns_provider_execution() -> None:
    runner = _Runner(
        {
            (
                "containerapp",
                "job",
                "start",
                "--name",
                _JOB,
                "--resource-group",
                "dander-phase6",
            ): [{"name": _EXECUTION}],
            (
                "containerapp",
                "job",
                "execution",
                "show",
                "--name",
                _JOB,
                "--resource-group",
                "dander-phase6",
                "--job-execution-name",
                _EXECUTION,
            ): [_execution(status="Running")],
        }
    )

    execution = AzureContainerAppsOperations(_binding(), runner=runner).start()

    assert execution.name == _EXECUTION
    assert execution.state == "running"
    assert runner.commands[0][1:4] == ("containerapp", "job", "start")


def test_identity_refresh_probe_overrides_only_bounded_runtime_arguments() -> None:
    start_key = (
        "containerapp",
        "job",
        "start",
        "--name",
        _JOB,
        "--resource-group",
        "dander-phase6",
        "--yaml",
        "<execution-template>",
    )
    show_key = (
        "containerapp",
        "job",
        "execution",
        "show",
        "--name",
        _JOB,
        "--resource-group",
        "dander-phase6",
        "--job-execution-name",
        _EXECUTION,
    )
    runner = _Runner(
        {
            start_key: [{"name": _EXECUTION}],
            show_key: [_execution(status="Running")],
        }
    )

    execution = AzureContainerAppsOperations(
        _binding(), runner=runner
    ).start_identity_refresh_probe(
        project="unit-project",
        dataset="raw",
        table="proof_rows",
    )

    assert execution.state == "running"
    assert runner.execution_templates == [
        {
            "containers": [
                {
                    "name": "runtime",
                    "args": [
                        "runtime",
                        "identity-refresh-probe",
                        "--project",
                        "unit-project",
                        "--dataset",
                        "raw",
                        "--table",
                        "proof_rows",
                        "--max-wait-seconds",
                        "900",
                        "--refresh-margin-seconds",
                        "15",
                    ],
                }
            ]
        }
    ]
    assert all(not path.exists() for path in runner.execution_template_paths)
    assert not set(runner.commands[0]).intersection({"--image", "--env-vars", "--command"})


def test_latest_normalizes_the_most_recent_execution() -> None:
    older = _execution(name=f"{_JOB}-old1234")
    older_properties = older["properties"]
    assert isinstance(older_properties, dict)
    older_properties["startTime"] = "2026-08-11T11:00:00+00:00"
    runner = _Runner(
        {
            (
                "containerapp",
                "job",
                "execution",
                "list",
                "--name",
                _JOB,
                "--resource-group",
                "dander-phase6",
            ): [[older, _execution()]],
        }
    )

    execution = AzureContainerAppsOperations(_binding(), runner=runner).latest()

    assert execution is not None
    assert execution.name == _EXECUTION
    assert execution.state == "succeeded"


def test_logs_are_bounded_and_correlated_through_log_analytics() -> None:
    show_key = (
        "containerapp",
        "job",
        "execution",
        "show",
        "--name",
        _JOB,
        "--resource-group",
        "dander-phase6",
        "--job-execution-name",
        _EXECUTION,
    )
    runner = _Runner(
        {
            show_key: [_execution()],
            (
                "containerapp",
                "env",
                "show",
                "--name",
                "dander-phase6-env",
                "--resource-group",
                "dander-phase6",
            ): [
                {
                    "properties": {
                        "appLogsConfiguration": {
                            "logAnalyticsConfiguration": {"customerId": "workspace-unit"}
                        }
                    }
                }
            ],
            (
                "monitor",
                "log-analytics",
                "query",
                "--workspace",
                "workspace-unit",
                "--analytics-query",
                (
                    "ContainerAppConsoleLogs_CL "
                    f"| where ContainerGroupName_s startswith '{_EXECUTION}' "
                    "| order by _timestamp_d asc | take 7 "
                    "| project timestamp=tostring(_timestamp_d), message=Log_s"
                ),
                "--timespan",
                "P30D",
            ): [[{"timestamp": "2026-08-11T12:00:01Z", "message": "runtime complete"}]],
        }
    )

    events = AzureContainerAppsOperations(_binding(), runner=runner).logs(_EXECUTION, limit=7)

    assert events[0].message == "runtime complete"
    query_command = runner.commands[-1]
    assert _EXECUTION in query_command[query_command.index("--analytics-query") + 1]
    assert "take 7" in query_command[query_command.index("--analytics-query") + 1]


def test_cancel_requires_a_running_owned_execution() -> None:
    show_key = (
        "containerapp",
        "job",
        "execution",
        "show",
        "--name",
        _JOB,
        "--resource-group",
        "dander-phase6",
        "--job-execution-name",
        _EXECUTION,
    )
    stop_key = (
        "containerapp",
        "job",
        "stop",
        "--name",
        _JOB,
        "--resource-group",
        "dander-phase6",
        "--job-execution-name",
        _EXECUTION,
    )
    runner = _Runner({show_key: [_execution(status="Running")], stop_key: [{}]})

    execution = AzureContainerAppsOperations(_binding(), runner=runner).cancel(_EXECUTION)

    assert execution.state == "cancellation_requested"
    assert execution.failure_code is None
    assert execution.terminal is False


def test_replay_requires_terminal_status_then_starts_a_fresh_provider_execution() -> None:
    replayed = f"{_JOB}-replay1"
    show_previous = (
        "containerapp",
        "job",
        "execution",
        "show",
        "--name",
        _JOB,
        "--resource-group",
        "dander-phase6",
        "--job-execution-name",
        _EXECUTION,
    )
    show_replayed = (*show_previous[:-1], replayed)
    start = (
        "containerapp",
        "job",
        "start",
        "--name",
        _JOB,
        "--resource-group",
        "dander-phase6",
    )
    runner = _Runner(
        {
            show_previous: [_execution()],
            start: [{"name": replayed}],
            show_replayed: [_execution(status="Running", name=replayed)],
        }
    )

    execution = AzureContainerAppsOperations(_binding(), runner=runner).replay(_EXECUTION)

    assert execution.name == replayed
    assert execution.state == "running"


def test_foreign_execution_and_unknown_status_fail_closed() -> None:
    runner = _Runner({})
    operations = AzureContainerAppsOperations(_binding(), runner=runner)

    with pytest.raises(AzureContainerAppsOperationError, match="does not belong"):
        operations.describe("another-job-abc1234")

    assert runner.commands == []

    show_key = (
        "containerapp",
        "job",
        "execution",
        "show",
        "--name",
        _JOB,
        "--resource-group",
        "dander-phase6",
        "--job-execution-name",
        _EXECUTION,
    )
    with pytest.raises(AzureContainerAppsOperationError, match="unknown execution status"):
        AzureContainerAppsOperations(
            _binding(), runner=_Runner({show_key: [_execution(status="Mystery")]})
        ).describe(_EXECUTION)
