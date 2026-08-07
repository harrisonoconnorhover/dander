"""Read-only runtime inspection and credential-free local conformance."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import dander.runtime_inspection as inspection_module
from dander.cli.main import app
from dander.plugins import (
    ConnectorDescriptor,
    ConnectorEndpointDescriptor,
    ConnectorPlugin,
    ConnectorPluginRegistry,
    InstalledConnectorPlugin,
)
from dander.runtime_contract import RUNTIME_CONTRACT, RuntimeContractError
from dander.runtime_inspection import inspect_runtime, run_local_conformance

if TYPE_CHECKING:
    from pathlib import Path

    from dander.ingestion import Source, SourceConfig
    from dander.security import AuthStrategy


def _manifest(path: Path) -> Path:
    config = path / "dander.yaml"
    config.write_text(
        """\
version: 1
platform:
  safety:
    require_guarded_free_tier: false
pipelines:
  runtime_conformance:
    source: example
    models: []
    build_models: false
""",
        encoding="utf-8",
    )
    return config


def test_runtime_inspect_reports_active_metadata_without_provider_access(tmp_path: Path) -> None:
    inspection = json.loads(inspect_runtime(_manifest(tmp_path)).to_json())

    assert inspection["contract"] == RUNTIME_CONTRACT
    assert inspection["build"]["distribution"] == "dander-platform"
    assert inspection["adapters"]["platform_profiles"] == ["gcp"]
    assert inspection["adapters"]["launchers"] == ["cloud_run", "local"]
    assert "bigquery" in inspection["adapters"]["warehouses"]
    assert "dlt" in inspection["adapters"]["ingestion_engines"]
    assert inspection["plugins"] == []


def test_runtime_inspect_reports_declared_plugin_without_constructing_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def source_factory(config: SourceConfig, auth: AuthStrategy) -> Source:
        del config, auth
        raise AssertionError("inspection must not construct a source")

    plugin = ConnectorPlugin(
        plugin_id="example",
        api_version=1,
        engine="example_engine",
        display_name="Example",
        source_factory=source_factory,
        connectors=(
            ConnectorDescriptor(
                connector_id="example",
                display_name="Example",
                engine="example_engine",
                endpoints=(
                    ConnectorEndpointDescriptor(
                        endpoint_id="widgets",
                        display_name="Widgets",
                    ),
                ),
            ),
        ),
    )
    registry = ConnectorPluginRegistry(
        source_factories={"example_engine": source_factory},
        plugins=(
            InstalledConnectorPlugin(
                plugin=plugin,
                distribution="dander-connector-example",
                version="1.2.3",
            ),
        ),
    )
    monkeypatch.setattr(inspection_module, "load_connector_plugins", lambda _pins: registry)

    inspection = json.loads(inspect_runtime(_manifest(tmp_path)).to_json())

    assert inspection["plugins"] == [
        {
            "api_version": 1,
            "connectors": [
                {
                    "connector_id": "example",
                    "display_name": "Example",
                    "endpoints": ["widgets"],
                }
            ],
            "display_name": "Example",
            "distribution": "dander-connector-example",
            "engine": "example_engine",
            "plugin_id": "example",
            "version": "1.2.3",
        }
    ]


def test_local_conformance_runs_one_pipeline_and_limits_filesystem_writes(
    tmp_path: Path,
) -> None:
    result = run_local_conformance(tmp_path)

    assert result.contract == RUNTIME_CONTRACT
    assert result.status == "succeeded"
    assert result.events == ("runtime.started", "runtime.completed")
    assert result.filesystem_writes == ("state.db",)
    assert result.signal == "SIGTERM"
    assert {path.name for path in tmp_path.iterdir()} == {"state.db"}


def test_local_conformance_never_overwrites_existing_state(tmp_path: Path) -> None:
    state = tmp_path / "state.db"
    state.write_text("operator-owned", encoding="utf-8")

    with pytest.raises(RuntimeContractError, match="already exists"):
        run_local_conformance(tmp_path)

    assert state.read_text(encoding="utf-8") == "operator-owned"


def test_runtime_inspect_and_conformance_cli_emit_one_json_document(tmp_path: Path) -> None:
    runner = CliRunner()
    inspected = runner.invoke(app, ["runtime", "inspect", "--config", str(_manifest(tmp_path))])
    conformance_dir = tmp_path / "probe"
    conformed = runner.invoke(
        app,
        ["runtime", "conformance", "--work-dir", str(conformance_dir)],
    )

    assert inspected.exit_code == 0, inspected.output
    assert conformed.exit_code == 0, conformed.output
    assert json.loads(inspected.output)["contract"] == RUNTIME_CONTRACT
    assert json.loads(conformed.output)["status"] == "succeeded"
    assert inspected.stderr == ""
    assert conformed.stderr == ""
