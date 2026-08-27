"""Hosted Control execution through fixed-size Managed Spark batches."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from dander.control.dataproc_serverless_execution_backend import (
    DataprocServerlessExecutionBackend,
)
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
    attempt_identity,
)
from dander.deployment.projection import (
    EXECUTION_PROJECTION_SCHEMA,
    ExecutionTemplate,
    NetworkPlacement,
    ObservabilityProjection,
    ResourceProjection,
    ScheduleProjection,
)
from dander.physical_plan import (
    ExchangeTransport,
    PartitioningStrategy,
    PhysicalExchange,
    PhysicalExecutionMode,
    PhysicalPlan,
    PhysicalStage,
    serialize_physical_plan,
)
from dander.providers.dataproc_serverless import (
    DataprocServerlessBinding,
    DataprocServerlessOperationError,
)
from dander.runtime_contract import RUNTIME_CONTRACT

PROJECT = "dander-unit-project"
REGION = "us-central1"
PIPELINE = "hosted_graph"
STAGING_BUCKET = "dander-spark-stage"
IMAGE = f"{REGION}-docker.pkg.dev/{PROJECT}/dander/spark@sha256:" + "b" * 64
TAGGED_IMAGE = f"{REGION}-docker.pkg.dev/{PROJECT}/dander/spark:dander-unit-immutable"
DRIVER = f"gs://{STAGING_BUCKET}/drivers/spark-driver-" + "d" * 64 + ".py"
NOW = datetime(2026, 8, 27, 14, tzinfo=UTC)
RUN_ID = "run-hosted-001"
ATTEMPT_ID = attempt_identity(RUN_ID, 1)


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
    batches: dict[str, dict[str, object]] = field(default_factory=dict)
    calls: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)
    log_response: dict[str, object] = field(default_factory=dict)
    lose_create_response: bool = False
    fail_log_read: bool = False
    close_count: int = 0

    def request(self, method: str, url: str, **kwargs: object) -> _Response:
        self.calls.append((method, url, dict(kwargs)))
        if url.endswith("/entries:list"):
            if self.fail_log_read:
                raise OSError("provider transport secret")
            return _Response(200, self.log_response)
        if method == "POST" and url.endswith("/batches"):
            params = kwargs["params"]
            body = kwargs["json"]
            assert isinstance(params, dict)
            assert isinstance(body, dict)
            batch_id = params["batchId"]
            assert isinstance(batch_id, str)
            resource = f"projects/{PROJECT}/locations/{REGION}/batches/{batch_id}"
            operation = f"projects/{PROJECT}/regions/{REGION}/operations/op-{batch_id}"
            self.batches[resource] = {
                "name": resource,
                "state": "PENDING",
                "operation": operation,
                **body,
            }
            if self.lose_create_response:
                raise OSError("provider transport secret")
            return _Response(200, {"name": operation, "done": False})
        if method == "POST" and url.endswith(":cancel"):
            operation = url.removeprefix("https://dataproc.googleapis.com/v1/").removesuffix(
                ":cancel"
            )
            canceling_batch = next(
                item for item in self.batches.values() if item["operation"] == operation
            )
            canceling_batch["state"] = "CANCELLING"
            return _Response(200, {})
        resource = url.removeprefix("https://dataproc.googleapis.com/v1/")
        if method == "GET" and "/batches/" in resource:
            found_batch = self.batches.get(resource)
            return (
                _Response(200, _provider_observed_batch(found_batch))
                if found_batch is not None
                else _Response(404, {})
            )
        raise AssertionError((method, url, kwargs))

    def close(self) -> None:
        self.close_count += 1


def _provider_observed_batch(batch: dict[str, object]) -> dict[str, object]:
    observed = copy.deepcopy(batch)
    runtime = observed["runtimeConfig"]
    assert isinstance(runtime, dict)
    runtime["version"] = f"{runtime['version']}.39"
    properties = runtime["properties"]
    assert isinstance(properties, dict)
    runtime["properties"] = {
        f"{'dataproc' if key.startswith('dataproc.') else 'spark'}:{key}": value
        for key, value in properties.items()
    }
    return observed


def _physical_plan() -> PhysicalPlan:
    return PhysicalPlan(
        pipeline_id=PIPELINE,
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        stages=(
            PhysicalStage(
                stage_id="extract",
                operators=("extract",),
                partition_count=2,
            ),
            PhysicalStage(
                stage_id="publish",
                operators=("publish",),
                partition_count=4,
                depends_on=("extract",),
            ),
        ),
        exchanges=(
            PhysicalExchange(
                exchange_id="extract-publish",
                producer_stage_id="extract",
                consumer_stage_id="publish",
                transport=ExchangeTransport.OBJECT_STORE,
                partitioning=PartitioningStrategy.ROUND_ROBIN,
                partition_count=4,
            ),
        ),
        maximum_parallelism=4,
    )


def _template() -> ExecutionTemplate:
    physical = _physical_plan()
    return ExecutionTemplate(
        schema=EXECUTION_PROJECTION_SCHEMA,
        contract=RUNTIME_CONTRACT,
        pipeline_id=PIPELINE,
        profile_id="gcp",
        launcher="dataproc_serverless",
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
            "--physical-plan",
            serialize_physical_plan(physical).decode(),
        ),
        configuration_reference="/app/dander.yaml",
        environment=(("GCP_PROJECT_ID", PROJECT),),
        secret_bindings=(),
        workload_identity=f"dander-spark@{PROJECT}.iam.gserviceaccount.com",
        resources=ResourceProjection(
            cpu_millis=4_000,
            memory_mib=16_384,
            ephemeral_storage_mib=None,
            deadline_seconds=600,
            runtime_retry_count=0,
            launcher_retry_count=1,
        ),
        schedule=ScheduleProjection(
            task_count=2,
            maximum_parallelism=2,
            expression=None,
            time_zone=None,
            paused=True,
        ),
        network=NetworkPlacement(
            placement=(f"projects/{PROJECT}/regions/{REGION}/subnetworks/dander-spark")
        ),
        labels=(),
        observability=ObservabilityProjection(
            log_destination="cloud_logging",
            metric_namespace="dataproc.googleapis.com",
            alert_target=None,
            retention_days=None,
        ),
        extensions=(
            ("spark.container_image_tag", TAGGED_IMAGE),
            ("spark.main_python_file_uri", DRIVER),
            ("spark.runtime_version", "2.3"),
            ("spark.staging_bucket", STAGING_BUCKET),
        ),
    )


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="gcp-spark-bigquery",
        environment="gcp",
        project="demo",
        graph="hosted-graph",
        graph_revision="graph-r1",
        graph_content_sha256="c" * 64,
        backend_id="dataproc_serverless",
        profile_id="gcp",
        image=IMAGE,
        execution_template=_template(),
        deadline_seconds=600,
        retry_policy=RetryPolicy(max_attempts=2),
        physical_plan=_physical_plan(),
    )


def _binding(plan: ExecutionPlan) -> DataprocServerlessBinding:
    return DataprocServerlessBinding.from_execution_template(
        plan.execution_template,
        project_id=PROJECT,
    )


def _backend(
    transport: _Transport | None = None,
) -> tuple[DataprocServerlessExecutionBackend, ExecutionPlan, _Transport]:
    plan = _plan()
    selected_transport = transport or _Transport()
    return (
        DataprocServerlessExecutionBackend(
            {plan.revision: _binding(plan)},
            transport=selected_transport,
            clock=lambda: NOW,
        ),
        plan,
        selected_transport,
    )


def _start(
    backend: DataprocServerlessExecutionBackend,
    plan: ExecutionPlan,
) -> BackendHandle:
    return backend.submit_or_adopt(
        plan,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        trigger=RunTrigger(kind=TriggerKind.API, trigger_id="control-api"),
    )


def test_submit_is_deterministic_fixed_size_and_restart_adopts() -> None:
    backend, plan, transport = _backend()

    first = _start(backend, plan)
    restarted, _, _ = _backend(transport)
    adopted = _start(restarted, plan)

    assert first == adopted
    assert first.execution_id.startswith(f"projects/{PROJECT}/locations/{REGION}/batches/dander-")
    create_calls = [
        call for call in transport.calls if call[0] == "POST" and call[1].endswith("/batches")
    ]
    assert len(create_calls) == 1
    params = create_calls[0][2]["params"]
    body = create_calls[0][2]["json"]
    assert isinstance(params, dict)
    assert isinstance(body, dict)
    assert len(str(params["requestId"])) == 36
    runtime = body["runtimeConfig"]
    assert isinstance(runtime, dict)
    properties = runtime["properties"]
    assert isinstance(properties, dict)
    assert properties["spark.dynamicAllocation.enabled"] == "false"
    assert properties["spark.executor.instances"] == "2"
    assert "autotuningConfig" not in runtime
    assert runtime["containerImage"] == TAGGED_IMAGE
    environment = body["environmentConfig"]
    assert isinstance(environment, dict)
    assert environment["executionConfig"] == {
        "serviceAccount": f"dander-spark@{PROJECT}.iam.gserviceaccount.com",
        "stagingBucket": STAGING_BUCKET,
        "ttl": "600s",
        "authenticationConfig": {"userWorkloadAuthenticationType": "SERVICE_ACCOUNT"},
        "subnetworkUri": f"projects/{PROJECT}/regions/{REGION}/subnetworks/dander-spark",
    }


def test_submit_reconciles_lost_create_response_without_duplicate_batch() -> None:
    transport = _Transport(lose_create_response=True)
    backend, plan, _ = _backend(transport)

    handle = _start(backend, plan)

    assert handle.execution_id in transport.batches
    assert len([call for call in transport.calls if call[1].endswith("/batches")]) == 1


def test_observe_normalizes_running_success_failure_and_cancellation() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)
    batch = transport.batches[handle.execution_id]

    batch["state"] = "RUNNING"
    running = backend.observe(plan, handle)
    assert running.execution_state is BackendExecutionState.RUNNING

    transport.log_response = {
        "entries": [
            {
                "timestamp": "2026-08-27T14:00:02Z",
                "jsonPayload": {
                    "class": "org.apache.spark.deploy.PythonRunner",
                    "message": json.dumps(_completion_payload()),
                },
            }
        ]
    }
    batch["state"] = "SUCCEEDED"
    succeeded = backend.observe(plan, handle)
    assert succeeded.outcome is RunOutcome.SUCCEEDED
    assert succeeded.results_state is ResultsState.AVAILABLE
    assert succeeded.cleanup_state is CleanupState.CONFIRMED
    assert succeeded.result_summary is not None
    assert succeeded.result_summary.extracted_rows == 3

    batch["state"] = "FAILED"
    failed = backend.observe(plan, handle)
    assert failed.outcome is RunOutcome.FAILED
    assert failed.failure_code == "spark_batch_failed"

    batch["state"] = "CANCELLED"
    canceled = backend.observe(plan, handle)
    assert canceled.outcome is RunOutcome.CANCELED


def test_logs_are_bounded_paginated_and_batch_scoped() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)
    transport.log_response = {
        "entries": [
            {
                "timestamp": "2026-08-27T14:00:01Z",
                "textPayload": "runtime started",
                "severity": "INFO",
            },
            {
                "timestamp": "2026-08-27T14:00:02Z",
                "jsonPayload": {"message": "runtime completed", "status": "ok"},
            },
            {"timestamp": "2026-08-27T14:00:03Z", "jsonPayload": {"status": "ok"}},
        ],
        "nextPageToken": "page-2",
    }

    page = backend.logs(plan, handle, cursor=None, limit=3)

    assert [record.message for record in page.records] == [
        "runtime started",
        "runtime completed",
        '{"status":"ok"}',
    ]
    assert page.records[0].level == "info"
    assert page.next_cursor == "page-2"
    body = transport.calls[-1][2]["json"]
    assert isinstance(body, dict)
    assert f'resource.labels.location="{REGION}"' in str(body["filter"])
    assert handle.execution_id.rsplit("/", maxsplit=1)[-1] in str(body["filter"])


def test_cancel_uses_batch_operation_and_is_idempotent_while_cancelling() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)
    transport.batches[handle.execution_id]["state"] = "RUNNING"

    backend.cancel(plan, handle)
    backend.cancel(plan, handle)

    assert len([call for call in transport.calls if call[1].endswith(":cancel")]) == 1


def test_plan_drift_foreign_handles_and_provider_failures_fail_closed() -> None:
    backend, plan, transport = _backend()
    changed = replace(plan, plan_id="different-plan")
    with pytest.raises(ExecutionBackendError, match="not registered"):
        _start(backend, changed)
    handle = _start(backend, plan)
    with pytest.raises(ExecutionBackendError, match="outside"):
        backend.observe(
            plan,
            replace(handle, execution_id="projects/foreign/locations/x/batches/dander-" + "a" * 40),
        )
    transport.fail_log_read = True
    with pytest.raises(ExecutionBackendError, match="logs are unavailable"):
        backend.logs(plan, handle, cursor=None, limit=10)


def test_observe_rejects_replaced_or_drifted_batch() -> None:
    backend, plan, transport = _backend()
    handle = _start(backend, plan)
    batch = transport.batches[handle.execution_id]
    runtime = batch["runtimeConfig"]
    assert isinstance(runtime, dict)
    properties = runtime["properties"]
    assert isinstance(properties, dict)
    properties["spark.dynamicAllocation.enabled"] = "true"

    with pytest.raises(ExecutionBackendError, match="observed Managed Spark batch"):
        backend.observe(plan, handle)

    properties["spark.dynamicAllocation.enabled"] = "false"
    runtime["version"] = "2.4"
    with pytest.raises(ExecutionBackendError, match="observed Managed Spark batch"):
        backend.observe(plan, handle)


def test_binding_requires_content_addressed_driver_and_exact_extensions() -> None:
    plan = _plan()
    changed = replace(
        plan.execution_template,
        extensions=(
            ("spark.container_image_tag", TAGGED_IMAGE),
            ("spark.main_python_file_uri", f"gs://{STAGING_BUCKET}/driver.py"),
            ("spark.runtime_version", "2.3"),
            ("spark.staging_bucket", STAGING_BUCKET),
        ),
    )

    with pytest.raises(DataprocServerlessOperationError, match="content-addressed"):
        DataprocServerlessBinding.from_execution_template(changed, project_id=PROJECT)


def test_binding_rejects_tag_for_a_different_immutable_image_package() -> None:
    plan = _plan()
    changed = replace(
        plan.execution_template,
        extensions=(
            (
                "spark.container_image_tag",
                f"{REGION}-docker.pkg.dev/{PROJECT}/dander/other:dander-unit-immutable",
            ),
            ("spark.main_python_file_uri", DRIVER),
            ("spark.runtime_version", "2.3"),
            ("spark.staging_bucket", STAGING_BUCKET),
        ),
    )

    with pytest.raises(DataprocServerlessOperationError, match="immutable plan image package"):
        DataprocServerlessBinding.from_execution_template(changed, project_id=PROJECT)


def test_close_releases_transport_once() -> None:
    backend, _plan, transport = _backend()

    backend.close()
    backend.close()

    assert transport.close_count == 1
