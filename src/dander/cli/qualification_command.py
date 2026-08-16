"""Stable image-owned entrypoint for trusted qualification harnesses."""

from __future__ import annotations

import os
import sys
from pathlib import Path  # noqa: TC003 - Typer resolves command annotations at runtime.
from typing import TYPE_CHECKING, Annotated, Never

import typer
from click import ClickException

if TYPE_CHECKING:
    from collections.abc import Sequence


class QualificationRunnerError(RuntimeError):
    """Raised when a qualification harness cannot be started safely."""


def execute_qualification_script(script: Path, arguments: Sequence[str]) -> Never:
    """Replace the current process with a trusted qualification Python script.

    Args:
        script: Operator-mounted Python harness to execute.
        arguments: Arguments forwarded unchanged to the harness.

    Raises:
        QualificationRunnerError: If the harness is not a readable file or cannot be started.
    """
    try:
        if not script.is_file():
            raise QualificationRunnerError(f"Qualification script is not a file: {script}")
        with script.open("rb"):
            pass
    except OSError as error:
        raise QualificationRunnerError(f"Qualification script is not readable: {script}") from error

    argv = [sys.executable, os.fspath(script), *arguments]
    try:
        os.execv(sys.executable, argv)
    except OSError as error:
        raise QualificationRunnerError("Could not start the qualification script") from error


def qualification_run_command(
    context: typer.Context,
    script: Annotated[
        Path,
        typer.Argument(help="Trusted operator-mounted Python qualification harness."),
    ],
) -> None:
    """Run a qualification harness without depending on the image's Python path."""
    try:
        execute_qualification_script(script, context.args)
    except QualificationRunnerError as error:
        raise ClickException(str(error)) from error


def register_qualification_command(parent: typer.Typer) -> None:
    """Register the stable qualification runner on the root CLI."""
    parent.command(
        "qualification-run",
        context_settings={
            "allow_extra_args": True,
            "help_option_names": [],
            "ignore_unknown_options": True,
        },
    )(qualification_run_command)


__all__ = [
    "QualificationRunnerError",
    "execute_qualification_script",
    "qualification_run_command",
    "register_qualification_command",
]
