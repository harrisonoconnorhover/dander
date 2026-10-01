"""Readable, deterministic explanations of the evidence in a Control run status."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dander.control.models import ControlModel, RunState, RunStatusResponse

type RunActionKind = Literal[
    "refresh", "logs", "replay", "cancel", "inspect_outputs", "review_configuration", "none"
]


class RunExplanationAction(ControlModel):
    """One suggested next step, without performing a run mutation."""

    kind: RunActionKind
    label: str
    reason: str


class RunExplanationResponse(ControlModel):
    """A bounded explanation derived only from the current normalized run status."""

    run_id: str
    state: RunState
    results_available: bool
    summary: str
    details: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    caveats: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    next_action: RunExplanationAction
    available_actions: tuple[RunActionKind, ...] = Field(default_factory=tuple, max_length=7)


_ACTIVE_STATES = frozenset(
    {RunState.QUEUED, RunState.RUNNING, RunState.RETRYING, RunState.CANCELING}
)
_STATE_SUMMARIES = {
    RunState.QUEUED: "The run is queued; no completed outcome is available yet.",
    RunState.RUNNING: "The run is active; its outcome is not confirmed yet.",
    RunState.RETRYING: "The run is retrying; its final outcome is not confirmed yet.",
    RunState.CANCELING: "Cancellation was requested; the run has not stopped yet.",
    RunState.CANCELED: "The run was canceled.",
    RunState.FAILED: "The run failed; the status does not identify the underlying cause.",
}
# Stages and failure summaries can contain provider-supplied free text. Only known stage
# semantics are translated here; raw logs, configuration and arbitrary diagnostics stay out.
_STAGE_DETAILS = {
    "submitted": "The execution request has been submitted.",
    "starting": "The execution is starting.",
    "pending": "The execution is waiting to start.",
    "reconciling": "Control is checking the execution's latest state.",
    "pending_redrive": "The execution is awaiting a retry decision.",
}


def explain_run(status: RunStatusResponse) -> RunExplanationResponse:
    """Explain known outcomes without treating missing counters as measured zeros.

    Control may learn that an execution succeeded before it collects the runtime's result
    summary. ``result_schema`` is the evidence that counters were actually collected; the
    transport's default zeros alone never establish that no rows were processed.
    """
    results_available = status.result_schema is not None
    details: list[str] = []
    caveats: list[str] = []
    summary = _summary(status, results_available=results_available)

    if status.repair_window is not None:
        window = status.repair_window
        details.append(
            f"Output repair from retained raw data: {window.start_date.isoformat()} included "
            f"through {window.end_date.isoformat()} excluded (UTC)."
        )
        caveats.append(
            "This repair does not extract source records or advance normal source progress."
        )

    if status.state in _ACTIVE_STATES and status.stage in _STAGE_DETAILS:
        details.append(_STAGE_DETAILS[status.stage])
    if results_available:
        output_detail = (
            f"Recorded output: {status.affected:,} affected rows and {status.models:,} models."
        )
        if status.repair_window is not None:
            output_detail = "Output repair measurements have not been collected."
            if status.telemetry is not None:
                output_detail = (
                    f"Recorded repair: {status.telemetry.rows_written:,} rows written and "
                    f"{status.telemetry.rows_affected:,} inserted/deleted row operations "
                    f"across {status.models:,} outputs."
                )
        details.extend(
            (
                f"Recorded extraction: {status.extracted:,} rows "
                f"from {status.endpoints:,} endpoints.",
                output_detail,
                f"Recorded validation and catalog: {status.assertions:,} assertions evaluated "
                f"and {status.assets:,} assets.",
            )
        )
        caveats.append(
            "Recorded counts describe this run; they are not a before/after data comparison."
        )
        if status.telemetry is not None:
            telemetry = status.telemetry
            details.append(
                f"Recorded runtime: {telemetry.duration_ms:,} ms, "
                f"{telemetry.operation_count:,} operations and {telemetry.retry_count:,} retries."
            )
            if telemetry.bytes_processed > 0:
                details.append(f"Recorded processing: {telemetry.bytes_processed:,} bytes.")
            caveats.append(
                "Telemetry covers reported operations; zero or omitted counters do not prove "
                "that every provider operation was measured. Actual billed cost is not collected."
            )
        else:
            caveats.append("Runtime telemetry has not been collected for this run.")
    elif status.state is RunState.SUCCEEDED:
        caveats.append(
            "The execution reported success, but its result summary has not been collected. "
            "Row counts and completed data checks are not yet verified."
        )
    else:
        caveats.append(
            "No completed result summary is available; row counts and data checks are unknown."
        )

    if status.state in {RunState.FAILED, RunState.CANCELED, RunState.CANCELING}:
        caveats.append(
            "Stopping or failing does not confirm that earlier writes were rolled back. "
            "Inspect the outputs before another attempt."
        )
    if status.can_replay:
        caveats.append(
            "Replay repeats this repair window against the raw data available at that time; "
            "it does not restore a historical snapshot."
            if status.repair_window is not None
            else "Replay starts another execution; it does not undo prior writes or repair only "
            "a selected date range."
        )

    next_action = _next_action(status, results_available=results_available)
    actions: list[RunActionKind] = ["refresh"]
    if status.logs_available:
        actions.append("logs")
    if status.can_cancel:
        actions.append("cancel")
    if status.can_replay:
        actions.append("replay")
    if next_action.kind not in actions and next_action.kind != "none":
        actions.append(next_action.kind)
    return RunExplanationResponse(
        run_id=status.run_id,
        state=status.state,
        results_available=results_available,
        summary=summary,
        details=tuple(details),
        caveats=tuple(caveats),
        next_action=next_action,
        available_actions=tuple(actions),
    )


def _summary(status: RunStatusResponse, *, results_available: bool) -> str:
    if status.state is RunState.SUCCEEDED:
        if not results_available:
            return "The execution succeeded; awaiting its result measurements."
        if status.skipped:
            return "The execution completed, but the pipeline reported that its work was skipped."
        return "The run succeeded and its result measurements are available."
    if status.state is RunState.FAILED and status.failure_code == "launcher_deadline_exceeded":
        return "The execution exceeded its configured time limit."
    return _STATE_SUMMARIES[status.state]


def _next_action(status: RunStatusResponse, *, results_available: bool) -> RunExplanationAction:
    if status.state in _ACTIVE_STATES or (
        status.state is RunState.SUCCEEDED and not results_available
    ):
        return RunExplanationAction(
            kind="refresh",
            label="Refresh run status",
            reason="Wait for confirmed status and result measurements before starting another run.",
        )
    if status.state is RunState.FAILED:
        if status.logs_available:
            return RunExplanationAction(
                kind="logs",
                label="Inspect execution logs",
                reason="Find the reported error and check for partial writes "
                "before deciding to replay.",
            )
        return RunExplanationAction(
            kind="review_configuration",
            label="Review the execution and configuration",
            reason="Logs are not available through Control; inspect the execution and its outputs "
            "before deciding whether another attempt is appropriate.",
        )
    if status.state is RunState.CANCELED:
        return RunExplanationAction(
            kind="inspect_outputs",
            label="Inspect any partial outputs",
            reason="Cancellation does not establish that prior writes were undone.",
        )
    if status.skipped:
        return RunExplanationAction(
            kind="logs" if status.logs_available else "review_configuration",
            label="Review why the work was skipped",
            reason="The result records a skip but does not include its reason.",
        )
    return RunExplanationAction(
        kind="inspect_outputs",
        label="Review the pipeline outputs",
        reason="Confirm that the recorded output matches the result you expected.",
    )
