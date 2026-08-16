"""Stable qualification-run CLI behavior."""

from __future__ import annotations

import os
import pathlib
import sys
from typing import TYPE_CHECKING, Never

import pytest
from typer.testing import CliRunner

from dander.cli.main import app
from dander.cli.qualification_command import (
    QualificationRunnerError,
    execute_qualification_script,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def test_qualification_run_uses_installed_python_and_forwards_arguments(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    script = tmp_path / "mounted qualification.py"
    script.write_text(
        "raise AssertionError('exec should replace this process')\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def replace_process(executable: str, arguments: list[str]) -> Never:
        captured.update(executable=executable, arguments=arguments)
        raise SystemExit(23)

    monkeypatch.setattr(os, "execv", replace_process)

    result = CliRunner().invoke(
        app,
        [
            "qualification-run",
            str(script),
            "--rows",
            "2600000",
            "--report",
            "/reports/result.json",
            "--help",
        ],
    )

    assert result.exit_code == 23
    assert captured == {
        "executable": sys.executable,
        "arguments": [
            sys.executable,
            str(script),
            "--rows",
            "2600000",
            "--report",
            "/reports/result.json",
            "--help",
        ],
    }


def test_qualification_run_rejects_missing_script(tmp_path: Path) -> None:
    missing = tmp_path / "missing.py"

    result = CliRunner().invoke(app, ["qualification-run", str(missing)])

    assert result.exit_code == 1
    assert result.exception is not None
    assert f"Qualification script is not a file: {missing}" in str(result.exception)


def test_qualification_run_rejects_unreadable_script(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    script = tmp_path / "qualification.py"
    script.write_text("pass\n", encoding="utf-8")

    def refuse_open(self: pathlib.Path, *args: object, **kwargs: object) -> Never:
        raise PermissionError

    monkeypatch.setattr(pathlib.Path, "open", refuse_open)

    with pytest.raises(QualificationRunnerError, match="Qualification script is not readable"):
        execute_qualification_script(script, ())


def test_qualification_run_is_listed_in_root_help() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "qualification-run" in result.output
