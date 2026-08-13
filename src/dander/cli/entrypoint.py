"""Lightweight console dispatcher that keeps hosted Control startup provider-free."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def dispatch(arguments: Sequence[str]) -> None:
    """Load only the command tree selected by the first console argument."""
    args = list(arguments)
    if args[:1] == ["control"]:
        import typer

        from dander.cli.control_command import control_app

        hosted_app = typer.Typer()
        hosted_app.add_typer(control_app, name="control")
        hosted_app(args=args, prog_name="dander")
        return

    from dander.cli.main import app

    app(args=args, prog_name="dander")


def main() -> None:
    """Dispatch the installed ``dander`` console script."""
    dispatch(sys.argv[1:])


__all__ = ["dispatch", "main"]
