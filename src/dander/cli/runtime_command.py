"""CLI implementation for the versioned OCI runtime boundary."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Annotated, cast

import typer
from click import ClickException
from google.cloud import bigquery
from rich.console import Console

from dander.cli.run_command import RunOptions, execute_run
from dander.compatibility import CompatibilityError, load_runtime_compatibility
from dander.identity import google_client_options, launcher_identity
from dander.identity.refresh_probe import (
    PROBE_SCHEMA,
    BigQueryProbeClient,
    ExpiringCredentials,
    GoogleRefreshProbeError,
    run_google_refresh_probe,
    validate_probe_target,
)
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
from dander.runtime_inspection import inspect_runtime, run_local_conformance
from dander.runtime_secrets import RuntimeSecretBindingError, projected_secret_environment
from dander.state import RunStage, classify_failure

runtime_app = typer.Typer(
    help="Execute and inspect Dander's launcher-neutral OCI runtime contract.",
    no_args_is_help=True,
)
_CONSOLE = Console()


@runtime_app.command("identity-refresh-probe", hidden=True)
def identity_refresh_probe_runtime_command(
    project: str = typer.Option(..., "--project"),
    dataset: str = typer.Option(..., "--dataset"),
    table: str = typer.Option(..., "--table"),
    max_wait_seconds: int = typer.Option(900, "--max-wait-seconds", min=1, max=1_800),
    refresh_margin_seconds: int = typer.Option(15, "--refresh-margin-seconds", min=0, max=60),
) -> None:
    """Prove one hosted launcher can renew keyless Google credentials in-process."""
    try:
        validate_probe_target(project=project, dataset=dataset, table=table)
        context = LauncherContext.from_environment()
        with launcher_identity(context):
            options = google_client_options()
            credentials = options.get("credentials")
            if credentials is None or not hasattr(credentials, "expiry"):
                raise GoogleRefreshProbeError(
                    "Selected launcher did not provide renewable Google credentials"
                )
            client = BigQueryProbeClient(bigquery.Client(project=project, **options))
            run_google_refresh_probe(
                credentials=cast("ExpiringCredentials", credentials),
                client=client,
                project=project,
                dataset=dataset,
                table=table,
                max_wait_seconds=max_wait_seconds,
                refresh_margin_seconds=refresh_margin_seconds,
                emit=lambda event: typer.echo(
                    json.dumps(event, sort_keys=True, separators=(",", ":"))
                ),
            )
    except (GoogleRefreshProbeError, RuntimeContractError) as error:
        typer.echo(
            json.dumps(
                {
                    "schema": PROBE_SCHEMA,
                    "event": "probe.failed",
                    "failure_code": "identity_or_query_failed",
                    "failure_type": type(error).__name__,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise typer.Exit(code=1) from None


@runtime_app.command("compatibility")
def compatibility_runtime_command() -> None:
    """Print the package-owned provider compatibility matrix without provider access."""
    try:
        compatibility = load_runtime_compatibility()
    except CompatibilityError as error:
        raise ClickException(str(error)) from error
    typer.echo(compatibility.to_json())


@runtime_app.command("inspect")
def inspect_runtime_command(
    project_config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
) -> None:
    """Report installed runtime, adapter, and plugin metadata without provider access."""
    try:
        inspection = inspect_runtime(project_config)
    except (RuntimeContractError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(inspection.to_json())


@runtime_app.command("conformance")
def conformance_runtime_command(
    work_dir: Annotated[
        Path | None,
        typer.Option("--work-dir", help="Directory in which the probe may write state.db."),
    ] = None,
) -> None:
    """Run a credential-free local executor, event, filesystem, and signal probe."""
    try:
        if work_dir is not None:
            result = run_local_conformance(work_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="dander-runtime-conformance-") as directory:
                result = run_local_conformance(Path(directory))
    except RuntimeContractError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(result.to_json())


@runtime_app.command("execute")
def execute_runtime(
    contract: str = typer.Option(..., "--contract", help=f"Must be {RUNTIME_CONTRACT}."),
    pipeline: str = typer.Option(..., "--pipeline", help="Pipeline ID from dander.yaml."),
    platform: str = typer.Option(
        ...,
        "--platform",
        help="Named deployment/profile selected for this runtime execution.",
    ),
    project_config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    platforms_config: Path | None = typer.Option(None, "--platforms-config"),  # noqa: B008
    connectors_dir: Path = typer.Option(Path("connectors"), "--connectors-dir"),  # noqa: B008
    models_dir: Path = typer.Option(Path("models"), "--models-dir"),  # noqa: B008
    catalog_output: Path | None = typer.Option(None, "--catalog-output"),  # noqa: B008
    project: str | None = typer.Option(
        None,
        "--project",
        help="Override the BigQuery/GCP project for profiles that use GCP.",
    ),
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        help="Override the raw warehouse dataset or schema.",
    ),
    guarded_free_tier: bool = typer.Option(False, "--guarded-free-tier"),
    batch_rows: int = typer.Option(10_000, "--batch-rows", min=1, max=100_000),
    budget_name: str = typer.Option("dander-sbx-cap", "--budget-name", hidden=True),
) -> None:
    """Execute one pipeline and emit only versioned, non-sensitive JSON Lines."""
    try:
        validate_runtime_contract(contract)
        validate_runtime_identifier(pipeline, label="pipeline id")
        validate_runtime_identifier(platform, label="platform")
        context = LauncherContext.from_environment()
    except RuntimeContractError as error:
        raise typer.BadParameter(str(error)) from error

    started_ns = time.monotonic_ns()
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
        platforms_config=platforms_config,
        deployment=platform,
        dry_run=False,
        sandbox=False,
        guarded_free_tier=guarded_free_tier,
        batch_rows=batch_rows,
        budget_name=budget_name,
        state_path=Path(".dander/state.db"),
        build_models=False,
        models_dir=models_dir,
        selected_models=None,
        catalog_output=catalog_output,
        publish_dataplex=False,
        dataplex_location="us",
    )
    try:
        with (
            graceful_signal_handlers(),
            launcher_identity(context),
            projected_secret_environment(),
        ):
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
                duration_ms=_elapsed_ms(started_ns),
            ).to_json()
        )
        raise typer.Exit(code=RuntimeExitCode.CANCELLED) from None
    except (ClickException, RuntimeSecretBindingError) as error:
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
                    duration_ms=_elapsed_ms(started_ns),
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
                duration_ms=_elapsed_ms(started_ns),
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
                duration_ms=_elapsed_ms(started_ns),
            ).to_json()
        )
        code = RuntimeExitCode.RETRYABLE_FAILURE if retryable else RuntimeExitCode.PERMANENT_FAILURE
        raise typer.Exit(code=code) from None

    assert result is not None
    typer.echo(
        RuntimeEvent.completed(
            result,
            context=context,
            platform=platform,
            duration_ms=_elapsed_ms(started_ns),
        ).to_json()
    )


def _elapsed_ms(started_ns: int) -> int:
    return max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
