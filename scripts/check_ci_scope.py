#!/usr/bin/env python3
"""Select focused CI checks from every path affected by a Git diff."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def changed_paths(base: str, head: str, *, repository: Path) -> list[str]:
    """Include deletions and both sides of renames, without splitting path names."""
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", base, head, "--"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    return [path.decode("utf-8") for path in result.stdout.split(b"\0") if path]


def is_rc32_redshift_objective(value: str) -> bool:
    """Recognize the existing exact-RC32 objective filename convention."""
    name = Path(value).name
    return "rc32-redshift" in name and "objective" in name and name.endswith(".json")


def classify_ci_scope(paths: list[str]) -> str:
    """Choose the broadest required lane; unknown or empty input requires full CI."""
    if not paths:
        return "full"
    lane = "documentation"
    for value in paths:
        path = Path(value)
        documentation = value in {
            "AGENTS.md",
            "HANDOFF.md",
            "README.md",
            "CHANGELOG.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
        } or (
            value.startswith(("docs/", "tickets/", "steering/", "infra/")) and path.suffix == ".md"
        )
        if documentation:
            continue
        if value.startswith("docs/evidence/phase8/"):
            if "objective" in path.name and not is_rc32_redshift_objective(value):
                return "full"
            if lane == "documentation":
                lane = "objective"
            continue
        benchmark = (
            value.startswith("scripts/benchmarks/")
            or value
            in {
                "scripts/validate_redshift_objective.py",
                "tests/test_validate_redshift_objective.py",
            }
            or (
                value.startswith("tests/portability/")
                and ("phase8" in path.name or "redshift" in path.name)
            )
        )
        if not benchmark:
            return "full"
        lane = "benchmark"
    return lane


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    arguments = parser.parse_args()
    paths = changed_paths(arguments.base, arguments.head, repository=Path.cwd())
    print(classify_ci_scope(paths))


if __name__ == "__main__":
    main()
