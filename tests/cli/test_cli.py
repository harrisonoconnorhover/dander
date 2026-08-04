"""CLI behavior tests for DANDER-20."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dander.cli.main import _build_source_adapter, app
from dander.ingestion import OdooJson2Source, load_source_config
from dander.security import NoAuth

_REPO_ROOT = Path(__file__).parents[2]


def test_greenhouse_dry_run_needs_no_credentials_or_gcp() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "greenhouse",
            "--dry-run",
            "--project",
            "unit-project",
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "greenhouse_candidates" in result.output
    assert "greenhouse_jobs" in result.output
    assert "SCD1" in result.output


def test_public_job_board_dry_run_needs_no_credentials() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "greenhouse_job_board",
            "--dry-run",
            "--project",
            "unit-project",
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "greenhouse_job_board_jobs" in result.output


def test_dry_run_reports_configured_writer_batch_rows() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "greenhouse_job_board",
            "--dry-run",
            "--project",
            "unit-project",
            "--batch-rows",
            "2048",
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Writer batch rows: 2048" in result.output


def test_graph_pipeline_dry_run_binds_connector_endpoint_and_target(tmp_path: Path) -> None:
    graphs = tmp_path / "graphs"
    graphs.mkdir()
    (graphs / "greenhouse_jobs.yaml").write_text(
        (_REPO_ROOT / "graphs" / "greenhouse_jobs.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = tmp_path / "dander.yaml"
    config.write_text(
        """
version: 1
pipelines:
  graph_greenhouse:
    source: greenhouse_job_board
    graph: graphs/greenhouse_jobs.yaml
    models: []
    build_models: false
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "graph_greenhouse",
            "--dry-run",
            "--project",
            "unit-project",
            "--config",
            str(config),
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
            "--models-dir",
            str(_REPO_ROOT / "models"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "greenhouse_job_board_jobs" in result.output
    assert "PipelineGraph targets" in result.output
    assert "unit-project.staging.graph_greenhouse_jobs" in result.output
    assert "REPLACE" in result.output


def test_harvest_v3_dry_run_validates_without_credentials() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "greenhouse",
            "--dry-run",
            "--project",
            "unit-project",
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "greenhouse_candidates" in result.output


def test_odoo_json2_dry_run_validates_without_credentials(tmp_path: Path) -> None:
    connectors = tmp_path / "connectors"
    connectors.mkdir()
    (connectors / "odoo.yaml").write_text(
        (_REPO_ROOT / "connectors" / "odoo.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "odoo",
            "--dry-run",
            "--project",
            "unit-project",
            "--connectors-dir",
            str(connectors),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "odoo_partners" in result.output
    assert "SCD1" in result.output


def test_odoo_json2_engine_selects_enterprise_source() -> None:
    config = load_source_config(_REPO_ROOT / "connectors" / "odoo.example.yaml")

    source = _build_source_adapter(config, NoAuth())

    assert isinstance(source, OdooJson2Source)


def test_sandbox_dry_run_declares_replace_mode_without_network() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "greenhouse",
            "--sandbox",
            "--dry-run",
            "--project",
            "unit-project",
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "REPLACE (sandbox)" in result.output


def test_guarded_free_tier_dry_run_declares_production_mode_without_network() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "greenhouse",
            "--guarded-free-tier",
            "--dry-run",
            "--project",
            "unit-project",
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "SCD1 (guarded billing)" in result.output


def test_billing_modes_are_mutually_exclusive() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "greenhouse",
            "--sandbox",
            "--guarded-free-tier",
            "--dry-run",
            "--project",
            "unit-project",
            "--connectors-dir",
            str(_REPO_ROOT / "connectors"),
        ],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "mutually exclusive" in str(result.exception)


def test_graph_operations_require_project_and_pipeline_together(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "graph",
            "serve",
            "--file",
            str(tmp_path / "graph.yaml"),
            "--pipeline",
            "graph_records",
        ],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "--pipeline and --project must be supplied together" in str(result.exception)
