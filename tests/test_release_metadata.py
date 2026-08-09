"""Release-facing versions stay synchronized with package metadata."""

from __future__ import annotations

import shutil
from pathlib import Path

from scripts.check_release_metadata import release_metadata_errors

ROOT_FILES = (
    "CHANGELOG.md",
    "README.md",
    "acceptance/cloud-portability/phase1b/README.md",
    "docs/getting-started.md",
    "docs/known-limitations.md",
    "docs/release-audit.md",
    "docs/session-resume.md",
    "docs/upgrading.md",
    "pyproject.toml",
    "src/dander/templates/project/Dockerfile",
)


def test_release_metadata_matches_package_version() -> None:
    root = Path(__file__).parents[1]

    assert release_metadata_errors(root) == []


def test_release_metadata_check_reports_stale_readme(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    for relative in ROOT_FILES:
        source = root / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            "dander-platform==0.8.0rc3",
            "dander-platform==0.1.0",
            1,
        ),
        encoding="utf-8",
    )

    assert release_metadata_errors(tmp_path) == [
        "README install command uses 0.1.0; package metadata uses 0.8.0rc3"
    ]
