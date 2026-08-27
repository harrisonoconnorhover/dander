"""Control CLI hosted-bind and query-free logging contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from typer.testing import CliRunner

from dander.cli.main import app
from dander.control import InMemoryGraphStore

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from uvicorn import Server

    from dander.control.orchestration import PlacementCandidate
    from dander.deployment.service import GCSGraphStoreBinding


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


def test_hosted_graph_store_config_selects_binding_instead_of_local_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    oidc_path = tmp_path / "oidc.json"
    oidc_path.write_text(json.dumps(_config()), encoding="utf-8")
    graph_store_path = tmp_path / "graph-store.json"
    graph_store_path.write_text(
        json.dumps({"kind": "gcs", "bucket": "dander-control-test"}),
        encoding="utf-8",
    )
    observed: list[GCSGraphStoreBinding] = []

    def build(binding: GCSGraphStoreBinding) -> InMemoryGraphStore:
        observed.append(binding)
        return InMemoryGraphStore()

    monkeypatch.setattr("dander.control.graph_store_factory.build_bound_graph_store", build)
    monkeypatch.setattr("uvicorn.Server.run", lambda _server: None)
    result = CliRunner().invoke(
        app,
        [
            "control",
            "serve",
            "--host",
            "0.0.0.0",
            "--oidc-config",
            str(oidc_path),
            "--graph-store-config",
            str(graph_store_path),
        ],
    )

    assert result.exit_code == 0
    assert len(observed) == 1
    assert observed[0].bucket == "dander-control-test"
    assert "(gcs)" in result.stdout


def test_invalid_graph_store_config_fails_before_adapter_or_server_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph_store_path = tmp_path / "graph-store.json"
    graph_store_path.write_text(
        json.dumps(
            {
                "kind": "gcs",
                "bucket": "dander-control-test",
                "credential": "must-not-be-accepted",
            }
        ),
        encoding="utf-8",
    )
    called = False

    def unexpected(_value: object) -> InMemoryGraphStore:
        nonlocal called
        called = True
        return InMemoryGraphStore()

    monkeypatch.setattr("dander.control.graph_store_factory.build_bound_graph_store", unexpected)
    monkeypatch.setattr("uvicorn.Server.run", lambda _server: unexpected(_server))
    result = CliRunner().invoke(
        app,
        ["control", "serve", "--graph-store-config", str(graph_store_path)],
    )

    assert result.exit_code == 1
    assert called is False


def test_ephemeral_and_bound_graph_store_are_mutually_exclusive(tmp_path: Path) -> None:
    graph_store_path = tmp_path / "graph-store.json"
    graph_store_path.write_text(
        json.dumps({"kind": "local", "root": "/var/lib/dander/control"}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["control", "serve", "--ephemeral", "--graph-store-config", str(graph_store_path)],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "mutually exclusive" in str(result.exception)


def test_execution_plans_require_a_durable_run_store(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["control", "serve", "--ephemeral", "--execution-plan", str(plan_path)],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "must be configured together" in str(result.exception)


def test_execution_plan_options_install_lifecycle_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    trigger_path = tmp_path / "trigger.json"
    trigger_path.write_text("{}", encoding="utf-8")
    platforms_path = tmp_path / "dander.platforms.yaml"
    platforms_path.write_text("version: 1\n", encoding="utf-8")
    observed: list[dict[str, object]] = []

    class _Lifecycle:
        close_count = 0

        def ready(self) -> bool:
            return True

        def close(self) -> None:
            self.close_count += 1

    lifecycle = _Lifecycle()

    def build(**kwargs: object) -> object:
        observed.append(kwargs)
        return SimpleNamespace(lifecycle=lifecycle, resolver=object())

    monkeypatch.setattr(
        "dander.control.run_composition.build_fargate_run_composition",
        build,
    )
    monkeypatch.setattr("uvicorn.Server.run", lambda _server: None)
    result = CliRunner().invoke(
        app,
        [
            "control",
            "serve",
            "--ephemeral",
            "--project",
            "demo",
            "--execution-plan",
            str(plan_path),
            "--platforms-config",
            str(platforms_path),
            "--run-store-bucket",
            "dander-control-runs",
            "--trigger-spec",
            str(trigger_path),
            "--schedule-queue-url",
            "https://sqs.us-east-1.amazonaws.com/123456789012/dander-control-schedules",
            "--run-environment",
            "auto",
            "--run-placement-candidate",
            f"{'a' * 64},us-east-1,400",
            "--run-preferred-locality",
            "us-east-1",
            "--run-max-cost-microusd",
            "500",
        ],
    )

    assert result.exit_code == 0
    assert observed[0]["graph_store"].__class__ is InMemoryGraphStore
    assert observed[0]["plan_paths"] == [plan_path]
    assert observed[0]["platforms_config"] == platforms_path
    assert observed[0]["run_store_bucket"] == "dander-control-runs"
    assert observed[0]["trigger_paths"] == [trigger_path]
    assert observed[0]["schedule_queue_url"] == (
        "https://sqs.us-east-1.amazonaws.com/123456789012/dander-control-schedules"
    )
    assert observed[0]["environment"] == "auto"
    candidates = cast("tuple[PlacementCandidate, ...]", observed[0]["placement_candidates"])
    assert candidates[0].plan_revision == "a" * 64
    assert observed[0]["preferred_locality"] == "us-east-1"
    assert observed[0]["max_cost_microusd"] == 500
    assert result.stdout.count("Serving Dander Control") == 1
    assert lifecycle.close_count == 1
