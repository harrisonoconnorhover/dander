#!/usr/bin/env python3
"""Reject stale public release versions before Dander is published."""

from __future__ import annotations

import argparse
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class VersionReference:
    path: str
    label: str
    pattern: re.Pattern[str]


EXACT_REFERENCES = (
    VersionReference(
        "README.md",
        "README install command",
        re.compile(r"uv tool install dander-platform==(?P<version>[^\s]+)"),
    ),
    VersionReference(
        "README.md",
        "README status",
        re.compile(r"Dander `(?P<version>[^`]+)` is the current public beta"),
    ),
    VersionReference(
        "docs/getting-started.md",
        "quickstart install command",
        re.compile(r"uv tool install dander-platform==(?P<version>[^\s]+)"),
    ),
    VersionReference(
        "docs/upgrading.md",
        "upgrade target",
        re.compile(r'DANDER_TARGET_VERSION="(?P<version>[^"]+)"'),
    ),
    VersionReference(
        "docs/session-resume.md",
        "session public release",
        re.compile(r"Dander `(?P<version>[^`]+)` is the current public beta"),
    ),
    VersionReference(
        "docs/release-audit.md",
        "release audit public release",
        re.compile(r"Public Dander beta: `(?P<version>[^`]+)`"),
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root to validate.",
    )
    arguments = parser.parse_args()
    errors = release_metadata_errors(arguments.root.resolve())
    if errors:
        raise SystemExit("Release metadata is inconsistent:\n- " + "\n- ".join(errors))
    project = _project(arguments.root.resolve())
    print(f"Validated release metadata for {project['name']} {project['version']}.")


def release_metadata_errors(root: Path) -> list[str]:
    try:
        project = _project(root)
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        return [f"could not read project metadata: {error}"]

    name = project.get("name")
    version = project.get("version")
    if name != "dander-platform":
        return [f"pyproject project name is {name!r}; expected 'dander-platform'"]
    if not isinstance(version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.post\d+|\.dev\d+)?", version
    ):
        return [f"pyproject project version is not a supported release version: {version!r}"]

    errors: list[str] = []
    for reference in EXACT_REFERENCES:
        errors.extend(_check_reference(root, reference, expected=version))

    release_line = re.match(r"(?P<line>\d+\.\d+)\.", version)
    assert release_line is not None
    errors.extend(
        _check_single_match(
            root / "docs/known-limitations.md",
            "known-limitations release line",
            re.compile(r"Dander `(?P<version>\d+\.\d+\.x)` is beta"),
            expected=f"{release_line.group('line')}.x",
        )
    )

    changelog = _read(root / "CHANGELOG.md", errors)
    if changelog is not None and not re.search(
        rf"^## {re.escape(version)}(?:\s|$)", changelog, flags=re.MULTILINE
    ):
        errors.append(f"CHANGELOG.md has no release heading for {version}")

    template = _read(root / "src/dander/templates/project/Dockerfile", errors)
    if template is not None:
        placeholder = "ARG DANDER_VERSION=__DANDER_DISTRIBUTION_VERSION__"
        if template.count(placeholder) != 1:
            errors.append(
                "starter Dockerfile must contain exactly one Dander distribution "
                "version placeholder"
            )

    return errors


def _project(root: Path) -> dict[str, object]:
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return cast("dict[str, object]", config["project"])


def _check_reference(root: Path, reference: VersionReference, *, expected: str) -> list[str]:
    return _check_single_match(
        root / reference.path,
        reference.label,
        reference.pattern,
        expected=expected,
    )


def _check_single_match(
    path: Path,
    label: str,
    pattern: re.Pattern[str],
    *,
    expected: str,
) -> list[str]:
    errors: list[str] = []
    content = _read(path, errors)
    if content is None:
        return errors
    matches = pattern.findall(content)
    if len(matches) != 1:
        return [f"{label} must appear exactly once in {path.name}; found {len(matches)}"]
    actual = matches[0]
    if actual != expected:
        return [f"{label} uses {actual}; package metadata uses {expected}"]
    return []


def _read(path: Path, errors: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"could not read {path}: {error}")
        return None


if __name__ == "__main__":
    main()
