"""Provider-import isolation for the real hosted Control console path."""

from __future__ import annotations

import subprocess
import sys
import textwrap

from click import unstyle


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
    assert "Usage: dander [OPTIONS] COMMAND [ARGS]" in unstyle(result.stdout)


def test_control_console_error_preserves_exit_code_without_loading_providers() -> None:
    script = textwrap.dedent(
        """
        import sys
        from click import ClickException
        from dander.cli.control_command import control_app
        from dander.cli.entrypoint import dispatch

        class ConfigurationFailure(ClickException):
            exit_code = 7

        @control_app.command("invalid-config")
        def invalid_config():
            raise ConfigurationFailure("Invalid Control binding")

        try:
            dispatch(("control", "invalid-config"))
        except SystemExit as error:
            forbidden = (
                "azure.identity", "boto3", "google.cloud.bigquery", "google.cloud.dataplex",
                "google.cloud.secretmanager", "oci",
            )
            loaded = sorted(
                name for name in sys.modules
                if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
            )
            assert loaded == [], loaded
            raise SystemExit(error.code) from None
        raise AssertionError("Expected a nonzero console exit")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 7
    assert "Error: Invalid Control binding" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
