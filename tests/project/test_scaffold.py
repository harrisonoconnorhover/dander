"""Installed-project scaffold safety and completeness."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dander import __version__
from dander.project import ProjectScaffoldError, load_project_config, scaffold_project

if TYPE_CHECKING:
    from pathlib import Path


def test_scaffold_creates_complete_paused_project(tmp_path: Path) -> None:
    project = scaffold_project(tmp_path / "analytics")

    manifest = load_project_config(project / "dander.yaml")
    manifest.validate_references(project)
    assert manifest.pipelines["greenhouse_jobs"].paused
    assert manifest.platform.safety.require_guarded_free_tier is False
    dockerfile = (project / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG DANDER_VERSION={__version__}" in dockerfile
    assert "RUN dander plugins install --config dander.yaml" in dockerfile
    assert "COPY --chown=65532:65532 dander.yaml ./dander.yaml" in dockerfile
    assert "COPY --chown=65532:65532 connectors ./connectors" in dockerfile
    assert "COPY --chown=65532:65532 graphs ./graphs" in dockerfile
    assert "COPY --chown=65532:65532 models ./models" in dockerfile
    assert "org.opencontainers.image.revision=" in dockerfile
    assert "org.opencontainers.image.created=" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert dockerfile.index("COPY --chown=65532:65532 dander.yaml") < dockerfile.index(
        "dander plugins install"
    )
    assert 'CMD ["run", "greenhouse_jobs"]' in (project / "Dockerfile").read_text(encoding="utf-8")
    assert "--guarded-free-tier" not in (project / "Dockerfile").read_text(encoding="utf-8")
    for relative in (
        ".dockerignore",
        ".gitignore",
        "Dockerfile",
        "connectors/greenhouse_job_board.yaml",
        "graphs/greenhouse_jobs.yaml",
        "infra/main.tf",
        "infra/bootstrap-admin/main.tf",
        "models/staging/stg_greenhouse__jobs.sql",
        "models/staging/stg_greenhouse__jobs.yml",
        "examples/salesforce/dander.yaml",
        "examples/salesforce/connectors/salesforce.yaml",
        "examples/salesforce/models/staging/stg_salesforce__users.sql",
        "examples/salesforce/models/staging/stg_salesforce__accounts.sql",
        "examples/salesforce/models/staging/stg_salesforce__contacts.sql",
        "examples/salesforce/models/staging/stg_salesforce__opportunities.sql",
        "examples/salesforce/models/marts/fct_salesforce__opportunities.sql",
    ):
        assert (project / relative).is_file()
    salesforce = load_project_config(project / "examples" / "salesforce" / "dander.yaml")
    assert salesforce.plugins["salesforce"].version == "0.3.1rc1"
    assert set(salesforce.pipelines) == {"salesforce_crm"}
    assert not list(project.rglob("*.tfplan"))
    assert not list(project.rglob("*.tfstate"))
    assert not list(project.rglob(".terraform"))


def test_scaffold_refuses_to_overwrite_existing_directory(tmp_path: Path) -> None:
    project = tmp_path / "existing"
    project.mkdir()
    marker = project / "keep.txt"
    marker.write_text("owned by user", encoding="utf-8")

    with pytest.raises(ProjectScaffoldError, match="already exists"):
        scaffold_project(project)

    assert marker.read_text(encoding="utf-8") == "owned by user"


def test_scaffold_refuses_to_replace_dangling_symlink(tmp_path: Path) -> None:
    project = tmp_path / "linked"
    project.symlink_to(tmp_path / "missing", target_is_directory=True)

    with pytest.raises(ProjectScaffoldError, match="already exists"):
        scaffold_project(project)

    assert project.is_symlink()
