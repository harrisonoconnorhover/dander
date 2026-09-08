"""Named pipelines resolve the same project files during validation and execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

import dander.cli.main as cli_module
from dander.cli.main import app
from dander.security import NoAuth

if TYPE_CHECKING:
    from pathlib import Path

    from dander.ingestion import SourceConfig


def _write_connector(directory: Path, endpoint: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "example.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "example",
                "base_url": "https://example.test",
                "auth_strategy": "none",
                "endpoints": [
                    {
                        "name": endpoint,
                        "path": "/records",
                        "primary_key": ["id"],
                        "raw_schema": [{"name": "id", "type": "INT64"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("directory_kind", ("default", "relative", "absolute"))
def test_named_pipeline_ignores_same_named_connector_in_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory_kind: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = project / "dander.yaml"
    config.write_text(
        "version: 1\npipelines:\n  example_pipeline:\n    source: example\n"
        "    models: []\n    build_models: false\n",
        encoding="utf-8",
    )
    working = tmp_path / "working"
    _write_connector(working / "connectors", "shadow_records")
    monkeypatch.chdir(working)

    if directory_kind == "default":
        _write_connector(project / "connectors", "project_records")
        directory_args: list[str] = []
    elif directory_kind == "relative":
        _write_connector(project / "resources" / "connectors", "project_records")
        _write_connector(working / "resources" / "connectors", "shadow_records")
        directory_args = ["--connectors-dir", "resources/connectors"]
    else:
        override = tmp_path / "override"
        _write_connector(override, "project_records")
        directory_args = ["--connectors-dir", str(override)]
    common = ["--config", str(config), *directory_args]
    result = CliRunner().invoke(app, ["validate", *common])
    assert result.exit_code == 0, result.output

    result = CliRunner().invoke(app, ["run", "example_pipeline", "--dry-run", *common])
    assert result.exit_code == 0, result.output
    assert "project_records" in result.output
    assert "shadow_records" not in result.output

    inspected: list[str] = []

    def capture_auth(config: SourceConfig, _secrets: object) -> NoAuth:
        inspected.extend(endpoint.name for endpoint in config.endpoints)
        return NoAuth()

    monkeypatch.setattr(cli_module, "_build_auth", capture_auth)
    result = CliRunner().invoke(app, ["connector", "inspect", "example_pipeline", *common])
    assert result.exit_code == 0, result.output
    assert inspected == ["project_records"]

    # Source-name commands keep their existing working-directory interpretation.
    if directory_kind == "default":
        result = CliRunner().invoke(app, ["run", "example", "--dry-run", *common])
        assert result.exit_code == 0, result.output
        assert "shadow_records" in result.output
        inspected.clear()
        result = CliRunner().invoke(app, ["connector", "inspect", "example", *common])
        assert result.exit_code == 0, result.output
        assert inspected == ["shadow_records"]
