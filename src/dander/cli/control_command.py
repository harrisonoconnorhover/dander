"""Separately named command for the hosted Dander Control API."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from click import ClickException

from dander.control import InMemoryGraphStore, RootedLocalGraphStore
from dander.control.application import ControlApplication, ControlOperationError
from dander.plugins import ConnectorPluginError, load_connector_plugins
from dander.project import ProjectConfigError, load_project_config

if TYPE_CHECKING:
    from dander.plugins import InstalledConnectorPlugin

control_app = typer.Typer(help="Run Dander's multi-graph Control API.")


@control_app.command("serve")
def serve_control(
    root: Path = typer.Option(  # noqa: B008
        Path(".dander/control"),
        "--root",
        help="Private root used by the durable local graph store.",
    ),
    ephemeral: bool = typer.Option(
        False,
        "--ephemeral",
        help="Use an explicitly non-durable in-memory graph store.",
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8770, "--port", min=1, max=65535),
    projects: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--project",
        help="Logical project exposed by hosted routes; repeat for multiple projects.",
    ),
    project_config: Path = typer.Option(  # noqa: B008
        Path("dander.yaml"),
        "--config",
        help="Optional project manifest used only for presentation-safe plugin discovery.",
    ),
    platforms_config: Path | None = typer.Option(  # noqa: B008
        None,
        "--platforms-config",
        help="Operator-owned platform manifest used by hosted execution backends.",
    ),
    oidc_config: Path | None = typer.Option(  # noqa: B008
        None,
        "--oidc-config",
        help="Non-secret hosted OIDC deployment JSON required for external binds.",
    ),
    graph_store_config: Path | None = typer.Option(  # noqa: B008
        None,
        "--graph-store-config",
        help="Credential-free typed GraphStore locator JSON for hosted persistence.",
    ),
    execution_plans: list[Path] | None = typer.Option(  # noqa: B008
        None,
        "--execution-plan",
        help="Canonical hosted execution-plan JSON; repeat for each active graph.",
    ),
    trigger_specs: list[Path] | None = typer.Option(  # noqa: B008
        None,
        "--trigger-spec",
        help="Canonical scheduled TriggerSpec JSON; repeat for each Control-owned schedule.",
    ),
    schedule_queue_url: str | None = typer.Option(
        None,
        "--schedule-queue-url",
        help="Encrypted standard SQS queue receiving scheduled Control wakeups.",
    ),
    run_store_bucket: str | None = typer.Option(
        None,
        "--run-store-bucket",
        help="S3 bucket for durable hosted run snapshots and attempt history.",
    ),
    run_store_prefix: str = typer.Option(
        "dander-control/v1",
        "--run-store-prefix",
        help="Object prefix for durable hosted run state.",
    ),
    run_environment: str = typer.Option(
        "production",
        "--run-environment",
        help="Execution-plan environment selected by the compatibility run route.",
    ),
    reconcile_interval_seconds: float = typer.Option(
        5.0,
        "--reconcile-interval-seconds",
        min=0.1,
        max=300.0,
        help="Background durable-run reconciliation interval.",
    ),
    shutdown_grace_seconds: float = typer.Option(
        35.0,
        "--shutdown-grace-seconds",
        min=1.0,
        max=300.0,
        help="Maximum wait for graceful reconciler shutdown.",
    ),
    aws_deployment_name: str = typer.Option(
        "dander",
        "--aws-deployment-name",
        help="Existing Fargate resource-name prefix used by the hosted backend.",
    ),
) -> None:
    """Serve multi-graph Control locally or behind an approved hosted OIDC deployment."""
    try:
        from dander.control.auth import HostedOIDCDeploymentInput

        oidc = (
            HostedOIDCDeploymentInput.model_validate_json(oidc_config.read_text(encoding="utf-8"))
            if oidc_config is not None
            else None
        )
        from dander.deployment.service import graph_store_binding_from_json

        graph_store_binding = (
            graph_store_binding_from_json(graph_store_config.read_text(encoding="utf-8"))
            if graph_store_config is not None
            else None
        )
        if ephemeral and graph_store_binding is not None:
            raise ClickException("--ephemeral and --graph-store-config are mutually exclusive.")
        if bool(execution_plans) != (run_store_bucket is not None):
            raise ClickException(
                "--execution-plan and --run-store-bucket must be configured together."
            )
        if bool(trigger_specs) != (schedule_queue_url is not None):
            raise ClickException(
                "--trigger-spec and --schedule-queue-url must be configured together."
            )
        if trigger_specs and not execution_plans:
            raise ClickException("Scheduled triggers require --execution-plan configuration.")
        if not _is_loopback(host) and oidc is None:
            raise ClickException(
                "External Control binds require a valid --oidc-config deployment input."
            )
        plugins: tuple[InstalledConnectorPlugin, ...] = ()
        if project_config.is_file():
            manifest = load_project_config(project_config)
            plugins = load_connector_plugins(manifest.plugins).plugins
        if graph_store_binding is not None:
            from dander.control.graph_store_factory import build_bound_graph_store

            store = build_bound_graph_store(graph_store_binding)
        else:
            store = InMemoryGraphStore() if ephemeral else RootedLocalGraphStore(root)
        selected_projects = tuple(projects or ("default",))
        if execution_plans:
            assert run_store_bucket is not None
            from dander.control.run_composition import build_fargate_run_composition

            run_composition = build_fargate_run_composition(
                graph_store=store,
                project_config=project_config,
                platforms_config=platforms_config,
                plan_paths=execution_plans,
                run_store_bucket=run_store_bucket,
                run_store_prefix=run_store_prefix,
                environment=run_environment,
                deployment_name=aws_deployment_name,
                reconcile_interval_seconds=reconcile_interval_seconds,
                shutdown_grace_seconds=shutdown_grace_seconds,
                trigger_paths=trigger_specs or (),
                schedule_queue_url=schedule_queue_url,
            )
            try:
                application = ControlApplication(
                    store,
                    lifecycle=run_composition.lifecycle,
                    submission_resolver=run_composition.resolver,
                    connector_plugins=plugins,
                    projects=selected_projects,
                    readiness=run_composition.lifecycle.ready,
                )
            except Exception:  # noqa: BLE001 - close the started reconciler before re-raising
                run_composition.lifecycle.close()
                raise
        else:
            application = ControlApplication(
                store,
                connector_plugins=plugins,
                projects=selected_projects,
            )
        from uvicorn import Config, Server

        from dander.control.http import create_control_app

        public_url = oidc.api_url if oidc is not None else f"http://{host}:{port}"
        storage = (
            graph_store_binding.kind
            if graph_store_binding is not None
            else "ephemeral"
            if ephemeral
            else str(root.resolve())
        )
        typer.echo(f"Serving Dander Control on {public_url} ({storage})")
        try:
            Server(
                Config(
                    create_control_app(application, oidc=oidc),
                    host=host,
                    port=port,
                    log_level="info",
                    access_log=False,
                )
            ).run()
        finally:
            application.close()
    except (
        ConnectorPluginError,
        ControlOperationError,
        ProjectConfigError,
        OSError,
        ValueError,
    ) as error:
        raise ClickException(str(error)) from error


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


__all__ = ["control_app"]
