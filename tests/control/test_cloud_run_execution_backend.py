"""Hosted Control execution through existing GCP Cloud Run Jobs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from dander.control.cloud_run_execution_backend import CloudRunExecutionBackend
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
)
from dander.providers.cloud_run import CloudRunBinding
from dander.runtime_contract import RUNTIME_CONTRACT

PROJECT = "dander-unit-project"
REGION = "us-central1"
JOB = "dander-hosted-graph"
PIPELINE = "hosted_graph"
IMAGE = f"{REGION}-docker.pkg.dev/{PROJECT}/dander/runtime@sha256:" + "b" * 64
NOW = datetime(2026, 8, 26, 18, tzinfo=UTC)


@dataclass
class _Response:
    status_code: int
    payload: object

    def json(self) -> object:
        return self.payload


@dataclass
class _Transport:
    job: dict[str, object]
    executions: dict[str, dict[str, object]] = field(default_factory=dict)
    calls: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)
    log_response: dict[str, object] = field(default_factory=dict)
    lose_patch_response: bool = False
    fail_job_read: bool = False
    fail_log_read: bool = False
    close_count: int = 0

    def request(self, method: str, url: str, **kwargs: object) -> _Response:
        self.calls.append((method, url, dict(kwargs)))
        if url.endswith("/entries:list"):
            if self.fail_log_read:
                raise OSError("provider transport secret")
            return _Response(200, self.log_response)
        if url.endswith(":cancel"):
            resource = url.removeprefix("https://run.googleapis.com/v2/").removesuffix(":cancel")
            self.executions[resource].update(
                {"completionTime": "2026-08-26T18:01:00Z", "cancelledCount": 1}
            )
            return _Response(200, {"name": "operations/cancel"})
        resource = url.removeprefix("https://run.googleapis.com/v2/")
        if method == "GET" and "/executions/" in resource:
            execution = self.executions.get(resource)
            return _Response(200, execution) if execution is not None else _Response(404, {})
        if method == "GET" and "/jobs/" in resource:
            if self.fail_job_read:
                raise OSError("provider transport secret")
            return _Response(200, dict(self.job))
        if method == "PATCH" and "/jobs/" in resource:
            payload = kwargs["json"]
            assert isinstance(payload, dict)
            token = payload["startExecutionToken"]
            assert isinstance(token, str)
            self.job["startExecutionToken"] = token
            execution_resource = (
                f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB}/executions/{JOB}-{token}"
            )
            self.executions[execution_resource] = {
                "name": execution_resource,
                "job": JOB,
                "taskCount": 1,
                "startTime": "2026-08-26T18:00:01Z",
            }
            if self.lose_patch_response:
                raise OSError("provider transport secret")
            return _Response(200, {"name": "operations/start"})
        raise AssertionError((method, url, kwargs))

    def close(self) -> None:
        self.close_count += 1


def _template() -> ExecutionTemplate:
    return ExecutionTemplate(
        schema=EXECUTION_PROJECTION_SCHEMA,
        contract=RUNTIME_CONTRACT,
        pipeline_id=PIPELINE,
        profile_id="gcp",
        launcher="cloud_run",
        image=IMAGE,
        command=(
            "runtime",
            "execute",
            "--contract",
            RUNTIME_CONTRACT,
            "--pipeline",
            PIPELINE,
            "--platform",
            "gcp",
        ),
        configuration_reference="/app/dander.yaml",
        environment=(("GCP_PROJECT_ID", PROJECT),),
        secret_bindings=(),
        workload_identity=f"dander-runtime@{PROJECT}.iam.gserviceaccount.com",
        resources=ResourceProjection(
            cpu_millis=1_000,
            memory_mib=512,
            ephemeral_storage_mib=None,
            deadline_seconds=300,
            runtime_retry_count=0,
            launcher_retry_count=1,
        ),
        schedule=ScheduleProjection(
            task_count=1,
            maximum_parallelism=1,
            expression=None,
            time_zone=None,
            paused=True,
        ),
        network=NetworkPlacement(),
        labels=(),
        observability=ObservabilityProjection(
            log_destination="cloud_logging",
            metric_namespace="run.googleapis.com",
            alert_target=None,
            retention_days=None,
        ),
    )


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="gcp-bigquery",
        environment="gcp",
        project="demo",
        graph="hosted-graph",
        graph_revision="graph-r1",
        graph_content_sha256="c" * 64,
        backend_id="cloud_run",
        profile_id="gcp",
        image=IMAGE,
        execution_template=_template(),
        deadline_seconds=300,
        retry_policy=RetryPolicy(max_attempts=2),
    )


def _binding() -> CloudRunBinding:
    return CloudRunBinding(
        project_id=PROJECT,
        region=REGION,
        deployment_name="gcp_cloud_run",
        profile_id="gcp",
        pipeline_id=PIPELINE,
        job_name=JOB,
        runtime_service_account=f"dander-runtime@{PROJECT}.iam.gserviceaccount.com",
    )


def _job(plan: ExecutionPlan) -> dict[str, object]:
    binding = _binding()
    return {
        "name": binding.job_resource,
        "etag": "job-etag-1",
        "template": {
            "taskCount": 1,
            "parallelism": 1,
            "template": {
                "serviceAccount": binding.runtime_service_account,
                "containers": [
                    {"image": plan.image, "args": list(plan.execution_template.command)}
                ],
            },
        },
    }


def _backend(
    *,
    transport: _Transport | None = None,
) -> tuple[CloudRunExecutionBackend, ExecutionPlan, _Transport]:
    plan = _plan()
    selected_transport = transport or _Transport(_job(plan))
    return (
        CloudRunExecutionBackend(
            {plan.revision: _binding()},
            transport=selected_transport,
            clock=lambda: NOW,
        ),
        plan,
        selected_transport,
    )


def _start(backend: CloudRunExecutionBackend, plan: ExecutionPlan) -> BackendHandle:
    return backend.submit_or_adopt(
        plan,
        run_id="run-hosted-001",
        attempt_id="attempt-1-hosted",
        trigger=RunTrigger(kind=TriggerKind.API, trigger_id="control-api"),
    )


def test_submit_uses_tokenized_identity_and_restart_adopts_one_execution() -> None:
    backend, plan, transport = _backend()

    first = _start(backend, plan)
    restarted, _, _ = _backend(transport=transport)
    adopted = _start(restarted, plan)

    token = hashlib.sha256(b"run-hosted-001\0attempt-1-hosted").hexdigest()[:16]
    assert first == adopted
    assert first.execution_id.endswith(f"/{JOB}-{token}")
    patch_calls = [call for call in transport.calls if call[0] == "PATCH"]
    assert len(patch_calls) == 1
    update = patch_calls[0][2]["json"]
    assert isinstance(update, dict)
    assert update["template"] == transport.job["template"]
    assert update["etag"] == "job-etag-1"


def test_submit_reconciles_a_lost_patch_response_without_duplicate_effect() -> None:
    plan = _plan()
    transport = _Transport(_job(plan), lose_patch_response=True)
    backend, plan, transport = _backend(transport=transport)

    handle = _start(backend, plan)

    assert handle.execution_id in transport.executions
    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 1


def test_submit_adopts_committed_token_before_execution_is_visible() -> None:
    backend, plan, transport = _backend()
    first = _start(backend, plan)
    transport.executions.clear()

    restarted, _, _ = _backend(transport=transport)
    adopted = _start(restarted, plan)

    assert adopted == first
    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 1


def test_submit_rejects_deployed_job_drift_before_mutation() -> None:
    backend, plan, transport = _backend()
    template = transport.job["template"]
    assert isinstance(template, dict)
    task = template["template"]
    assert isinstance(task, dict)
    containers = task["containers"]
    assert isinstance(containers, list)
    assert isinstance(containers[0], dict)
    containers[0]["image"] = IMAGE.replace("b" * 64, "a" * 64)

    with pytest.raises(ExecutionBackendError, match="immutable execution plan"):
        _start(backend, plan)

    assert not [call for call in transport.calls if call[0] == "PATCH"]


def test_observe_normalizes_start_success_failure_and_cancellation() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)

    running = backend.observe(plan, handle)
    assert running.execution_state is BackendExecutionState.RUNNING
    execution = transport.executions[handle.execution_id]
    execution.update(
        {
            "completionTime": "2026-08-26T18:01:00Z",
            "succeededCount": 1,
        }
    )
    succeeded = backend.observe(plan, handle)
    assert succeeded.outcome is RunOutcome.SUCCEEDED
    assert succeeded.results_state is ResultsState.AVAILABLE
    assert succeeded.cleanup_state is CleanupState.CONFIRMED

    execution.update({"succeededCount": 0, "failedCount": 1})
    failed = backend.observe(plan, handle)
    assert failed.outcome is RunOutcome.FAILED
    assert failed.failure_code == "launcher_execution_failed"

    execution.update({"failedCount": 0, "cancelledCount": 1})
    canceled = backend.observe(plan, handle)
    assert canceled.outcome is RunOutcome.CANCELED


def test_logs_are_bounded_paginated_and_execution_scoped() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)
    transport.log_response = {
        "entries": [
            {"timestamp": "2026-08-26T18:00:01Z", "textPayload": "runtime started"},
            {
                "timestamp": "2026-08-26T18:00:02Z",
                "jsonPayload": {"status": "succeeded"},
            },
        ],
        "nextPageToken": "page-2",
    }

    page = backend.logs(plan, handle, cursor=None, limit=2)

    assert [record.message for record in page.records] == [
        "runtime started",
        '{"status":"succeeded"}',
    ]
    assert page.next_cursor == "page-2"
    log_call = transport.calls[-1]
    body = log_call[2]["json"]
    assert isinstance(body, dict)
    assert JOB in str(body["filter"])
    assert handle.execution_id.rsplit("/", maxsplit=1)[-1] in str(body["filter"])


def test_provider_failures_are_sanitized_as_backend_errors() -> None:
    plan = _plan()
    transport = _Transport(_job(plan), fail_job_read=True)
    backend, plan, transport = _backend(transport=transport)

    with pytest.raises(ExecutionBackendError, match="Job lookup"):
        _start(backend, plan)

    transport.fail_job_read = False
    handle = _start(backend, plan)
    transport.fail_log_read = True
    with pytest.raises(ExecutionBackendError, match="logs are unavailable"):
        backend.logs(plan, handle, cursor=None, limit=10)


def test_cancel_is_idempotent_after_terminal_observation() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)

    backend.cancel(plan, handle)
    backend.cancel(plan, handle)

    assert len([call for call in transport.calls if call[1].endswith(":cancel")]) == 1


def test_unregistered_plan_and_foreign_handle_fail_before_provider_mutation() -> None:
    backend, plan, transport = _backend()
    changed = replace(plan, plan_id="different-plan")
    with pytest.raises(ExecutionBackendError, match="not registered"):
        _start(backend, changed)
    handle = _start(backend, plan)
    with pytest.raises(ExecutionBackendError, match="outside"):
        backend.observe(plan, replace(handle, execution_id="projects/foreign/executions/nope"))
    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 1


def test_observe_rejects_execution_from_a_foreign_parent_job() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)
    transport.executions[handle.execution_id]["job"] = "foreign-job"

    with pytest.raises(ExecutionBackendError, match="unexpected execution"):
        backend.observe(plan, handle)


def test_close_releases_transport_once() -> None:
    backend, _plan, transport = _backend()

    backend.close()
    backend.close()

    assert transport.close_count == 1
