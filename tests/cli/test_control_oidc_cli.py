"""Control CLI hosted-bind and query-free logging contracts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from dander.cli.main import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from uvicorn import Server


def _config() -> dict[str, object]:
    return {
        "api_url": "https://control.example.test",
        "issuer": "https://identity.example.test",
        "jwks_uri": "https://identity.example.test/.well-known/jwks.json",
        "public_client_id": "druff-public-client",
        "api_audience": "https://control.example.test/api",
        "redirect_uri": "https://druff.example.test/auth/callback",
        "logout_uri": "https://druff.example.test/signed-out",
        "allowed_origins": ["https://druff.example.test"],
    }


def test_external_bind_requires_a_valid_oidc_input() -> None:
    result = CliRunner().invoke(app, ["control", "serve", "--host", "0.0.0.0", "--ephemeral"])

    assert result.exit_code == 1
    assert result.exception is not None
    assert "require a valid --oidc-config" in str(result.exception)


def test_hosted_server_disables_query_bearing_uvicorn_access_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "oidc.json"
    path.write_text(json.dumps(_config()), encoding="utf-8")
    observed: list[Server] = []

    def capture(server: Server) -> None:
        observed.append(server)

    monkeypatch.setattr("uvicorn.Server.run", capture)
    result = CliRunner().invoke(
        app,
        [
            "control",
            "serve",
            "--host",
            "0.0.0.0",
            "--ephemeral",
            "--oidc-config",
            str(path),
        ],
    )

    assert result.exit_code == 0
    assert len(observed) == 1
    assert observed[0].config.access_log is False
    assert "https://control.example.test" in result.stdout


def test_malformed_oidc_input_fails_before_server_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "oidc.json"
    path.write_text("{}", encoding="utf-8")
    called = False

    def capture(_server: Server) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("uvicorn.Server.run", capture)
    result = CliRunner().invoke(
        app,
        ["control", "serve", "--ephemeral", "--oidc-config", str(path)],
    )

    assert result.exit_code == 1
    assert called is False
