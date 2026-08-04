"""CLI boundaries for Druff's operator-bound graph service."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from dander.cli.main import app

if TYPE_CHECKING:
    import pytest

    from dander.pipeline.graph_operations import GraphOperations

_REPO_ROOT = Path(__file__).parents[2]


def test_deployment_preview_requires_explicit_operator_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.yaml"
    graph.write_text(
        (_REPO_ROOT / "graphs" / "greenhouse_jobs.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = tmp_path / "dander.yaml"
    config.write_text(
        """
version: 1
platform:
  safety:
    require_guarded_free_tier: false
pipelines:
  empty_graph:
    source: greenhouse_job_board
    graph: graph.yaml
    models: []
    build_models: false
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "connectors").mkdir()
    (tmp_path / "connectors" / "greenhouse_job_board.yaml").write_text(
        (_REPO_ROOT / "connectors" / "greenhouse_job_board.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "models").mkdir()

    called = False

    def fake_serve(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("dander.cli.main.serve_graph_file", fake_serve)

    result = CliRunner().invoke(
        app,
        [
            "graph",
            "serve",
            "--file",
            str(graph),
            "--config",
            str(config),
            "--pipeline",
            "empty_graph",
            "--project",
            "unit-project",
            "--enable-deployment-preview",
        ],
    )

    assert result.exit_code == 1
    assert "--failure-alert-email" in str(result.exception)
    assert called is False


def test_deployment_preview_is_bound_at_startup_and_never_applies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graphs" / "graph.yaml"
    graph.parent.mkdir()
    graph.write_text(
        (_REPO_ROOT / "graphs" / "greenhouse_jobs.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = tmp_path / "dander.yaml"
    config.write_text(
        """
version: 1
platform:
  safety:
    require_guarded_free_tier: false
pipelines:
  empty_graph:
    source: greenhouse_job_board
    graph: graphs/graph.yaml
    models: []
    build_models: false
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "connectors").mkdir()
    (tmp_path / "connectors" / "greenhouse_job_board.yaml").write_text(
        (_REPO_ROOT / "connectors" / "greenhouse_job_board.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "models").mkdir()
    captured: dict[str, object] = {}

    def fake_serve(
        _graph_file: Path,
        *,
        origin: str,
        port: int,
        operations: GraphOperations,
        connector_plugins: tuple[object, ...],
    ) -> None:
        captured.update(
            origin=origin,
            port=port,
            operations=operations,
            connector_plugins=connector_plugins,
        )

    monkeypatch.setattr("dander.cli.main.serve_graph_file", fake_serve)
    monkeypatch.setattr(
        "dander.pipeline.graph_operations.BigQueryRunHistoryStore",
        lambda **_kwargs: object(),
    )

    result = CliRunner().invoke(
        app,
        [
            "graph",
            "serve",
            "--file",
            str(graph),
            "--config",
            str(config),
            "--pipeline",
            "empty_graph",
            "--project",
            "unit-project",
            "--enable-deployment-preview",
            "--failure-alert-email",
            "",
            "--secret-id",
            "operator-secret",
            "--no-cost-guard",
        ],
    )

    assert result.exit_code == 0, result.output
    operations = captured["operations"]
    previewer = operations._deployment_previewer  # type: ignore[attr-defined]
    assert previewer is not None
    assert previewer.settings.state_bucket == "unit-project-dander-state"
    assert previewer.settings.failure_alert_email == ""
    assert previewer.settings.secret_ids == ("operator-secret",)
    assert captured["connector_plugins"] == ()
    assert "never applies it" in result.output
