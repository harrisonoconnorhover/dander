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

REDSHIFT_OBJECTIVE_VALIDATOR = ROOT / "scripts/validate_redshift_objective.py"


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


def changed_rc32_redshift_objectives(
    repository_root: Path,
    *,
    base: str,
    head: str,
) -> tuple[Path, ...]:
    """Return new or changed RC32 Redshift objectives in the proposed PR."""
    base_ref = _resolve_ref(repository_root, f"refs/remotes/origin/{base}", base)
    head_ref = _resolve_ref(repository_root, f"refs/heads/{head}", head)
    changed = subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            f"{base_ref}...{head_ref}",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if changed.returncode != 0:
        raise SystemExit(
            changed.stderr.strip() or "Could not inspect the proposed pull-request diff."
        )
    return tuple(
        repository_root / line
        for line in changed.stdout.splitlines()
        if _is_rc32_redshift_objective(line)
    )


def run_redshift_objective_preflight(
    repository_root: Path,
    *,
    base: str,
    head: str,
    smoke_image: str | None,
) -> None:
    """Fail before GitHub when a new RC32 objective misses static or image preflight."""
    validator = repository_root / REDSHIFT_OBJECTIVE_VALIDATOR.relative_to(ROOT)
    if not validator.is_file():
        return
    objectives = changed_rc32_redshift_objectives(repository_root, base=base, head=head)
    if not objectives:
        return
    if not smoke_image:
        raise SystemExit(
            "New RC32 Redshift objectives require --redshift-smoke-image so the exact candidate "
            "command is tested before opening the pull request."
        )
    command = [
        sys.executable,
        str(validator),
        "--repository-root",
        str(repository_root),
        "--smoke-image",
        smoke_image,
        *(str(path) for path in objectives),
    ]
    validated = subprocess.run(command, capture_output=True, check=False, text=True)
    if validated.returncode != 0:
        raise SystemExit(validated.stderr.strip() or validated.stdout.strip())


def _resolve_ref(repository_root: Path, preferred: str, fallback: str) -> str:
    for candidate in (preferred, fallback):
        resolved = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "--verify", candidate],
            capture_output=True,
            check=False,
            text=True,
        )
        if resolved.returncode == 0:
            return candidate
    raise SystemExit(f"Could not resolve Git ref {fallback!r} for objective preflight.")


def _is_rc32_redshift_objective(path: str) -> bool:
    name = Path(path).name
    return (
        path.startswith("docs/evidence/phase8/")
        and "rc32-redshift" in name
        and "objective" in name
        and name.endswith(".json")
    )


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
    parser.add_argument(
        "--redshift-smoke-image",
        help=(
            "Local immutable RC32 image reference required when the diff adds an RC32 "
            "Redshift objective."
        ),
    )
    arguments = parser.parse_args()

    if ":" in arguments.base or ":" in arguments.head:
        raise SystemExit("Cross-repository base or head syntax is not supported.")

    repository_root = arguments.repository_root.resolve()
    verify_remote(repository_root, "origin")
    verify_remote(repository_root, "origin", push=True)
    run_redshift_objective_preflight(
        repository_root,
        base=arguments.base,
        head=arguments.head,
        smoke_image=arguments.redshift_smoke_image,
    )

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
