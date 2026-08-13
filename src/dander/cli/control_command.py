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
) -> None:
    """Serve multi-graph Control locally; external binds require the later OIDC ticket."""
    try:
        if not _is_loopback(host):
            raise ClickException(
                "Hosted Control may bind only to loopback until OIDC authorization is enabled."
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

        typer.echo(
            f"Serving Dander Control on http://{host}:{port} "
            f"({'ephemeral' if ephemeral else str(root.resolve())})"
        )
        Server(
            Config(create_control_app(application), host=host, port=port, log_level="info")
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
