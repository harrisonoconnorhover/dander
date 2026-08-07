"""OCI runtime invocation, output, and exit-code contract."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from click import ClickException
from typer.testing import CliRunner

import dander.cli.runtime_command as runtime_module
from dander.cli.main import app
from dander.executor import PipelineExecutionResult
from dander.runtime import EndpointRunResult, PipelineRunResult
from dander.runtime_contract import RuntimeCancelledError, RuntimeExitCode

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest import MonkeyPatch
    from rich.console import Console
    from typer.testing import Result

    from dander.cli.run_command import RunOptions


def _invoke(
    monkeypatch: MonkeyPatch,
    execute: Callable[..., PipelineExecutionResult | None],
) -> Result:
    monkeypatch.setattr(runtime_module, "execute_run", execute)
    return CliRunner().invoke(
        app,
        [
            "runtime",
            "execute",
            "--contract",
            "io.dander.runtime/v1",
            "--pipeline",
            "greenhouse_jobs",
            "--platform",
            "gcp",
            "--project",
            "unit-project",
            "--batch-rows",
            "2500",
        ],
        env={
            "DANDER_RUN_ID": "cloud-run:execution-42",
            "DANDER_LAUNCHER": "cloud_run",
        },
    )


def test_runtime_execute_uses_launcher_run_id_and_emits_json_lines(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def execute(
        options: RunOptions,
        *,
        console: Console,
        run_id: str | None = None,
        render: bool = True,
    ) -> PipelineExecutionResult:
        captured.update(options=options, console=console, run_id=run_id, render=render)
        assert run_id is not None
        return PipelineExecutionResult(
            run_id=run_id,
            pipeline_id=options.pipeline_or_source,
            ingestion=PipelineRunResult(
                run_id=run_id,
                source="greenhouse_job_board",
                endpoints=(
                    EndpointRunResult(
                        endpoint="jobs",
                        extracted=4,
                        affected=4,
                        committed_cursor=None,
                    ),
                ),
            ),
            models=("stg_greenhouse__jobs",),
            assertions=1,
            assets=1,
        )

    result = _invoke(monkeypatch, execute)

    assert result.exit_code == RuntimeExitCode.SUCCESS, result.output
    events = [json.loads(line) for line in result.output.splitlines()]
    assert [event["event"] for event in events] == ["runtime.started", "runtime.completed"]
    assert events[-1]["run_id"] == "cloud-run:execution-42"
    assert events[-1]["outputs"]["metrics"]["extracted_rows"] == 4
    options = cast("RunOptions", captured["options"])
    assert options.pipeline_or_source == "greenhouse_jobs"
    assert options.project == "unit-project"
    assert options.batch_rows == 2500
    assert captured["render"] is False


def test_runtime_execute_sanitizes_retryable_failure(monkeypatch: MonkeyPatch) -> None:
    class _Response:
        status_code = 429

    class HttpFailureError(RuntimeError):
        response = _Response()

    def fail(*args: object, **kwargs: object) -> None:
        raise HttpFailureError("credential-value")

    result = _invoke(monkeypatch, fail)

    assert result.exit_code == RuntimeExitCode.RETRYABLE_FAILURE
    terminal = json.loads(result.output.splitlines()[-1])
    assert terminal["failure_code"] == "rate_limited"
    assert terminal["retryable"] is True
    assert "credential-value" not in result.output


def test_runtime_execute_distinguishes_invalid_configuration(
    monkeypatch: MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise ClickException("private configuration detail")

    result = _invoke(monkeypatch, fail)

    assert result.exit_code == RuntimeExitCode.INVALID_INVOCATION
    terminal = json.loads(result.output.splitlines()[-1])
    assert terminal["failure_code"] == "invalid_configuration"
    assert "private configuration detail" not in result.output


def test_runtime_execute_preserves_wrapped_execution_failure_classification(
    monkeypatch: MonkeyPatch,
) -> None:
    class TransformRunError(RuntimeError):
        pass

    def fail(*args: object, **kwargs: object) -> None:
        error = ClickException("private transform detail")
        error.__cause__ = TransformRunError("source row must not escape")
        raise error

    result = _invoke(monkeypatch, fail)

    assert result.exit_code == RuntimeExitCode.PERMANENT_FAILURE
    terminal = json.loads(result.output.splitlines()[-1])
    assert terminal["failure_code"] == "transform_failed"
    assert "private transform detail" not in result.output
    assert "source row" not in result.output


def test_runtime_execute_reports_graceful_cancellation(monkeypatch: MonkeyPatch) -> None:
    def cancel(*args: object, **kwargs: object) -> None:
        raise RuntimeCancelledError("SIGTERM")

    result = _invoke(monkeypatch, cancel)

    assert result.exit_code == RuntimeExitCode.CANCELLED
    terminal = json.loads(result.output.splitlines()[-1])
    assert terminal["failure_code"] == "interrupted_run"
    assert terminal["stage"] == "cancelled"


def test_runtime_execute_rejects_unknown_contract_before_start() -> None:
    result = CliRunner().invoke(
        app,
        [
            "runtime",
            "execute",
            "--contract",
            "io.dander.runtime/v2",
            "--pipeline",
            "greenhouse_jobs",
            "--platform",
            "gcp",
        ],
    )

    assert result.exit_code == RuntimeExitCode.INVALID_INVOCATION
    assert "unsupported runtime contract" in result.output
    assert "runtime.started" not in result.output
