"""Separately named command for the hosted Dander Control API."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from click import ClickException

from dander.control import InMemoryGraphStore, RootedLocalGraphStore
from dander.control.application import ControlApplication
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
    oidc_config: Path | None = typer.Option(  # noqa: B008
        None,
        "--oidc-config",
        help="Non-secret hosted OIDC deployment JSON required for external binds.",
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
        if not _is_loopback(host) and oidc is None:
            raise ClickException(
                "External Control binds require a valid --oidc-config deployment input."
            )
        plugins: tuple[InstalledConnectorPlugin, ...] = ()
        if project_config.is_file():
            manifest = load_project_config(project_config)
            plugins = load_connector_plugins(manifest.plugins).plugins
        store = InMemoryGraphStore() if ephemeral else RootedLocalGraphStore(root)
        application = ControlApplication(
            store,
            connector_plugins=plugins,
            projects=tuple(projects or ("default",)),
        )
        from uvicorn import Config, Server

        from dander.control.http import create_control_app

        public_url = oidc.api_url if oidc is not None else f"http://{host}:{port}"
        storage = "ephemeral" if ephemeral else str(root.resolve())
        typer.echo(f"Serving Dander Control on {public_url} ({storage})")
        Server(
            Config(
                create_control_app(application, oidc=oidc),
                host=host,
                port=port,
                log_level="info",
                access_log=False,
            )
        ).run()
    except (ConnectorPluginError, ProjectConfigError, OSError, ValueError) as error:
        raise ClickException(str(error)) from error


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


__all__ = ["control_app"]
