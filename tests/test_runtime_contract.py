"""Stable JSON boundary for launchers invoking the Dander OCI image."""

from __future__ import annotations

import json

import pytest

from dander.executor import PipelineExecutionResult
from dander.runtime import EndpointRunResult, PipelineRunResult
from dander.runtime_contract import (
    RUNTIME_CONTRACT,
    RuntimeContractError,
    RuntimeOutcome,
    resolve_runtime_run_id,
)


def _result(*, skipped: bool = False) -> PipelineExecutionResult:
    return PipelineExecutionResult(
        run_id="launcher-run-42",
        pipeline_id="example_pipeline",
        ingestion=PipelineRunResult(
            run_id="launcher-run-42",
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


def test_runtime_outcome_is_one_non_sensitive_machine_readable_record() -> None:
    payload = json.loads(RuntimeOutcome.completed(_result()).to_json())

    assert payload == {
        "contract": RUNTIME_CONTRACT,
        "event": "runtime.completed",
        "status": "succeeded",
        "run_id": "launcher-run-42",
        "pipeline_id": "example_pipeline",
        "outputs": {
            "source": "example",
            "endpoints": [
                {
                    "name": "widgets",
                    "extracted_rows": 3,
                    "affected_rows": 2,
                    "cursor_committed": True,
                }
            ],
            "models": ["stg_widgets"],
            "metrics": {
                "endpoints": 1,
                "extracted_rows": 3,
                "affected_rows": 2,
                "models": 1,
                "assertions": 2,
                "assets": 1,
            },
        },
    }
    assert "2026-08-06" not in RuntimeOutcome.completed(_result()).to_json()


def test_runtime_outcome_distinguishes_overlap_and_failure() -> None:
    skipped = json.loads(RuntimeOutcome.completed(_result(skipped=True)).to_json())
    failed = json.loads(
        RuntimeOutcome.failed(run_id="launcher-run-42", pipeline_id="example_pipeline").to_json()
    )

    assert skipped["status"] == "skipped"
    assert failed["status"] == "failed"
    assert failed["error_code"] == "runtime_failed"
    assert failed["outputs"] == {}


def test_runtime_run_id_accepts_launcher_ids_and_rejects_log_unsafe_values() -> None:
    assert resolve_runtime_run_id("cloud-run:execution_42") == "cloud-run:execution_42"
    assert len(resolve_runtime_run_id(None)) == 32

    with pytest.raises(RuntimeContractError, match="run id must be"):
        resolve_runtime_run_id("unsafe run\nnext-line")
