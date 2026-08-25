"""Hosted Control execution through the existing AWS Fargate controller."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from dander.control.fargate_execution_backend import FargateExecutionBackend
from dander.control.orchestration import (
    BackendExecutionState,
    BackendHandle,
    CleanupState,
    ExecutionBackendError,
    ExecutionPlan,
    ResultsState,
    RetryPolicy,
    RunOutcome,
    RunTrigger,
    TriggerKind,
)
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
    SecretReference,
)
from dander.providers.fargate import FargateBinding
from dander.runtime_contract import RUNTIME_CONTRACT

if TYPE_CHECKING:
    from pathlib import Path

ACCOUNT = "123456789012"
REGION = "us-east-1"
PROFILE = "aws"
PIPELINE = "hosted_graph"
RESOURCE = "dander-hosted-graph-12345678"
MACHINE = f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{RESOURCE}"
IMAGE = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/dander@sha256:" + "b" * 64
TASK_ID = "a" * 32
TASK_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/dander/{TASK_ID}"
NOW = datetime(2026, 8, 25, 17, tzinfo=UTC)


class _FakeAwsError(Exception):
    def __init__(self, code: str, message: str = "provider detail") -> None:
        super().__init__(message)
        self.response: dict[str, object] = {"Error": {"Code": code, "Message": message}}


class _StepFunctions:
    def __init__(self) -> None:
        self.executions: dict[str, dict[str, object]] = {}
        self.histories: dict[str, list[object]] = {}
        self.start_calls: list[dict[str, object]] = []
        self.describe_calls: list[dict[str, object]] = []
        self.stop_calls: list[dict[str, object]] = []
        self.describe_error: Exception | None = None
        self.history_error: Exception | None = None
        self.close_count = 0

    def start_execution(self, **kwargs: object) -> dict[str, object]:
        self.start_calls.append(dict(kwargs))
        machine = cast("str", kwargs["stateMachineArn"])
        name = cast("str", kwargs["name"])
        execution_arn = machine.replace(":stateMachine:", ":execution:") + f":{name}"
        if execution_arn in self.executions:
            raise _FakeAwsError("ExecutionAlreadyExists")
        self.executions[execution_arn] = {
            "executionArn": execution_arn,
            "status": "RUNNING",
            "input": kwargs["input"],
        }
        return {"executionArn": execution_arn, "startDate": NOW}

    def describe_execution(self, **kwargs: object) -> dict[str, object]:
        self.describe_calls.append(dict(kwargs))
        if self.describe_error is not None:
            raise self.describe_error
        execution_arn = cast("str", kwargs["executionArn"])
        execution = self.executions.get(execution_arn)
        if execution is None:
            raise _FakeAwsError("ExecutionDoesNotExist")
        return dict(execution)

    def get_execution_history(self, **kwargs: object) -> dict[str, object]:
        if self.history_error is not None:
            raise self.history_error
        execution_arn = cast("str", kwargs["executionArn"])
        assert kwargs == {
            "executionArn": execution_arn,
            "reverseOrder": True,
            "maxResults": 100,
            "includeExecutionData": True,
        }
        return {"events": self.histories.get(execution_arn, [])}

    def stop_execution(self, **kwargs: object) -> dict[str, object]:
        self.stop_calls.append(dict(kwargs))
        execution_arn = cast("str", kwargs["executionArn"])
        self.executions[execution_arn]["status"] = "ABORTED"
        return {"stopDate": NOW}

    def close(self) -> None:
        self.close_count += 1


class _Logs:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses: list[dict[str, object]] = []
        self.close_count = 0

    def filter_log_events(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if not self.responses:
            raise AssertionError("missing fake log response")
        return self.responses.pop(0)

    def close(self) -> None:
        self.close_count += 1


class _Ecs:
    def __init__(self) -> None:
        self.tasks: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []
        self.error: Exception | None = None
        self.close_count = 0

    def describe_tasks(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        task_arn = cast("list[str]", kwargs["tasks"])[0]
        status = self.tasks.get(task_arn)
        if status is None:
            return {"tasks": [], "failures": [{"arn": task_arn}]}
        return {
            "tasks": [{"taskArn": task_arn, "lastStatus": status}],
            "failures": [],
        }

    def close(self) -> None:
        self.close_count += 1


def _template() -> ExecutionTemplate:
    return ExecutionTemplate(
        schema=EXECUTION_PROJECTION_SCHEMA,
        contract=RUNTIME_CONTRACT,
        pipeline_id=PIPELINE,
        profile_id=PROFILE,
        launcher="fargate",
        image=IMAGE,
        command=(
            "runtime",
            "execute",
            "--contract",
            RUNTIME_CONTRACT,
            "--pipeline",
            PIPELINE,
            "--platform",
            PROFILE,
        ),
        configuration_reference="/app/dander.yaml",
        environment=(("DANDER_PLATFORMS_CONFIG_JSON", "{}"),),
        secret_bindings=(
            (
                "DANDER_STATE_DSN",
                SecretReference(
                    provider="aws_secret_manager",
                    reference=(
                        f"aws-sm://arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:dander/state"
                    ),
                ),
            ),
        ),
        workload_identity=f"arn:aws:iam::{ACCOUNT}:role/dander-runtime",
        resources=ResourceProjection(
            cpu_millis=1_000,
            memory_mib=2_048,
            ephemeral_storage_mib=20_480,
            deadline_seconds=300,
            runtime_retry_count=0,
            launcher_retry_count=1,
        ),
        schedule=ScheduleProjection(
            task_count=1,
            maximum_parallelism=1,
            expression=None,
            time_zone=None,
            paused=False,
        ),
        network=NetworkPlacement(
            placement="awsvpc",
            extensions=(
                ("fargate_security_group_ids", "sg-0123456789abcdef0"),
                ("fargate_subnet_ids", "subnet-0123456789abcdef0"),
            ),
        ),
        labels=(("pipeline", PIPELINE),),
        observability=ObservabilityProjection(
            log_destination="cloudwatch_logs",
            metric_namespace="Dander",
            alert_target=None,
            retention_days=30,
        ),
        extensions=(
            ("fargate_architecture", "ARM64"),
            ("fargate_assign_public_ip", "disabled"),
        ),
    )


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="aws-redshift",
        environment="production",
        project="demo",
        graph="hosted-graph",
        graph_revision="graph-r1",
        graph_content_sha256="c" * 64,
        backend_id="fargate",
        profile_id=PROFILE,
        image=IMAGE,
        execution_template=_template(),
        deadline_seconds=300,
        retry_policy=RetryPolicy(max_attempts=2),
    )


def _binding(tmp_path: Path) -> FargateBinding:
    return FargateBinding(
        account_id=ACCOUNT,
        region=REGION,
        deployment_name=PROFILE,
        pipeline_id=PIPELINE,
        resource_name=RESOURCE,
        state_machine_arn=MACHINE,
        cluster_name="dander",
        log_group_name=f"/dander/dander/{PIPELINE}",
        schedule_paused=True,
        project_dir=tmp_path,
    )


def _backend(
    tmp_path: Path,
    *,
    plan: ExecutionPlan | None = None,
    step_functions: _StepFunctions | None = None,
    logs: _Logs | None = None,
    ecs: _Ecs | None = None,
) -> tuple[FargateExecutionBackend, ExecutionPlan, _StepFunctions, _Logs, _Ecs]:
    selected_plan = plan or _plan()
    step_functions = step_functions or _StepFunctions()
    logs = logs or _Logs()
    ecs = ecs or _Ecs()
    return (
        FargateExecutionBackend(
            {selected_plan.revision: _binding(tmp_path)},
            step_functions_client=step_functions,
            logs_client=logs,
            ecs_client=ecs,
            clock=lambda: NOW,
        ),
        selected_plan,
        step_functions,
        logs,
        ecs,
    )


def _start(
    backend: FargateExecutionBackend,
    plan: ExecutionPlan,
    *,
    run_id: str = "run-hosted-001",
    attempt_id: str = "attempt-1-hosted",
) -> BackendHandle:
    return backend.submit_or_adopt(
        plan,
        run_id=run_id,
        attempt_id=attempt_id,
        trigger=RunTrigger(kind=TriggerKind.API, trigger_id="control-api"),
    )


def test_submit_uses_deterministic_identity_and_restart_adopts_one_execution(
    tmp_path: Path,
) -> None:
    backend, plan, step_functions, logs, ecs = _backend(tmp_path)

    first = _start(backend, plan)
    restarted, _, _, _, _ = _backend(
        tmp_path,
        plan=plan,
        step_functions=step_functions,
        logs=logs,
        ecs=ecs,
    )
    adopted = _start(restarted, plan)

    assert adopted == first
    assert len(step_functions.start_calls) == 1
    request = json.loads(cast("str", step_functions.start_calls[0]["input"]))
    assert request == {
        "deployment_revision": plan.revision,
        "scheduled_time": "2026-08-25T17:00:00Z",
        "scheduler_attempt": 1,
        "scheduler_execution_id": "control:run-hosted-001:attempt-1-hosted",
    }
    assert cast("str", step_functions.start_calls[0]["name"]).startswith("control-")


def test_submit_reconciles_a_lost_start_response_without_duplicate_effect(tmp_path: Path) -> None:
    backend, plan, step_functions, _logs, _ecs = _backend(tmp_path)
    original_start = step_functions.start_execution

    def lost_response(**kwargs: object) -> dict[str, object]:
        original_start(**kwargs)
        raise _FakeAwsError("ConnectionClosed", "secret transport detail")

    step_functions.start_execution = lost_response  # type: ignore[method-assign]

    handle = _start(backend, plan)

    assert handle.execution_id in step_functions.executions
    assert len(step_functions.start_calls) == 1


def test_submit_rejects_unregistered_plan_before_an_aws_call(tmp_path: Path) -> None:
    backend, plan, step_functions, _logs, _ecs = _backend(tmp_path)
    changed = replace(plan, plan_id="aws-redshift-v2")

    with pytest.raises(ExecutionBackendError, match="not registered"):
        _start(backend, changed)

    assert step_functions.describe_calls == []
    assert step_functions.start_calls == []


def test_observe_normalizes_success_results_and_confirmed_task_cleanup(tmp_path: Path) -> None:
    backend, plan, step_functions, _logs, ecs = _backend(tmp_path)
    handle = _start(backend, plan)
    step_functions.executions[handle.execution_id].update(
        {
            "status": "SUCCEEDED",
            "output": json.dumps(
                {
                    "status": "succeeded",
                    "task_arn": TASK_ARN,
                    "container_exit_code": 0,
                    "provider_noise": "not returned",
                }
            ),
        }
    )
    ecs.tasks[TASK_ARN] = "STOPPED"

    observed = backend.observe(plan, handle)

    assert observed.execution_state is BackendExecutionState.TERMINAL
    assert observed.outcome is RunOutcome.SUCCEEDED
    assert observed.results_state is ResultsState.AVAILABLE
    assert observed.cleanup_state is CleanupState.CONFIRMED
    assert observed.failure_code is None
    assert ecs.calls == [{"cluster": "dander", "tasks": [TASK_ARN]}]


def test_observe_recovers_allowlisted_failure_and_keeps_cleanup_independent(
    tmp_path: Path,
) -> None:
    backend, plan, step_functions, _logs, ecs = _backend(tmp_path)
    handle = _start(backend, plan)
    step_functions.executions[handle.execution_id]["status"] = "FAILED"
    step_functions.histories[handle.execution_id] = [
        {
            "stateExitedEventDetails": {
                "output": json.dumps(
                    {
                        "failure_code": "runtime_retry_exhausted",
                        "task_arn": TASK_ARN,
                        "secret_provider_cause": "not returned",
                    }
                )
            }
        }
    ]
    ecs.tasks[TASK_ARN] = "RUNNING"

    observed = backend.observe(plan, handle)

    assert observed.outcome is RunOutcome.FAILED
    assert observed.results_state is ResultsState.UNAVAILABLE
    assert observed.cleanup_state is CleanupState.PENDING
    assert observed.failure_code == "runtime_retry_exhausted"


def test_observe_reports_uncertain_cleanup_when_task_verification_fails(tmp_path: Path) -> None:
    backend, plan, step_functions, _logs, ecs = _backend(tmp_path)
    handle = _start(backend, plan)
    step_functions.executions[handle.execution_id].update(
        {"status": "SUCCEEDED", "output": json.dumps({"task_arn": TASK_ARN})}
    )
    ecs.error = _FakeAwsError("AccessDeniedException", "secret cleanup detail")

    observed = backend.observe(plan, handle)

    assert observed.outcome is RunOutcome.SUCCEEDED
    assert observed.results_state is ResultsState.AVAILABLE
    assert observed.cleanup_state is CleanupState.UNCERTAIN


def test_observe_keeps_terminal_truth_when_failure_history_is_unavailable(tmp_path: Path) -> None:
    backend, plan, step_functions, _logs, _ecs = _backend(tmp_path)
    handle = _start(backend, plan)
    step_functions.executions[handle.execution_id]["status"] = "FAILED"
    step_functions.history_error = _FakeAwsError(
        "AccessDeniedException",
        "secret history detail",
    )

    observed = backend.observe(plan, handle)

    assert observed.outcome is RunOutcome.FAILED
    assert observed.results_state is ResultsState.UNAVAILABLE
    assert observed.cleanup_state is CleanupState.UNCERTAIN
    assert observed.failure_code == "launcher_execution_failed"


def test_logs_use_owned_task_and_return_a_bounded_paginated_page(tmp_path: Path) -> None:
    backend, plan, step_functions, logs, _ecs = _backend(tmp_path)
    handle = _start(backend, plan)
    step_functions.histories[handle.execution_id] = [
        {"taskSubmittedEventDetails": {"output": json.dumps({"TaskArn": TASK_ARN})}}
    ]
    logs.responses.append(
        {
            "events": [
                {"timestamp": 1_777_134_600_000, "message": "runtime started"},
                {"timestamp": 1_777_134_601_000, "message": ""},
            ],
            "nextToken": "page-2",
        }
    )

    page = backend.logs(plan, handle, cursor=None, limit=2)

    assert [record.message for record in page.records] == [
        "runtime started",
        "(empty log event)",
    ]
    assert page.records[0].occurred_at.tzinfo is UTC
    assert page.next_cursor == "page-2"
    assert logs.calls == [
        {
            "logGroupName": f"/dander/dander/{PIPELINE}",
            "logStreamNamePrefix": f"runtime/dander/{TASK_ID}",
            "limit": 2,
        }
    ]


def test_logs_are_empty_before_the_controller_exposes_a_task(tmp_path: Path) -> None:
    backend, plan, _step_functions, logs, _ecs = _backend(tmp_path)
    handle = _start(backend, plan)

    page = backend.logs(plan, handle, cursor=None, limit=100)

    assert page.records == ()
    assert logs.calls == []


def test_cancel_is_idempotent_after_the_owned_execution_is_terminal(tmp_path: Path) -> None:
    backend, plan, step_functions, _logs, _ecs = _backend(tmp_path)
    handle = _start(backend, plan)

    backend.cancel(plan, handle)
    backend.cancel(plan, handle)

    assert step_functions.stop_calls == [
        {
            "executionArn": handle.execution_id,
            "error": "Dander.OperatorCancelled",
            "cause": "Cancelled by Dander Control",
        }
    ]


def test_provider_errors_are_sanitized_and_do_not_start_when_lookup_is_uncertain(
    tmp_path: Path,
) -> None:
    backend, plan, step_functions, _logs, _ecs = _backend(tmp_path)
    step_functions.describe_error = _FakeAwsError(
        "AccessDeniedException",
        "secret provider detail",
    )

    with pytest.raises(ExecutionBackendError) as captured:
        _start(backend, plan)

    assert "secret provider detail" not in str(captured.value)
    assert step_functions.start_calls == []


def test_close_releases_injected_sdk_clients_once(tmp_path: Path) -> None:
    backend, _plan, step_functions, logs, ecs = _backend(tmp_path)

    backend.close()
    backend.close()

    assert (step_functions.close_count, logs.close_count, ecs.close_count) == (1, 1, 1)
