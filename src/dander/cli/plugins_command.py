"""Connector plugin commands independent of deployment and runtime setup."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import typer
from click import ClickException
from rich.console import Console
from rich.table import Table

from dander import __version__
from dander.plugins import (
    ConnectorPluginError,
    PluginScaffoldError,
    load_connector_plugins,
    scaffold_connector_plugin,
    search_connector_catalog,
)

plugins_app = typer.Typer(help="Install and inspect explicitly pinned connector plugins.")
console = Console()
_DEFAULT_PROJECT_CONFIG = Path("dander.yaml")


@plugins_app.command("install")
def install_plugins(
    project_config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    platforms_config: Path | None = typer.Option(None, "--platforms-config"),  # noqa: B008
    deployment: str | None = typer.Option(None, "--deployment"),
) -> None:
    """Install the manifest's exact connector-plugin package pins."""
    from dander.project import ProjectConfigError, load_project_plugins

    try:
        plugins = load_project_plugins(project_config)
    except ProjectConfigError as error:
        raise ClickException(str(error)) from error
    requirements = [
        f"{plugin.distribution}=={plugin.version}" for _, plugin in sorted(plugins.items())
    ]
    if not requirements:
        console.print("No connector plugins are declared in dander.yaml.")
        return
    plugin_count = len(requirements)
    # Keep the package running this command in the resolver transaction. Plugin
    # compatibility constraints must fail clearly instead of silently replacing
    # Dander with an older release inside a source-free runtime image.
    requirements.append(f"dander-platform=={__version__}")
    uv_executable = shutil.which("uv")
    command = (
        (uv_executable, "pip", "install", "--python", sys.executable, *requirements)
        if uv_executable is not None
        else (sys.executable, "-m", "pip", "install", *requirements)
    )
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            check=False,
        )
    except OSError as error:
        raise ClickException("Could not start the Python package installer") from error
    if completed.returncode != 0:
        raise ClickException("Connector plugin installation failed")
    try:
        load_connector_plugins(plugins)
    except ConnectorPluginError as error:
        raise ClickException(f"Installed connector plugins are incompatible: {error}") from error
    console.print(f"[green]Installed {plugin_count} connector plugin(s).[/green]")


@plugins_app.command("scaffold")
def scaffold_plugin(
    plugin_id: str = typer.Argument(  # noqa: B008
        ...,
        help="Lowercase connector identifier, for example acme_crm.",
    ),
    directory: Path | None = typer.Option(  # noqa: B008
        None,
        "--directory",
        help="New destination directory (defaults to dander-connector-<id>).",
    ),
    display_name: str | None = typer.Option(  # noqa: B008
        None,
        "--display-name",
        help="Human-readable connector name shown in Druff.",
    ),
) -> None:
    """Create a tested generic-REST connector plugin without overwriting a path."""
    destination = directory or Path(f"dander-connector-{plugin_id.replace('_', '-')}")
    try:
        created = scaffold_connector_plugin(
            plugin_id,
            destination,
            display_name=display_name,
        )
    except PluginScaffoldError as error:
        raise ClickException(str(error)) from error
    console.print(f"[green]Created connector plugin at {created}.[/green]")
    console.print(f"Next: cd {created} && uv sync --extra dev && uv run pytest")


@plugins_app.command("search")
def search_plugins(
    query: str = typer.Argument(  # noqa: B008
        "",
        help="Optional connector name, package, or capability to search for.",
    ),
) -> None:
    """Search Dander's small curated connector catalog."""
    connectors = search_connector_catalog(query)
    if not connectors:
        console.print(f"No curated connectors match {query!r}.")
        return

    table = Table(title="Dander connector catalog")
    table.add_column("Connector")
    table.add_column("Package pin")
    table.add_column("Dander")
    table.add_column("Support")
    table.add_column("Validation")
    for connector in connectors:
        table.add_row(
            connector.display_name,
            f"{connector.distribution}=={connector.version}",
            connector.dander_specifier,
            connector.support_status,
            connector.validation_status,
        )
    console.print(table)
    console.print("Exact package pins:")
    for connector in connectors:
        console.print(f"  {connector.distribution}=={connector.version}")
