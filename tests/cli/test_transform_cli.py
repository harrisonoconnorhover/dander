"""CLI coverage for transform build and test command wiring."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from dander.cli.main import app
from dander.transform import TransformRunResult

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


class _FakeRunner:
    calls: list[tuple[str, Path, tuple[str, ...] | None]] = []

    def __init__(self, *, project: str) -> None:
        assert project == "valid-project-123"

    def build(
        self,
        models_dir: Path,
        *,
        selected: list[str] | None = None,
    ) -> TransformRunResult:
        self.calls.append(("build", models_dir, tuple(selected) if selected else None))
        return TransformRunResult(models=("selected_model",), assertions=2)

    def test(
        self,
        models_dir: Path,
        *,
        selected: list[str] | None = None,
    ) -> TransformRunResult:
        self.calls.append(("test", models_dir, tuple(selected) if selected else None))
        return TransformRunResult(models=("selected_model",), assertions=2)


def test_build_command_wires_selection_and_prints_summary(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _FakeRunner.calls.clear()
    monkeypatch.setattr("dander.cli.main.BigQueryTransformRunner", _FakeRunner)

    result = CliRunner().invoke(
        app,
        [
            "build",
            "--project",
            "valid-project-123",
            "--models-dir",
            str(tmp_path),
            "--select",
            "selected_model",
        ],
    )

    assert result.exit_code == 0
    assert _FakeRunner.calls == [("build", tmp_path, ("selected_model",))]
    assert "Built 1 model(s); 2 assertion(s) passed." in result.stdout


def test_test_command_uses_test_only_path(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _FakeRunner.calls.clear()
    monkeypatch.setattr("dander.cli.main.BigQueryTransformRunner", _FakeRunner)

    result = CliRunner().invoke(
        app,
        [
            "test",
            "--project",
            "valid-project-123",
            "--models-dir",
            str(tmp_path),
            "--select",
            "selected_model",
        ],
    )

    assert result.exit_code == 0
    assert _FakeRunner.calls == [("test", tmp_path, ("selected_model",))]
    assert "Tested 1 model(s); 2 assertion(s) passed." in result.stdout


@pytest.mark.parametrize(
    ("command", "blocked_module", "missing_module"),
    (
        ("build", "google.cloud", "google.cloud"),
        ("test", "google.cloud.bigquery", "google.cloud.bigquery"),
        ("build", "google.cloud.bigquery", "unrelated_dependency"),
    ),
)
def test_transform_commands_explain_missing_bigquery_only(
    command: str,
    blocked_module: str,
    missing_module: str,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import importlib.abc
import sys

command, blocked_module, missing_module = sys.argv[1:]

class MissingSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == blocked_module:
            raise ModuleNotFoundError("No module named " + missing_module, name=missing_module)

sys.meta_path.insert(0, MissingSDK())
from dander.cli.entrypoint import dispatch

try:
    dispatch([command, "--project", "offline-project"])
except SystemExit as error:
    assert missing_module != "unrelated_dependency"
    assert error.code == 1
except ModuleNotFoundError as error:
    assert missing_module == "unrelated_dependency"
    assert error.name == missing_module
else:
    raise AssertionError("Missing dependency must fail before running transforms")
assert blocked_module not in sys.modules
""",
            command,
            blocked_module,
            missing_module,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    if missing_module == "unrelated_dependency":
        assert "dander-platform[bigquery]" not in result.stderr
    else:
        assert "Install dander-platform[bigquery]" in result.stderr
        assert "Traceback" not in result.stderr
