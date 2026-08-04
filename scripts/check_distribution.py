#!/usr/bin/env python3
"""Validate Dander wheel/sdist identity, starter assets, and archive hygiene."""

from __future__ import annotations

import argparse
import email
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

EXPECTED_NAME = "dander-platform"
EXPECTED_VERSION = "0.4.0rc2"
WHEEL_REQUIRED = {
    "dander/templates/project/.dockerignore",
    "dander/templates/project/.gitignore",
    "dander/templates/project/Dockerfile",
    "dander/templates/project/README.md",
    "dander/templates/project/connectors/greenhouse_job_board.yaml",
    "dander/templates/project/dander.yaml",
    "dander/templates/project/infra/main.tf",
    "dander/templates/project/models/staging/stg_greenhouse__jobs.sql",
    "dander/templates/project/models/staging/stg_greenhouse__jobs.yml",
}
SDIST_REQUIRED = {
    "LICENSE",
    "README.md",
    "infra/main.tf",
    "pyproject.toml",
    "src/dander/project/scaffold.py",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    arguments = parser.parse_args()
    _check_wheel(arguments.wheel)
    _check_sdist(arguments.sdist)
    print(f"Validated {EXPECTED_NAME} {EXPECTED_VERSION} wheel and sdist.")


def _check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        _reject_unsafe(names)
        _require(names, WHEEL_REQUIRED, kind="wheel")
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError("Wheel must contain exactly one METADATA file")
        metadata = email.message_from_bytes(archive.read(metadata_paths[0]))
        _check_metadata(metadata["Name"], metadata["Version"])


def _check_sdist(path: Path) -> None:
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
        _check_metadata(metadata["Name"], metadata["Version"])


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


def _check_metadata(name: str | None, version: str | None) -> None:
    if name != EXPECTED_NAME or version != EXPECTED_VERSION:
        raise ValueError(f"Unexpected distribution identity: {name} {version}")


if __name__ == "__main__":
    main()
