"""Hosted Control execution through existing GCP Cloud Run Jobs."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

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
from dander.pipeline.repair import GraphRepairWindow
from dander.providers.cloud_run import CloudRunBinding
from dander.runtime_contract import RUNTIME_CONTRACT

PROJECT = "dander-unit-project"
REGION = "us-central1"
JOB = "dander-hosted-graph"
PIPELINE = "hosted_graph"
IMAGE = f"{REGION}-docker.pkg.dev/{PROJECT}/dander/runtime@sha256:" + "b" * 64
RESOLVED_IMAGE = IMAGE.replace("b" * 64, "d" * 64)
IMAGE_METADATA_URL = (
    f"https://artifactregistry.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/"
    "repositories/dander/dockerImages/runtime%40sha256%3A" + "b" * 64
)
NOW = datetime(2026, 8, 26, 18, tzinfo=UTC)


def _completion_payload() -> dict[str, object]:
    return {
        "contract": RUNTIME_CONTRACT,
        "event": "runtime.completed",
        "pipeline_id": PIPELINE,
        "status": "succeeded",
        "outputs": {
            "metrics": {
                "endpoints": 1,
                "extracted_rows": 3,
                "affected_rows": 3,
                "models": 1,
                "assertions": 3,
                "assets": 1,
            },
            "telemetry": {
                "duration_ms": 1_000,
                "retry_count": 0,
                "rows_read": 3,
                "rows_written": 3,
                "rows_affected": 3,
                "bytes_read": 30,
                "bytes_written": 30,
                "bytes_processed": 30,
                "bytes_billed": 0,
                "queue_duration_ms": 0,
                "execution_duration_ms": 10,
                "spill_bytes": 0,
                "operations": [{"operation": "load"}],
            },
        },
    }


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
    image_metadata: dict[str, object] = field(default_factory=dict)
    image_status: int = 200
    lose_patch_response: bool = False
    reject_restore: bool = False
    supersede_before_restore: bool = False
    fail_job_read: bool = False
    fail_log_read: bool = False
    close_count: int = 0

    def request(self, method: str, url: str, **kwargs: object) -> _Response:
        self.calls.append((method, url, dict(kwargs)))
        if method == "GET" and url == IMAGE_METADATA_URL:
            return _Response(self.image_status, self.image_metadata)
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
            if "startExecutionToken" not in payload:
                if self.supersede_before_restore:
                    self.job["startExecutionToken"] = "f" * 16
                    self.job["etag"] = "new-launch-etag"
                    return _Response(409, {})
                if self.reject_restore:
                    return _Response(503, {})
                self.job.update(deepcopy(payload))
                if self.lose_patch_response:
                    raise OSError("provider transport secret")
                return _Response(200, {"name": "operations/restore"})
            token = payload["startExecutionToken"]
            assert isinstance(token, str)
            self.job.update(deepcopy(payload))
            template = payload["template"]
            assert isinstance(template, dict)
            execution_resource = (
                f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB}/executions/{JOB}-{token}"
            )
            self.executions[execution_resource] = {
                "name": execution_resource,
                "job": JOB,
                "taskCount": 1,
                "template": deepcopy(template["template"]),
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


def _start(
    backend: CloudRunExecutionBackend,
    plan: ExecutionPlan,
    *,
    trigger: RunTrigger | None = None,
    run_id: str = "run-hosted-001",
) -> BackendHandle:
    return backend.submit_or_adopt(
        plan,
        run_id=run_id,
        attempt_id="attempt-1-hosted",
        trigger=trigger or RunTrigger(kind=TriggerKind.API, trigger_id="control-api"),
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


def _repair_trigger() -> RunTrigger:
    return RunTrigger(
        kind=TriggerKind.API,
        trigger_id="control-api",
        repair_window=GraphRepairWindow(start_date=date(2026, 9, 1), end_date=date(2026, 9, 3)),
    )


def _execution_container(transport: _Transport, handle: BackendHandle) -> dict[str, object]:
    task = transport.executions[handle.execution_id]["template"]
    assert isinstance(task, dict)
    containers = task["containers"]
    assert isinstance(containers, list) and isinstance(containers[0], dict)
    return containers[0]


def _image_metadata() -> dict[str, object]:
    return {
        "uri": IMAGE,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "imageManifests": [
            {"os": "linux", "architecture": "amd64", "digest": "sha256:" + "d" * 64},
            {"os": "unknown", "architecture": "unknown", "digest": "sha256:" + "e" * 64},
        ],
    }


def test_resolved_index_image_allows_restart_adoption_and_terminal_repair_cleanup() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan, trigger=_repair_trigger())
    _execution_container(transport, handle)["image"] = RESOLVED_IMAGE
    transport.image_metadata = _image_metadata()
    restarted, _, _ = _backend(transport=transport)

    assert _start(restarted, plan, trigger=_repair_trigger()) == handle
    transport.executions[handle.execution_id].update(
        {"completionTime": "2026-08-26T18:01:00Z", "succeededCount": 1}
    )
    transport.log_response = {
        "entries": [{"timestamp": "2026-08-26T18:00:02Z", "jsonPayload": _completion_payload()}]
    }

    observed = restarted.observe(plan, handle)

    assert observed.outcome is RunOutcome.SUCCEEDED
    assert observed.results_state is ResultsState.AVAILABLE
    assert observed.result_summary is not None
    assert observed.cleanup_state is CleanupState.CONFIRMED
    assert len(transport.executions) == 1
    patches = [call[2]["json"] for call in transport.calls if call[0] == "PATCH"]
    assert len(patches) == 2
    restored = patches[1]
    assert isinstance(restored, dict)
    assert "startExecutionToken" not in restored
    template = restored["template"]
    assert isinstance(template, dict)
    task = template["template"]
    assert isinstance(task, dict)
    containers = task["containers"]
    assert isinstance(containers, list)
    assert containers[0]["image"] == IMAGE
    assert containers[0]["args"] == list(plan.execution_template.command)
    assert containers[0]["env"] == []


@pytest.mark.parametrize(
    "mismatch",
    ["index", "repository", "child", "platform", "ambiguous", "not-index", "unavailable"],
)
def test_resolved_image_mismatch_cannot_adopt_or_restore(mismatch: str) -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan, trigger=_repair_trigger())
    container = _execution_container(transport, handle)
    container["image"] = RESOLVED_IMAGE
    metadata = _image_metadata()
    manifests = metadata["imageManifests"]
    assert isinstance(manifests, list)
    if mismatch == "index":
        metadata["uri"] = RESOLVED_IMAGE
    elif mismatch == "repository":
        container["image"] = RESOLVED_IMAGE.replace("/dander/runtime", "/other/runtime")
    elif mismatch == "child":
        container["image"] = RESOLVED_IMAGE.replace("d" * 64, "f" * 64)
    elif mismatch == "platform":
        manifests[0]["architecture"] = "arm64"
    elif mismatch == "ambiguous":
        manifests.append(dict(manifests[0]))
    elif mismatch == "not-index":
        metadata["mediaType"] = "application/vnd.oci.image.manifest.v1+json"
    elif mismatch == "unavailable":
        transport.image_status = 503
    transport.image_metadata = metadata
    _fail_execution(transport, handle)

    with pytest.raises(ExecutionBackendError):
        _start(backend, plan, trigger=_repair_trigger())
    with pytest.raises(ExecutionBackendError):
        backend.observe(plan, handle)

    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 1
    assert len(transport.executions) == 1
    if mismatch == "repository":
        assert not [call for call in transport.calls if call[1] == IMAGE_METADATA_URL]


def test_repair_is_execution_bound_and_adopted_after_lost_response() -> None:
    backend, plan, transport = _backend(
        transport=_Transport(_job(_plan()), lose_patch_response=True)
    )

    handle = _start(backend, plan, trigger=_repair_trigger())
    adopted = _start(backend, plan, trigger=_repair_trigger())

    assert adopted == handle
    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 1
    container = _execution_container(transport, handle)
    assert container["args"] == [
        *plan.execution_template.command,
        "--graph-repair-contract",
        "io.dander.graph-repair/v1",
    ]
    environment = container["env"]
    assert isinstance(environment, list)
    payload = json.loads(environment[0]["value"])
    assert payload == {
        "start_date": "2026-09-01",
        "end_date": "2026-09-03",
        "graph_content_sha256": plan.graph_content_sha256,
        "execution_name": handle.execution_id.rsplit("/", maxsplit=1)[-1],
    }


@pytest.mark.parametrize("execution_visible", [True, False])
def test_adoption_rejects_another_repair_window_without_second_patch(
    execution_visible: bool,
) -> None:
    backend, plan, transport = _backend()
    _start(backend, plan, trigger=_repair_trigger())
    if not execution_visible:
        transport.executions.clear()
    changed = replace(
        _repair_trigger(),
        repair_window=GraphRepairWindow(start_date=date(2026, 9, 2), end_date=date(2026, 9, 3)),
    )

    with pytest.raises(ExecutionBackendError, match="different date-repair selection"):
        _start(backend, plan, trigger=changed)

    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 1


def test_normal_run_clears_repair_marker_and_window_without_changing_prior_execution() -> None:
    backend, plan, transport = _backend()
    repair = _start(backend, plan, trigger=_repair_trigger())

    normal = _start(backend, plan, run_id="next-normal-run")

    assert _execution_container(transport, normal)["args"] == list(plan.execution_template.command)
    assert _execution_container(transport, normal)["env"] == []
    assert _execution_container(transport, repair)["env"]
    assert _start(backend, plan, trigger=_repair_trigger()) == repair


def test_repair_adoption_requires_the_guard_that_makes_old_images_fail_closed() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan, trigger=_repair_trigger())
    _execution_container(transport, handle)["args"] = list(plan.execution_template.command)

    with pytest.raises(ExecutionBackendError, match="immutable execution plan"):
        _start(backend, plan, trigger=_repair_trigger())

    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 1


def _fail_execution(transport: _Transport, handle: BackendHandle) -> None:
    transport.executions[handle.execution_id].update(
        {"completionTime": "2026-08-26T18:01:00Z", "failedCount": 1}
    )


@pytest.mark.parametrize("lost_response", [False, True])
def test_terminal_repair_restores_job_without_starting_work_and_reconciles_lost_response(
    lost_response: bool,
) -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan, trigger=_repair_trigger())
    _fail_execution(transport, handle)
    transport.lose_patch_response = lost_response

    observed = backend.observe(plan, handle)
    backend.observe(plan, handle)

    assert observed.outcome is RunOutcome.FAILED
    assert observed.cleanup_state is CleanupState.CONFIRMED
    patches = [call[2]["json"] for call in transport.calls if call[0] == "PATCH"]
    assert len(patches) == 2
    cleanup = patches[1]
    assert isinstance(cleanup, dict)
    assert cleanup["etag"] == "job-etag-1"
    assert "startExecutionToken" not in cleanup
    assert "runExecutionToken" not in cleanup
    template = cleanup["template"]
    assert isinstance(template, dict)
    task = template["template"]
    assert isinstance(task, dict)
    containers = task["containers"]
    assert isinstance(containers, list)
    assert containers[0]["args"] == list(plan.execution_template.command)
    assert containers[0]["env"] == []
    assert len(transport.executions) == 1


def test_cleanup_conflict_preserves_newer_launch_configuration() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan, trigger=_repair_trigger())
    _fail_execution(transport, handle)
    transport.supersede_before_restore = True

    observed = backend.observe(plan, handle)

    assert observed.cleanup_state is CleanupState.CONFIRMED
    assert transport.job["startExecutionToken"] == "f" * 16
    assert transport.job["etag"] == "new-launch-etag"
    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 2
    backend.observe(plan, handle)
    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 2


def test_failed_cleanup_remains_pending_until_existing_reconciliation_retries() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan, trigger=_repair_trigger())
    _fail_execution(transport, handle)
    transport.reject_restore = True

    observed = backend.observe(plan, handle)

    assert observed.outcome is RunOutcome.FAILED
    assert observed.cleanup_state is CleanupState.PENDING
    transport.reject_restore = False
    assert backend.observe(plan, handle).cleanup_state is CleanupState.CONFIRMED
    assert len(transport.executions) == 1


def test_old_repair_cleanup_does_not_change_a_later_normal_launch() -> None:
    backend, plan, transport = _backend()
    repair = _start(backend, plan, trigger=_repair_trigger())
    normal = _start(backend, plan, run_id="new-normal")
    _fail_execution(transport, repair)
    current = deepcopy(transport.job)

    assert backend.observe(plan, repair).cleanup_state is CleanupState.CONFIRMED

    assert transport.job == current
    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 2
    assert _execution_container(transport, normal)["env"] == []


def test_normal_terminal_observation_does_not_patch_job_configuration() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)
    _fail_execution(transport, handle)
    transport.calls.clear()

    assert backend.observe(plan, handle).cleanup_state is CleanupState.CONFIRMED

    assert len(transport.calls) == 1
    assert transport.calls[0][0] == "GET"


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
    transport.log_response = {
        "entries": [{"timestamp": "2026-08-26T18:00:02Z", "jsonPayload": _completion_payload()}]
    }
    succeeded = backend.observe(plan, handle)
    assert succeeded.outcome is RunOutcome.SUCCEEDED
    assert succeeded.results_state is ResultsState.AVAILABLE
    assert succeeded.cleanup_state is CleanupState.CONFIRMED
    assert succeeded.result_summary is not None
    assert succeeded.result_summary.extracted_rows == 3

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


def test_cancellation_before_start_is_terminal_without_a_completion_timestamp() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)
    execution = transport.executions[handle.execution_id]
    execution.pop("startTime")
    execution.update(
        {
            "cancelledCount": 1,
            "conditions": [
                {"type": "Started", "state": "CONDITION_RECONCILING"},
                {
                    "type": "Completed",
                    "state": "CONDITION_FAILED",
                    "executionReason": "CANCELLED",
                },
            ],
        }
    )

    observed = backend.observe(plan, handle)
    assert observed.execution_state is BackendExecutionState.TERMINAL
    assert observed.outcome is RunOutcome.CANCELED
    assert observed.cleanup_state is CleanupState.CONFIRMED
    assert observed.results_state is ResultsState.UNAVAILABLE
    backend.cancel(plan, handle)
    assert not [call for call in transport.calls if call[1].endswith(":cancel")]


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
