"""CLI coverage for local-first metadata catalog compilation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from dander.cli.main import app

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from dander.catalog import CatalogAsset

_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


class _FakeDataplexPublisher:
    published: list[str] = []

    def __init__(self, *, project: str, location: str) -> None:
        assert project == "valid-project-123"
        assert location == "us"

    def publish(self, asset: CatalogAsset) -> str:
        self.published.append(asset.name)
        return f"entries/{asset.name}"


def _build_publisher(*, provider_id: str, project: str, location: str) -> _FakeDataplexPublisher:
    assert provider_id == "dataplex"
    return _FakeDataplexPublisher(project=project, location=location)


def test_catalog_compiles_locally_without_cloud_publication(tmp_path: Path) -> None:
    output = tmp_path / "catalog.json"

    result = CliRunner().invoke(
        app,
        [
            "catalog",
            "--project",
            "valid-project-123",
            "--models-dir",
            str(_MODELS_DIR),
            "--select",
            "stg_greenhouse__jobs",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    normalized = " ".join(result.stdout.split())
    assert "Cataloged 1 model(s)" in normalized
    assert "published 0 Dataplex entries" in normalized
    manifest = json.loads(output.read_text())
    assert manifest["assets"][0]["relation"] == ("valid-project-123.staging.stg_greenhouse__jobs")


def test_catalog_only_publishes_with_explicit_flag(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _FakeDataplexPublisher.published.clear()
    monkeypatch.setattr(
        "dander.cli.main.build_catalog_publisher",
        _build_publisher,
    )

    result = CliRunner().invoke(
        app,
        [
            "catalog",
            "--project",
            "valid-project-123",
            "--models-dir",
            str(_MODELS_DIR),
            "--select",
            "stg_greenhouse__jobs",
            "--output",
            str(tmp_path / "catalog.json"),
            "--publish-dataplex",
        ],
    )

    assert result.exit_code == 0
    assert _FakeDataplexPublisher.published == ["stg_greenhouse__jobs"]
    assert "published 1 Dataplex entry" in " ".join(result.stdout.split())
