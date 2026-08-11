"""OCI runtime command composition and terminal JSON output."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import dander.cli.main as main_module
from dander.cli.main import app
from dander.executor import PipelineExecutionResult
from dander.runtime import EndpointRunResult, PipelineRunResult

if TYPE_CHECKING:
    from rich.console import Console

    from dander.cli.run_command import RunOptions


def test_runtime_uses_launcher_run_id_and_emits_terminal_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
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

    monkeypatch.setattr(main_module, "execute_run", execute)

    result = CliRunner().invoke(
        app,
        [
            "runtime",
            "greenhouse_jobs",
            "--project",
            "unit-project",
            "--run-id",
            "cloud-run:execution-42",
            "--batch-rows",
            "2500",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["contract"] == "io.dander.runtime/v1"
    assert payload["status"] == "succeeded"
    assert payload["run_id"] == "cloud-run:execution-42"
    assert payload["outputs"]["metrics"]["extracted_rows"] == 4
    options = captured["options"]
    assert options.pipeline_or_source == "greenhouse_jobs"  # type: ignore[union-attr]
    assert options.project == "unit-project"  # type: ignore[union-attr]
    assert options.batch_rows == 2500  # type: ignore[union-attr]
    assert captured["render"] is False


def test_runtime_failure_is_machine_readable_and_nonzero(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("credential contents must not enter the terminal record")

    monkeypatch.setattr(main_module, "execute_run", fail)

    result = CliRunner().invoke(
        app,
        ["runtime", "greenhouse_jobs", "--run-id", "cloud-run:failed-42"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert payload["run_id"] == "cloud-run:failed-42"
    assert payload["error_code"] == "runtime_failed"
    assert "credential" not in result.output
