#!/usr/bin/env python3
"""Validate Dander wheel/sdist identity, starter assets, and archive hygiene."""

from __future__ import annotations

import argparse
import email
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
WHEEL_REQUIRED = {
    "dander/runtime-compatibility.json",
    "dander/templates/project/.dockerignore",
    "dander/templates/project/.gitignore",
    "dander/templates/project/Dockerfile",
    "dander/templates/project/README.md",
    "dander/templates/project/connectors/greenhouse_job_board.yaml",
    "dander/templates/project/dander.yaml",
    "dander/templates/project/infra/kubernetes/chart/dander/Chart.yaml",
    "dander/templates/project/infra/kubernetes/chart/dander/templates/cronjobs.yaml",
    "dander/templates/project/infra/kubernetes/chart/dander/values.schema.json",
    "dander/templates/project/infra/azure/bootstrap-admin/main.tf",
    "dander/templates/project/infra/azure/main.tf",
    "dander/templates/project/infra/azure/modules/container-apps-jobs/main.tf",
    "dander/templates/project/infra/main.tf",
    "dander/templates/project/models/staging/stg_greenhouse__jobs.sql",
    "dander/templates/project/models/staging/stg_greenhouse__jobs.yml",
    "dander/templates/project/examples/salesforce/dander.yaml",
    "dander/templates/project/examples/salesforce/connectors/salesforce.yaml",
    "dander/templates/project/examples/salesforce/models/staging/stg_salesforce__users.sql",
    "dander/templates/project/examples/salesforce/models/staging/stg_salesforce__users.yml",
    "dander/templates/project/examples/salesforce/models/staging/stg_salesforce__accounts.sql",
    "dander/templates/project/examples/salesforce/models/staging/stg_salesforce__accounts.yml",
    "dander/templates/project/examples/salesforce/models/staging/stg_salesforce__contacts.sql",
    "dander/templates/project/examples/salesforce/models/staging/stg_salesforce__contacts.yml",
    "dander/templates/project/examples/salesforce/models/staging/stg_salesforce__opportunities.sql",
    "dander/templates/project/examples/salesforce/models/staging/stg_salesforce__opportunities.yml",
    "dander/templates/project/examples/salesforce/models/marts/fct_salesforce__opportunities.sql",
    "dander/templates/project/examples/salesforce/models/marts/fct_salesforce__opportunities.yml",
}
SDIST_REQUIRED = {
    "LICENSE",
    "README.md",
    "docs/compatibility-matrix.md",
    "docs/postgresql-benchmarks.md",
    "docs/warehouse-staging.md",
    "examples/salesforce/dander.yaml",
    "infra/main.tf",
    "infra/kubernetes/chart/dander/Chart.yaml",
    "infra/azure/bootstrap-admin/main.tf",
    "infra/azure/modules/container-apps-jobs/main.tf",
    "pyproject.toml",
    "scripts/benchmarks/postgresql.py",
    "src/dander/project/scaffold.py",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    arguments = parser.parse_args()
    expected_name, expected_version = _project_identity()
    _check_wheel(arguments.wheel, expected_name=expected_name, expected_version=expected_version)
    _check_sdist(arguments.sdist, expected_name=expected_name, expected_version=expected_version)
    print(f"Validated {expected_name} {expected_version} wheel and sdist.")


def _check_wheel(path: Path, *, expected_name: str, expected_version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        _reject_unsafe(names)
        _require(names, WHEEL_REQUIRED, kind="wheel")
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError("Wheel must contain exactly one METADATA file")
        metadata = email.message_from_bytes(archive.read(metadata_paths[0]))
        _check_metadata(
            metadata,
            expected_name=expected_name,
            expected_version=expected_version,
        )
        starter_dockerfile = archive.read("dander/templates/project/Dockerfile").decode("utf-8")
        required_runtime_markers = {
            "dander-platform[runtime-all]==${DANDER_VERSION}",
            "require_full_runtime",
        }
        if missing := sorted(
            marker for marker in required_runtime_markers if marker not in starter_dockerfile
        ):
            raise ValueError(
                "Starter Dockerfile does not install the full runtime: " + ", ".join(missing)
            )


def _check_sdist(path: Path, *, expected_name: str, expected_version: str) -> None:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if any(member.issym() or member.islnk() for member in members):
            raise ValueError("Source distribution must not contain links")
        names = {member.name for member in members}
        _reject_unsafe(names)
        roots = {PurePosixPath(name).parts[0] for name in names if PurePosixPath(name).parts}
        if len(roots) != 1:
            raise ValueError("Source distribution must have one archive root")
        root = roots.pop()
        _require(names, {f"{root}/{name}" for name in SDIST_REQUIRED}, kind="sdist")
        metadata_name = f"{root}/PKG-INFO"
        metadata_file = archive.extractfile(metadata_name)
        if metadata_file is None:
            raise ValueError("Source distribution is missing PKG-INFO")
        metadata = email.message_from_binary_file(metadata_file)
        _check_metadata(
            metadata,
            expected_name=expected_name,
            expected_version=expected_version,
        )


def _reject_unsafe(names: set[str]) -> None:
    for name in names:
        parts = PurePosixPath(name).parts
        if any(part in {".DS_Store", ".terraform", "__pycache__"} for part in parts):
            raise ValueError(f"Unsafe generated path in distribution: {name}")
        filename = PurePosixPath(name).name
        if (
            filename.endswith(".tfplan")
            or ".tfstate" in filename
            or (filename.endswith(".tfvars") and not filename.endswith(".tfvars.example"))
        ):
            raise ValueError(f"Unsafe Terraform artifact in distribution: {name}")


def _require(names: set[str], required: set[str], *, kind: str) -> None:
    if missing := sorted(required - names):
        raise ValueError(f"Missing {kind} assets: {', '.join(missing)}")


def _check_metadata(
    metadata: email.message.Message,
    *,
    expected_name: str,
    expected_version: str,
) -> None:
    name = metadata["Name"]
    version = metadata["Version"]
    if name != expected_name or version != expected_version:
        raise ValueError(f"Unexpected distribution identity: {name} {version}")
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected_extras = set(config["project"]["optional-dependencies"])
    provided_extras = set(metadata.get_all("Provides-Extra") or ())
    if provided_extras != expected_extras:
        raise ValueError(
            "Distribution extras differ from pyproject.toml: "
            f"expected {sorted(expected_extras)}, received {sorted(provided_extras)}"
        )
    description = metadata.get_payload()
    required_description = {
        f"uv tool install dander-platform=={expected_version}",
        f"Dander `{expected_version}` is the current public beta",
    }
    if not isinstance(description, str):
        raise ValueError("Distribution description is not text")
    missing = sorted(marker for marker in required_description if marker not in description)
    if missing:
        raise ValueError(f"Distribution description is stale: {', '.join(missing)}")


def _project_identity() -> tuple[str, str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    return project["name"], project["version"]


if __name__ == "__main__":
    main()
