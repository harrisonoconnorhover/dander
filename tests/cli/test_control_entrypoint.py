"""Provider-import isolation for the real hosted Control console path."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_control_console_dispatch_does_not_import_provider_sdks() -> None:
    script = textwrap.dedent(
        """
        import sys

        from dander.cli.entrypoint import dispatch

        try:
            dispatch(("control", "--help"))
        except SystemExit as error:
            assert error.code == 0

        forbidden = (
            "azure.identity",
            "boto3",
            "google.cloud.bigquery",
            "google.cloud.dataplex",
            "google.cloud.secretmanager",
            "oci",
        )
        loaded = sorted(
            module
            for module in sys.modules
            if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden)
        )
        assert loaded == [], loaded
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_non_control_console_dispatch_preserves_the_legacy_cli() -> None:
    script = textwrap.dedent(
        """
        from dander.cli.entrypoint import dispatch

        try:
            dispatch(("--help",))
        except SystemExit as error:
            assert error.code == 0
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage: dander [OPTIONS] COMMAND [ARGS]" in result.stdout
