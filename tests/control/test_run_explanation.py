"""Run explanations distinguish observed outcomes from missing measurements."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dander.control.models import RunState, RunStatusResponse, RunTelemetrySummary
from dander.control.run_explanation import RunExplanationResponse, explain_run

_RESULT_SCHEMA = "io.dander.control.execution-result-summary/v1"


def test_success_explains_collected_counts_without_claiming_a_data_diff() -> None:
    result = explain_run(
        RunStatusResponse(
            run_id="successful",
            state=RunState.SUCCEEDED,
            result_schema=_RESULT_SCHEMA,
            endpoints=2,
            extracted=1234,
            affected=27,
            models=1,
            assertions=3,
            assets=1,
        )
    )

    assert result.results_available
    assert any("1,234 rows from 2 endpoints" in detail for detail in result.details)
    assert any("27 affected rows" in detail for detail in result.details)
    assert any("3 assertions evaluated" in detail for detail in result.details)
    assert any("not a before/after data comparison" in caveat for caveat in result.caveats)
    assert result.next_action.kind == "inspect_outputs"
    assert "replay" not in result.available_actions


def test_success_without_collected_results_does_not_turn_defaults_into_measured_zeros() -> None:
    result = explain_run(RunStatusResponse(run_id="awaiting", state=RunState.SUCCEEDED))

    assert not result.results_available
    assert result.details == ()
    assert "awaiting" in result.summary
    assert any("not yet verified" in caveat for caveat in result.caveats)
    assert result.next_action.kind == "refresh"
    assert "0 rows" not in result.model_dump_json()


def test_counters_without_result_contract_do_not_establish_completed_work() -> None:
    result = explain_run(
        RunStatusResponse(
            run_id="uncollected",
            state=RunState.SUCCEEDED,
            extracted=900,
            affected=500,
            skipped=True,
        )
    )

    assert not result.results_available
    assert result.details == ()
    assert "skipped" not in result.summary
    assert result.next_action.kind == "refresh"


@pytest.mark.parametrize("logs_available", [False, True])
def test_skipped_work_does_not_invent_why_it_was_skipped(logs_available: bool) -> None:
    result = explain_run(
        RunStatusResponse(
            run_id="skipped",
            state=RunState.SUCCEEDED,
            result_schema=_RESULT_SCHEMA,
            skipped=True,
            logs_available=logs_available,
        )
    )

    assert result.results_available
    assert "skipped" in result.summary
    assert "0 rows" in " ".join(result.details)
    assert "does not include its reason" in result.next_action.reason
    assert result.next_action.kind == ("logs" if logs_available else "review_configuration")


@pytest.mark.parametrize(
    "state", [RunState.QUEUED, RunState.RUNNING, RunState.RETRYING, RunState.CANCELING]
)
def test_active_runs_recommend_observation_and_respect_cancel_availability(state: RunState) -> None:
    result = explain_run(
        RunStatusResponse(
            run_id="active",
            state=state,
            stage="reconciling",
            can_cancel=state is not RunState.CANCELING,
        )
    )

    assert result.next_action.kind == "refresh"
    assert "replay" not in result.available_actions
    assert ("cancel" in result.available_actions) == (state is not RunState.CANCELING)
    assert any("checking" in detail for detail in result.details)
    assert not result.results_available


def test_failed_run_points_to_available_logs_without_echoing_unsafe_diagnostics() -> None:
    result = explain_run(
        RunStatusResponse(
            run_id="failed",
            state=RunState.FAILED,
            stage="private-stage-with-token",
            failure_code="unrecognized-private-error",
            failure_summary="provider-password=private-diagnostic",
            logs_available=True,
            can_replay=True,
        )
    )

    assert result.next_action.kind == "logs"
    assert "replay" in result.available_actions
    assert "cancel" not in result.available_actions
    assert "underlying cause" in result.summary
    assert "private" not in result.model_dump_json()
    assert any("rolled back" in caveat for caveat in result.caveats)
    assert any("does not undo prior writes" in caveat for caveat in result.caveats)


def test_failed_run_without_logs_never_suggests_unavailable_logs_or_replay() -> None:
    result = explain_run(RunStatusResponse(run_id="failed", state=RunState.FAILED))

    assert result.next_action.kind == "review_configuration"
    assert "logs" not in result.available_actions
    assert "replay" not in result.available_actions
    assert "unknown" in " ".join(result.caveats)


def test_known_timeout_does_not_invent_the_cause_of_slow_execution() -> None:
    result = explain_run(
        RunStatusResponse(
            run_id="timeout",
            state=RunState.FAILED,
            failure_code="launcher_deadline_exceeded",
            logs_available=True,
        )
    )

    assert "time limit" in result.summary
    assert result.next_action.kind == "logs"
    assert "memory" not in result.model_dump_json()


def test_canceled_run_warns_about_existing_writes_before_replay() -> None:
    result = explain_run(
        RunStatusResponse(run_id="canceled", state=RunState.CANCELED, can_replay=True)
    )

    assert result.next_action.kind == "inspect_outputs"
    assert "replay" in result.available_actions
    assert "partial" in result.next_action.label
    assert any("rolled back" in caveat for caveat in result.caveats)


def test_telemetry_reports_observed_runtime_without_claiming_zero_cost_or_full_coverage() -> None:
    result = explain_run(
        RunStatusResponse(
            run_id="telemetry",
            state=RunState.SUCCEEDED,
            result_schema=_RESULT_SCHEMA,
            telemetry=RunTelemetrySummary(
                duration_ms=2500,
                operation_count=3,
                retry_count=1,
                rows_read=0,
                rows_written=0,
                rows_affected=0,
                bytes_read=0,
                bytes_written=0,
                bytes_processed=2048,
                bytes_billed=0,
                queue_duration_ms=0,
                execution_duration_ms=0,
                spill_bytes=0,
            ),
        )
    )

    assert any("2,500 ms" in detail and "1 retries" in detail for detail in result.details)
    assert any("2,048 bytes" in detail for detail in result.details)
    assert any("Actual billed cost is not collected" in caveat for caveat in result.caveats)
    assert any("every provider operation" in caveat for caveat in result.caveats)


def test_explanation_contract_rejects_extra_fields_at_each_object_boundary() -> None:
    result = explain_run(RunStatusResponse(run_id="closed", state=RunState.QUEUED))
    payload = result.model_dump(mode="json")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunExplanationResponse.model_validate({**payload, "raw_config": {}})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunExplanationResponse.model_validate(
            {
                **payload,
                "next_action": {**result.next_action.model_dump(), "command": "run-anything"},
            }
        )
