"""Connector-plugin installation command behavior."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import dander.cli.main as cli_module
from dander.cli.main import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_manifest(path: Path, *, plugins: str = "") -> None:
    path.write_text(
        f"""
version: 1
{plugins}
pipelines:
  example:
    source: example
    models: [example]
""".strip(),
        encoding="utf-8",
    )


def test_plugins_install_uses_exact_manifest_pins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "dander.yaml"
    _write_manifest(
        manifest,
        plugins="""plugins:
  salesforce:
    distribution: dander-connector-salesforce
    version: 0.1.0""",
    )
    captured: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        captured.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(cli_module.subprocess, "run", run)
    monkeypatch.setattr(cli_module, "load_connector_plugins", lambda _: None)

    result = CliRunner().invoke(app, ["plugins", "install", "--config", str(manifest)])

    assert result.exit_code == 0, result.output
    assert captured == [
        (
            sys.executable,
            "-m",
            "pip",
            "install",
            "dander-connector-salesforce==0.1.0",
        )
    ]


def test_plugins_install_is_a_noop_without_declarations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "dander.yaml"
    _write_manifest(manifest)

    def unexpected_run(*args: object, **kwargs: object) -> None:
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(cli_module.subprocess, "run", unexpected_run)

    result = CliRunner().invoke(app, ["plugins", "install", "--config", str(manifest)])

    assert result.exit_code == 0, result.output
    assert "No connector plugins are declared" in result.output
