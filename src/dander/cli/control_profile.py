"""Load Control command defaults from one operator-owned YAML profile.

The existing CLI options remain the type and validation boundary. Profiles select those
options; they do not introduce another startup model or copy provider binding semantics.
"""

from __future__ import annotations

from pathlib import Path

import typer
import yaml
from typer.core import TyperOption
from typer.models import TyperPath


def apply_control_profile(
    ctx: typer.Context, param: typer.CallbackParam, value: Path | None
) -> Path | None:
    """Apply profile defaults before ordinary options, preserving explicit CLI overrides."""
    if value is None or ctx.resilient_parsing:
        return value
    try:
        document = yaml.safe_load(value.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise typer.BadParameter("Cannot read a valid YAML Control profile.", param=param) from None
    if not isinstance(document, dict) or not all(isinstance(key, str) for key in document):
        raise typer.BadParameter("The Control profile must be an option mapping.", param=param)
    options = {
        name.removeprefix("--"): option
        for option in ctx.command.params
        if isinstance(option, TyperOption) and not option.is_eager
        for name in option.opts
        if name.startswith("--")
    }
    defaults = dict(ctx.default_map or {})
    for name, raw in document.items():
        option = options.get(name)
        if option is None or option.name is None:
            raise typer.BadParameter(
                "The Control profile contains an unknown option; use long option names "
                "without the leading --.",
                param=param,
            )
        if option.multiple and not isinstance(raw, list):
            raise typer.BadParameter(f"Profile option {name} must be a list.", param=param)
        if isinstance(option.type, TyperPath) and raw is not None:
            raw = (
                [_relative_path(item, value.parent, param) for item in raw]
                if option.multiple
                else _relative_path(raw, value.parent, param)
            )
        defaults[option.name] = raw
    ctx.default_map = defaults
    return value


def _relative_path(value: object, directory: Path, param: typer.CallbackParam) -> str:
    if not isinstance(value, str):
        raise typer.BadParameter("Profile file paths must be strings.", param=param)
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (directory / path).resolve())
