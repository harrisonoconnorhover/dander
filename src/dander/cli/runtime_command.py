"""CLI implementation for the versioned OCI runtime boundary."""

from __future__ import annotations

from pathlib import Path

import typer
from click import ClickException
from rich.console import Console

from dander.cli.run_command import RunOptions, execute_run
from dander.runtime_contract import (
    RUNTIME_CONTRACT,
    LauncherContext,
    RuntimeCancelledError,
    RuntimeContractError,
    RuntimeEvent,
    RuntimeExitCode,
    graceful_signal_handlers,
    is_retryable_failure,
    validate_runtime_contract,
    validate_runtime_identifier,
)
from dander.state import RunStage, classify_failure

runtime_app = typer.Typer(
    help="Execute and inspect Dander's launcher-neutral OCI runtime contract.",
    no_args_is_help=True,
)
_CONSOLE = Console()


@runtime_app.command("execute")
def execute_runtime(
    contract: str = typer.Option(..., "--contract", help=f"Must be {RUNTIME_CONTRACT}."),
    pipeline: str = typer.Option(..., "--pipeline", help="Pipeline ID from dander.yaml."),
    platform: str = typer.Option(
        ...,
        "--platform",
        help="Compatibility profile; Phase 1 supports the existing 'gcp' profile.",
    ),
    project_config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    connectors_dir: Path = typer.Option(Path("connectors"), "--connectors-dir"),  # noqa: B008
    models_dir: Path = typer.Option(Path("models"), "--models-dir"),  # noqa: B008
    project: str | None = typer.Option(None, "--project", help="Override GCP_PROJECT_ID."),
    dataset: str | None = typer.Option(None, "--dataset", help="Override BQ_DATASET_RAW."),
    guarded_free_tier: bool = typer.Option(False, "--guarded-free-tier"),
    batch_rows: int = typer.Option(10_000, "--batch-rows", min=1, max=100_000),
    budget_name: str = typer.Option("dander-sbx-cap", "--budget-name", hidden=True),
) -> None:
    """Execute one pipeline and emit only versioned, non-sensitive JSON Lines."""
    try:
        validate_runtime_contract(contract)
        validate_runtime_identifier(pipeline, label="pipeline id")
        validate_runtime_identifier(platform, label="platform")
        if platform != "gcp":
            raise RuntimeContractError(
                "runtime contract v1 currently supports only the 'gcp' compatibility profile"
            )
        context = LauncherContext.from_environment()
    except RuntimeContractError as error:
        raise typer.BadParameter(str(error)) from error

    typer.echo(
        RuntimeEvent.started(
            context=context,
            pipeline_id=pipeline,
            platform=platform,
        ).to_json()
    )
    options = RunOptions(
        pipeline_or_source=pipeline,
        project=project,
        dataset=dataset,
        connectors_dir=connectors_dir,
        project_config=project_config,
        dry_run=False,
        sandbox=False,
        guarded_free_tier=guarded_free_tier,
        batch_rows=batch_rows,
        budget_name=budget_name,
        state_path=Path(".dander/state.db"),
        build_models=False,
        models_dir=models_dir,
        selected_models=None,
        catalog_output=None,
        publish_dataplex=False,
        dataplex_location="us",
    )
    try:
        with graceful_signal_handlers():
            result = execute_run(
                options,
                console=_CONSOLE,
                run_id=context.run_id,
                render=False,
            )
    except RuntimeCancelledError:
        typer.echo(
            RuntimeEvent.failed(
                context=context,
                pipeline_id=pipeline,
                platform=platform,
                stage="cancelled",
                failure_code="interrupted_run",
                retryable=True,
            ).to_json()
        )
        raise typer.Exit(code=RuntimeExitCode.CANCELLED) from None
    except ClickException as error:
        failure = classify_failure(error, stage=RunStage.INGEST, run_id=context.run_id)
        if failure.code != "unexpected_error":
            retryable = is_retryable_failure(failure.code)
            typer.echo(
                RuntimeEvent.failed(
                    context=context,
                    pipeline_id=pipeline,
                    platform=platform,
                    stage="runtime",
                    failure_code=failure.code,
                    retryable=retryable,
                ).to_json()
            )
            code = (
                RuntimeExitCode.RETRYABLE_FAILURE
                if retryable
                else RuntimeExitCode.PERMANENT_FAILURE
            )
            raise typer.Exit(code=code) from None
        typer.echo(
            RuntimeEvent.failed(
                context=context,
                pipeline_id=pipeline,
                platform=platform,
                stage="configuration",
                failure_code="invalid_configuration",
                retryable=False,
            ).to_json()
        )
        raise typer.Exit(code=RuntimeExitCode.INVALID_INVOCATION) from None
    except Exception as error:
        failure = classify_failure(error, stage=RunStage.INGEST, run_id=context.run_id)
        retryable = is_retryable_failure(failure.code)
        typer.echo(
            RuntimeEvent.failed(
                context=context,
                pipeline_id=pipeline,
                platform=platform,
                stage="runtime",
                failure_code=failure.code,
                retryable=retryable,
            ).to_json()
        )
        code = RuntimeExitCode.RETRYABLE_FAILURE if retryable else RuntimeExitCode.PERMANENT_FAILURE
        raise typer.Exit(code=code) from None

    assert result is not None
    typer.echo(RuntimeEvent.completed(result, context=context, platform=platform).to_json())
