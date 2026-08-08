"""Hosted post-ingestion transform and catalog ordering tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dander.cli.main import _run_post_ingestion

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def test_post_ingestion_builds_before_registry_and_dataplex(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class _Runner:
        def __init__(self, *, project: str) -> None:
            assert project == "unit-project"

        def build(
            self,
            models_dir: Path,
            *,
            selected: tuple[str, ...] | None = None,
        ) -> object:
            assert models_dir == tmp_path
            assert selected == ("stg_jobs",)
            events.append("build")
            return object()

    class _Project:
        @staticmethod
        def load(models_dir: Path, *, project_id: str) -> object:
            assert (models_dir, project_id) == (tmp_path, "unit-project")
            events.append("load")
            return object()

    class _Spine:
        def compile(
            self,
            project: object,
            *,
            selected: tuple[str, ...] | None = None,
        ) -> tuple[str, ...]:
            assert selected == ("stg_jobs",)
            events.append("compile")
            return ("asset",)

        def manifest(self, assets: tuple[str, ...]) -> dict[str, object]:
            assert assets == ("asset",)
            return {"assets": []}

    class _Registry:
        def publish(self, manifest: dict[str, object], output: Path) -> Path:
            assert manifest == {"assets": []}
            events.append("registry")
            return output

    class _Dataplex:
        def __init__(self, *, project: str, location: str) -> None:
            assert (project, location) == ("unit-project", "us")

        def publish(self, asset: object) -> str:
            assert asset == "asset"
            events.append("dataplex")
            return "published"

    def build_publisher(*, provider_id: str, project: str, location: str) -> _Dataplex:
        assert provider_id == "dataplex"
        return _Dataplex(project=project, location=location)

    monkeypatch.setattr("dander.cli.main.BigQueryTransformRunner", _Runner)
    monkeypatch.setattr("dander.cli.main.TransformProject", _Project)
    monkeypatch.setattr("dander.cli.main.MetadataSpine", _Spine)
    monkeypatch.setattr("dander.cli.main.SemanticRegistryPublisher", _Registry)
    monkeypatch.setattr("dander.cli.main.build_catalog_publisher", build_publisher)

    _run_post_ingestion(
        project="unit-project",
        models_dir=tmp_path,
        selected_models=("stg_jobs",),
        build_models=True,
        catalog_output=tmp_path / "catalog.json",
        publish_dataplex=True,
        dataplex_location="us",
    )

    assert events == ["build", "load", "compile", "registry", "dataplex"]
