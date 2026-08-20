#!/usr/bin/env python3
"""Create and verify a Dander pull request in the canonical writable fork."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_repository_target import (  # noqa: E402
    CANONICAL_REPOSITORY,
    verify_remote,
)


def create_command(
    *,
    base: str,
    head: str,
    title: str,
    body: str | None,
    body_file: Path | None,
    draft: bool,
) -> list[str]:
    """Build the only supported ``gh pr create`` command."""
    command = [
        "gh",
        "pr",
        "create",
        "--repo",
        CANONICAL_REPOSITORY,
        "--base",
        base,
        "--head",
        head,
        "--title",
        title,
    ]
    if body is not None:
        command.extend(("--body", body))
    if body_file is not None:
        command.extend(("--body-file", str(body_file)))
    if draft:
        command.append("--draft")
    return command


def pull_request_target_errors(payload: Mapping[str, object], *, base: str, head: str) -> list[str]:
    """Return every repository or branch mismatch in a created PR response."""
    expected = {
        "base repository": CANONICAL_REPOSITORY,
        "base branch": base,
        "head repository": CANONICAL_REPOSITORY,
        "head branch": head,
    }
    actual = {
        "base repository": _nested(payload, "base", "repo", "full_name"),
        "base branch": _nested(payload, "base", "ref"),
        "head repository": _nested(payload, "head", "repo", "full_name"),
        "head branch": _nested(payload, "head", "ref"),
    }
    return [
        f"{label} is {actual[label]!r}; expected {expected_value!r}"
        for label, expected_value in expected.items()
        if actual[label] != expected_value
    ]


def _nested(payload: Mapping[str, object], *keys: str) -> object | None:
    value: object = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _created_pull_number(output: str) -> int:
    pattern = re.compile(
        rf"https://github\.com/{re.escape(CANONICAL_REPOSITORY)}/pull/(?P<number>\d+)"
    )
    matches = list(pattern.finditer(output))
    if not matches:
        raise SystemExit("Pull request creation returned no canonical pull-request URL.")
    return int(matches[-1].group("number"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Explicit base branch.")
    parser.add_argument("--head", required=True, help="Explicit head branch in the writable fork.")
    parser.add_argument("--title", required=True)
    body = parser.add_mutually_exclusive_group()
    body.add_argument("--body")
    body.add_argument("--body-file", type=Path)
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    arguments = parser.parse_args()

    if ":" in arguments.base or ":" in arguments.head:
        raise SystemExit("Cross-repository base or head syntax is not supported.")

    repository_root = arguments.repository_root.resolve()
    verify_remote(repository_root, "origin")
    verify_remote(repository_root, "origin", push=True)

    created = subprocess.run(
        create_command(
            base=arguments.base,
            head=arguments.head,
            title=arguments.title,
            body=arguments.body,
            body_file=arguments.body_file,
            draft=arguments.draft,
        ),
        capture_output=True,
        check=False,
        text=True,
    )
    if created.returncode != 0:
        raise SystemExit(created.stderr.strip() or "Pull request creation failed.")
    pull_number = _created_pull_number(created.stdout)

    inspected = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{CANONICAL_REPOSITORY}/pulls/{pull_number}",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if inspected.returncode != 0:
        raise SystemExit(inspected.stderr.strip() or "Created pull request could not be verified.")
    try:
        payload = json.loads(inspected.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(f"Created pull request returned invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SystemExit("Created pull request response must be a JSON object.")

    errors = pull_request_target_errors(payload, base=arguments.base, head=arguments.head)
    if errors:
        raise SystemExit("Pull request target rejected:\n- " + "\n- ".join(errors))

    print(f"Verified pull request: https://github.com/{CANONICAL_REPOSITORY}/pull/{pull_number}")


if __name__ == "__main__":
    main()
