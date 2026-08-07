"""Connector-plugin installation command behavior."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import dander.cli.main as cli_module
from dander import __version__
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
            f"dander-platform=={__version__}",
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


def test_plugins_scaffold_creates_named_project(tmp_path: Path) -> None:
    destination = tmp_path / "acme"

    result = CliRunner().invoke(
        app,
        [
            "plugins",
            "scaffold",
            "acme_crm",
            "--display-name",
            "Acme CRM",
            "--directory",
            str(destination),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Created connector plugin" in result.output
    assert (destination / "src" / "dander_connector_acme_crm" / "plugin.py").is_file()


def test_plugins_scaffold_reports_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()

    result = CliRunner().invoke(
        app,
        ["plugins", "scaffold", "example", "--directory", str(destination)],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "Destination already exists" in str(result.exception)


def test_plugins_search_lists_curated_exact_package_pins() -> None:
    result = CliRunner().invoke(app, ["plugins", "search"])

    assert result.exit_code == 0, result.output
    assert "dander-connector-salesforce==0.3.1rc1" in result.output
    assert "dander-connector-servicenow==0.2.2rc1" in result.output


def test_plugins_search_filters_and_reports_no_match() -> None:
    filtered = CliRunner().invoke(app, ["plugins", "search", "incident"])
    missing = CliRunner().invoke(app, ["plugins", "search", "not-a-connector"])

    assert filtered.exit_code == 0, filtered.output
    assert "dander-connector-servicenow==0.2.2rc1" in filtered.output
    assert "dander-connector-salesforce" not in filtered.output
    assert missing.exit_code == 0, missing.output
    assert "No curated connectors match" in missing.output
