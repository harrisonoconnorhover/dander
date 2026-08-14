"""OCI runtime invocation, output, and exit-code contract."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from click import ClickException
from typer.testing import CliRunner

import dander.cli.runtime_command as runtime_module
from dander.cli.main import app
from dander.executor import PipelineExecutionResult
from dander.identity import FargateIdentityError
from dander.runtime import EndpointRunResult, PipelineRunResult
from dander.runtime_contract import RuntimeCancelledError, RuntimeExitCode
from dander.state import mark_failure_diagnostic_logged

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pytest import LogCaptureFixture, MonkeyPatch
    from rich.console import Console
    from typer.testing import Result

    from dander.cli.run_command import RunOptions


def _invoke(
    monkeypatch: MonkeyPatch,
    execute: Callable[..., PipelineExecutionResult | None],
    *,
    attempt: int = 1,
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
            "--catalog-output",
            "/tmp/dander-catalog.json",
        ],
        env={
            "DANDER_RUN_ID": "cloud-run:execution-42",
            "DANDER_LAUNCHER": "cloud_run",
            "DANDER_ATTEMPT": str(attempt),
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
        retry: bool = False,
        render: bool = True,
    ) -> PipelineExecutionResult:
        captured.update(
            options=options,
            console=console,
            run_id=run_id,
            retry=retry,
            render=render,
        )
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
    assert events[-1]["outputs"]["telemetry"]["duration_ms"] >= 0
    options = cast("RunOptions", captured["options"])
    assert options.pipeline_or_source == "greenhouse_jobs"
    assert options.project == "unit-project"
    assert options.deployment == "gcp"
    assert options.batch_rows == 2500
    assert str(options.catalog_output) == "/tmp/dander-catalog.json"
    assert captured["render"] is False
    assert captured["retry"] is False


def test_runtime_execute_marks_later_launcher_attempt_as_retry(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def execute(options: RunOptions, **kwargs: object) -> PipelineExecutionResult:
        captured.update(kwargs)
        run_id = cast("str", kwargs["run_id"])
        return PipelineExecutionResult(
            run_id=run_id,
            pipeline_id=options.pipeline_or_source,
            ingestion=PipelineRunResult(run_id=run_id, source="fixture", endpoints=()),
            models=(),
            assertions=0,
            assets=0,
        )

    result = _invoke(monkeypatch, execute, attempt=2)

    assert result.exit_code == RuntimeExitCode.SUCCESS, result.output
    assert captured["run_id"] == "cloud-run:execution-42"
    assert captured["retry"] is True


def test_runtime_execute_selects_named_version_two_deployment(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def execute(options: RunOptions, **_: object) -> PipelineExecutionResult:
        captured["options"] = options
        return PipelineExecutionResult(
            run_id="postgres-run",
            pipeline_id=options.pipeline_or_source,
            ingestion=PipelineRunResult(
                run_id="postgres-run",
                source="fixture",
                endpoints=(),
            ),
            models=(),
            assertions=0,
            assets=0,
        )

    monkeypatch.setattr(runtime_module, "execute_run", execute)
    result = CliRunner().invoke(
        app,
        [
            "runtime",
            "execute",
            "--contract",
            "io.dander.runtime/v1",
            "--pipeline",
            "postgresql_fixture",
            "--platform",
            "postgres_fargate",
            "--platforms-config",
            "/app/dander.platforms.yaml",
        ],
    )

    assert result.exit_code == RuntimeExitCode.SUCCESS, result.output
    options = cast("RunOptions", captured["options"])
    assert options.deployment == "postgres_fargate"
    assert str(options.platforms_config) == "/app/dander.platforms.yaml"


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
    assert terminal["outputs"]["telemetry"]["duration_ms"] >= 0
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


def test_runtime_execute_sanitizes_fargate_identity_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    @contextmanager
    def identity_failure(_context: object) -> Iterator[None]:
        raise FargateIdentityError("temporary credential detail")
        yield

    monkeypatch.setattr(runtime_module, "launcher_identity", identity_failure)
    result = CliRunner().invoke(
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
        ],
        env={"DANDER_RUN_ID": "fargate:task-42", "DANDER_LAUNCHER": "fargate"},
    )

    assert result.exit_code == RuntimeExitCode.PERMANENT_FAILURE
    terminal = json.loads(result.output.splitlines()[-1])
    assert terminal["failure_code"] == "authentication_failed"
    assert "temporary credential detail" not in result.output


def test_runtime_execute_logs_diagnostic_for_pre_executor_sdk_failure(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    class ClientError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("secret-value-must-not-escape")
            self.response = {"ResponseMetadata": {"HTTPStatusCode": 403}}

    @contextmanager
    def secret_failure() -> Iterator[None]:
        raise ClientError
        yield

    monkeypatch.setattr(runtime_module, "projected_secret_environment", secret_failure)
    with caplog.at_level(logging.WARNING, logger=runtime_module.__name__):
        result = _invoke(monkeypatch, lambda *_args, **_kwargs: None)

    assert result.exit_code == RuntimeExitCode.PERMANENT_FAILURE
    record = next(
        item for item in caplog.records if getattr(item, "dander_event", None) == "pipeline_failed"
    )
    diagnostic = json.loads(record.message)
    assert diagnostic == {
        "duration_ms": diagnostic["duration_ms"],
        "event": "pipeline_failed",
        "exception_class_chain": ["ClientError"],
        "failure_code": "permission_denied",
        "pipeline_id": "greenhouse_jobs",
        "run_id": "cloud-run:execution-42",
        "stage": "runtime",
        "status_code": 403,
    }
    assert "secret-value-must-not-escape" not in record.message
    assert "secret-value-must-not-escape" not in result.output


def test_runtime_execute_does_not_duplicate_executor_diagnostic(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        error = RuntimeError("private-executor-detail")
        logging.getLogger("dander.executor").warning(
            '{"event":"pipeline_failed","stage":"transform"}',
            extra={"dander_event": "pipeline_failed"},
        )
        mark_failure_diagnostic_logged(error)
        raise error

    with caplog.at_level(logging.WARNING):
        result = _invoke(monkeypatch, fail)

    assert result.exit_code == RuntimeExitCode.PERMANENT_FAILURE
    diagnostics = [
        record
        for record in caplog.records
        if getattr(record, "dander_event", None) == "pipeline_failed"
    ]
    assert len(diagnostics) == 1
    assert json.loads(diagnostics[0].message)["stage"] == "transform"
    assert "private-executor-detail" not in result.output
