"""Stable JSON and launcher-context boundary for the Dander OCI runtime."""

from __future__ import annotations

import json
import signal

import pytest

from dander.executor import PipelineExecutionResult
from dander.runtime import EndpointRunResult, PipelineRunResult
from dander.runtime_contract import (
    RUNTIME_CONTRACT,
    LauncherContext,
    RuntimeCancelledError,
    RuntimeContractError,
    RuntimeEvent,
    graceful_signal_handlers,
    validate_runtime_contract,
)


def _context() -> LauncherContext:
    return LauncherContext.from_environment(
        {
            "DANDER_RUN_ID": "cloud-run:execution-42",
            "DANDER_LAUNCHER": "cloud_run",
            "DANDER_LAUNCHER_EXECUTION_ID": "execution-42",
            "DANDER_ATTEMPT": "2",
            "DANDER_SHARD_INDEX": "0",
            "DANDER_SHARD_COUNT": "1",
            "DANDER_DEADLINE_AT": "2026-08-07T12:00:00Z",
            "DANDER_PRINCIPAL": "service-account:runtime@example.invalid",
        }
    )


def _result(*, skipped: bool = False) -> PipelineExecutionResult:
    return PipelineExecutionResult(
        run_id="cloud-run:execution-42",
        pipeline_id="example_pipeline",
        ingestion=PipelineRunResult(
            run_id="cloud-run:execution-42",
            source="example",
            endpoints=(
                EndpointRunResult(
                    endpoint="widgets",
                    extracted=3,
                    affected=2,
                    committed_cursor="2026-08-06T12:00:00Z",
                ),
            ),
        ),
        models=("stg_widgets",),
        assertions=2,
        assets=1,
        skipped=skipped,
    )


def test_runtime_events_are_json_lines_without_cursor_values() -> None:
    started = json.loads(
        RuntimeEvent.started(
            context=_context(), pipeline_id="example_pipeline", platform="gcp"
        ).to_json()
    )
    completed_json = RuntimeEvent.completed(_result(), context=_context(), platform="gcp").to_json()
    completed = json.loads(completed_json)

    assert started["contract"] == RUNTIME_CONTRACT
    assert started["event"] == "runtime.started"
    assert started["dimensions"] == {
        "attempt": 2,
        "deadline_at": "2026-08-07T12:00:00Z",
        "launcher": "cloud_run",
        "launcher_execution_id": "execution-42",
        "principal": "service-account:runtime@example.invalid",
        "shard_count": 1,
        "shard_index": 0,
    }
    assert completed["event"] == "runtime.completed"
    assert completed["status"] == "succeeded"
    assert completed["outputs"]["metrics"]["extracted_rows"] == 3
    assert completed["outputs"]["endpoints"][0]["cursor_committed"] is True
    assert "2026-08-06" not in completed_json


def test_runtime_event_distinguishes_overlap_and_sanitized_failure() -> None:
    skipped = json.loads(
        RuntimeEvent.completed(_result(skipped=True), context=_context(), platform="gcp").to_json()
    )
    failed_json = RuntimeEvent.failed(
        context=_context(),
        pipeline_id="example_pipeline",
        platform="gcp",
        stage="runtime",
        failure_code="authentication_failed",
        retryable=False,
    ).to_json()
    failed = json.loads(failed_json)

    assert skipped["status"] == "skipped"
    assert failed["status"] == "failed"
    assert failed["failure_code"] == "authentication_failed"
    assert failed["outputs"] == {}
    assert "credential-value" not in failed_json


def test_launcher_context_validates_values_and_cloud_run_fallbacks() -> None:
    context = LauncherContext.from_environment(
        {
            "DANDER_LAUNCHER": "cloud_run",
            "CLOUD_RUN_EXECUTION": "execution-123",
            "CLOUD_RUN_TASK_ATTEMPT": "3",
            "CLOUD_RUN_TASK_INDEX": "1",
            "CLOUD_RUN_TASK_COUNT": "2",
            "DANDER_DEADLINE_AT": "2026-08-07T12:00:00Z",
        }
    )

    assert context.run_id == "execution-123"
    assert context.execution_id == "execution-123"
    assert context.attempt == 4
    assert context.shard_index == 1

    with pytest.raises(RuntimeContractError, match="shard index"):
        LauncherContext.from_environment({"DANDER_SHARD_INDEX": "2", "DANDER_SHARD_COUNT": "2"})
    with pytest.raises(RuntimeContractError, match="run id"):
        LauncherContext.from_environment({"DANDER_RUN_ID": "unsafe run\nnext-line"})
    with pytest.raises(RuntimeContractError, match="deadline"):
        LauncherContext.from_environment({"DANDER_DEADLINE_AT": "2026-08-07T12:00:00"})
    with pytest.raises(RuntimeContractError, match="attempt"):
        LauncherContext.from_environment({"DANDER_ATTEMPT": "0"})

    first_cloud_run_attempt = LauncherContext.from_environment({"CLOUD_RUN_TASK_ATTEMPT": "0"})
    assert first_cloud_run_attempt.attempt == 1


def test_contract_version_fails_closed() -> None:
    validate_runtime_contract(RUNTIME_CONTRACT)
    with pytest.raises(RuntimeContractError, match="unsupported runtime contract"):
        validate_runtime_contract("io.dander.runtime/v2")


def test_sigterm_handler_becomes_graceful_cancellation() -> None:
    with graceful_signal_handlers(), pytest.raises(RuntimeCancelledError) as raised:
        signal.raise_signal(signal.SIGTERM)

    assert raised.value.signal_name == "SIGTERM"
