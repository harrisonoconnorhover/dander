"""A Control profile uses the existing option validation and precedence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from dander.cli.main import app

if TYPE_CHECKING:
    from pathlib import Path

    from uvicorn import Server


@pytest.mark.parametrize("profile_first", (True, False))
def test_profile_paths_are_relative_and_explicit_flags_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile_first: bool
) -> None:
    profile = tmp_path / "control.yaml"
    profile.write_text("root: graphs\nport: 8760\nproject: [analytics]\n", encoding="utf-8")
    observed: list[Server] = []
    monkeypatch.setattr("uvicorn.Server.run", lambda server: observed.append(server))
    arguments = ["--profile", str(profile)]
    override = ["--port", "8761"]
    result = CliRunner().invoke(
        app,
        ["control", "serve", *(arguments + override if profile_first else override + arguments)],
    )
    assert result.exit_code == 0, result.output
    assert observed[0].config.port == 8761
    assert (tmp_path / "graphs").is_dir()


@pytest.mark.parametrize(
    "document",
    (
        "unknown-setting: true\n",
        "project: analytics\n",
        "root: [bad, path]\n",
        "port: invalid\n",
        "host: 0.0.0.0\nephemeral: true\n",
        "- not-an-option-mapping\n",
    ),
)
def test_profile_keeps_existing_validation_before_server_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: str
) -> None:
    profile = tmp_path / "control.yaml"
    profile.write_text(document, encoding="utf-8")
    observed: list[Server] = []
    monkeypatch.setattr("uvicorn.Server.run", lambda server: observed.append(server))
    result = CliRunner().invoke(app, ["control", "serve", "--profile", str(profile)])
    assert result.exit_code != 0
    assert not observed


def test_profile_resolves_typed_binding_paths_before_startup_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dander.control.startup_bindings import (
        PostgreSQLRunStoreStartupBinding,
        serialize_run_store_startup_binding,
    )

    binding = tmp_path / "run-store.json"
    binding.write_bytes(
        serialize_run_store_startup_binding(
            PostgreSQLRunStoreStartupBinding(
                connection_environment_variable="DANDER_CONTROL_DATABASE_URL",
                schema_name="control",
            )
        )
    )
    profile = tmp_path / "control.yaml"
    profile.write_text("run-store-config: run-store.json\n", encoding="utf-8")
    observed: list[Server] = []
    monkeypatch.setattr("uvicorn.Server.run", lambda server: observed.append(server))
    result = CliRunner().invoke(app, ["control", "serve", "--profile", str(profile)])
    assert result.exit_code != 0
    assert "--execution-plan and one run-store selection" in result.output
    assert not observed
