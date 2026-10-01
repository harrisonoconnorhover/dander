"""The installed plugin CLI stays independent of deployment and runtime commands."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("arguments", [("plugins", "--help"), ("plugins", "search", "incident")])
def test_plugins_console_does_not_load_deployment_commands(arguments: tuple[str, ...]) -> None:
    script = textwrap.dedent(
        """
        import sys
        from dander.cli.entrypoint import dispatch

        try:
            dispatch(sys.argv[1:])
        except SystemExit as error:
            assert error.code == 0

        forbidden = (
            "dander.cli.main", "dander.bootstrap", "dander.deployment", "dander.providers",
            "azure.identity", "boto3", "google.auth", "google.api_core", "dlt", "oci",
        )
        loaded = sorted(
            name for name in sys.modules
            if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
        )
        assert loaded == [], loaded
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "plugins" in result.stdout or "dander-connector-servicenow==" in result.stdout


def test_plugins_console_reports_manifest_errors_without_traceback(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from dander.cli.entrypoint import main; main()",
            "plugins",
            "install",
            "--config",
            str(tmp_path / "missing.yaml"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "Error:" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
