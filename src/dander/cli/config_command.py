"""Project configuration migration commands."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import typer
from click import ClickException

from dander.project import ProjectConfigError
from dander.project.portable_config import ProjectMigration, prepare_version_one_migration

config_app = typer.Typer(
    help="Validate and migrate versioned project configuration.",
    no_args_is_help=True,
)


@config_app.command("migrate")
def migrate_config(
    project_config: Path = typer.Option(Path("dander.yaml"), "--config"),  # noqa: B008
    platforms_config: Path | None = typer.Option(  # noqa: B008
        None,
        "--platforms-config",
        help="Output path (defaults beside dander.yaml).",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Prove deterministic compatibility without writing files.",
    ),
) -> None:
    """Split a version 1 project into v2 logical and deployment files."""
    resolved_platforms = platforms_config or project_config.with_name("dander.platforms.yaml")
    try:
        migration = prepare_version_one_migration(project_config)
        if resolved_platforms.exists() or resolved_platforms.is_symlink():
            raise ProjectConfigError(
                f"Refusing to overwrite platform configuration: {resolved_platforms}"
            )
        if check:
            typer.echo(
                "Migration check passed: version 2 resolves to the same GCP/Cloud Run behavior; "
                "no files changed."
            )
            return
        _write_migration(project_config, resolved_platforms, migration)
    except ProjectConfigError as error:
        raise ClickException(str(error)) from error
    except OSError as error:
        raise ClickException("Could not write migrated project configuration") from error
    typer.echo(f"Migrated logical project: {project_config}")
    typer.echo(f"Created platform configuration: {resolved_platforms}")
    typer.echo(
        f"Next: dander validate --config {project_config} --platforms-config {resolved_platforms}"
    )


def _write_migration(
    project_config: Path,
    platforms_config: Path,
    migration: ProjectMigration,
) -> None:
    if hashlib.sha256(project_config.read_bytes()).hexdigest() != migration.source_sha256:
        raise ProjectConfigError("dander.yaml changed during migration; no files were written")
    project_config.parent.mkdir(parents=True, exist_ok=True)
    platforms_config.parent.mkdir(parents=True, exist_ok=True)
    logical_temp = _write_temporary(project_config, migration.logical_yaml)
    platforms_temp = _write_temporary(platforms_config, migration.platforms_yaml)
    try:
        if platforms_config.exists() or platforms_config.is_symlink():
            raise ProjectConfigError(
                f"Refusing to overwrite platform configuration: {platforms_config}"
            )
        platforms_temp.replace(platforms_config)
        logical_temp.replace(project_config)
    finally:
        logical_temp.unlink(missing_ok=True)
        platforms_temp.unlink(missing_ok=True)


def _write_temporary(target: Path, content: str) -> Path:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path
