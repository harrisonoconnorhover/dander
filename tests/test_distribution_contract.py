"""Distribution metadata and explicit packaged-infrastructure inventory."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_distribution_identity_and_version_are_public_release() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["name"] == "dander-platform"
    assert project["version"] == "0.3.0"
    assert project["scripts"]["dander"] == "dander.cli.main:app"


def test_every_clean_infrastructure_asset_is_explicitly_packaged() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mappings = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    packaged = {Path(path) for path in mappings if path.startswith("infra/")}
    expected = {
        path.relative_to(ROOT)
        for path in (ROOT / "infra").rglob("*")
        if path.is_file() and not _generated(path)
    }

    assert packaged == expected


def _generated(path: Path) -> bool:
    relative = path.relative_to(ROOT / "infra")
    return (
        any(part in {".terraform", "__pycache__"} for part in relative.parts)
        or path.name == ".DS_Store"
        or path.name.endswith(".tfplan")
        or ".tfstate" in path.name
        or (path.name.endswith(".tfvars") and not path.name.endswith(".tfvars.example"))
    )
