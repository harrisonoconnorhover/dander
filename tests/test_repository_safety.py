"""Repository pushes and pull requests fail closed outside the writable fork."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.create_pull_request import (
    changed_rc32_redshift_objectives,
    create_command,
    pull_request_target_errors,
    run_redshift_objective_preflight,
)
from scripts.verify_repository_target import (
    CANONICAL_REPOSITORY,
    RepositoryTargetError,
    normalize_repository_url,
    verify_repository_url,
)

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://github.com/harrisonoconnorhover/dander.git",
        "ssh://git@github.com/harrisonoconnorhover/dander.git",
        "git@github.com:harrisonoconnorhover/dander.git",
    ],
)
def test_canonical_https_and_ssh_urls_are_accepted(remote_url: str) -> None:
    assert normalize_repository_url(remote_url) == CANONICAL_REPOSITORY
    assert verify_repository_url(remote_url) == CANONICAL_REPOSITORY


@pytest.mark.parametrize(
    ("remote_url", "resolved_repository"),
    [
        ("https://github.com/WagnerJ-Dev/dander.git", "wagnerj-dev/dander"),
        ("git@github.com:harrisonoconnorhover/druff.git", "harrisonoconnorhover/druff"),
    ],
)
def test_wrong_owner_and_repository_are_rejected(
    remote_url: str,
    resolved_repository: str,
) -> None:
    assert normalize_repository_url(remote_url) == resolved_repository
    with pytest.raises(RepositoryTargetError, match="is not writable"):
        verify_repository_url(remote_url)


def test_pre_push_hook_rejects_upstream_push(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    _copy_safety_files(tmp_path)
    hook = tmp_path / ".githooks/pre-push"

    accepted = subprocess.run(
        [str(hook), "origin", "git@github.com:harrisonoconnorhover/dander.git"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )
    rejected = subprocess.run(
        [str(hook), "upstream", "https://github.com/WagnerJ-Dev/dander.git"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert accepted.returncode == 0
    assert rejected.returncode != 0
    assert "Repository target rejected" in rejected.stderr


def test_bootstrap_is_idempotent_and_disables_upstream_pushes(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    _copy_safety_files(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:harrisonoconnorhover/dander.git"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "upstream", "https://example.com/wrong/repository.git"],
        cwd=tmp_path,
        check=True,
    )
    environment = _fake_gh_environment(tmp_path)

    for _ in range(2):
        subprocess.run(
            [str(tmp_path / "scripts/bootstrap_repository_safety.sh")],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            check=True,
            text=True,
        )

    assert _git_config(tmp_path, "remote.pushDefault") == "origin"
    assert _git_config(tmp_path, "core.hooksPath") == ".githooks"
    assert _git_remote_url(tmp_path, "upstream") == "https://github.com/WagnerJ-Dev/dander.git"
    assert _git_remote_url(tmp_path, "upstream", push=True) == "disabled://WagnerJ-Dev/dander"
    assert Path(environment["FAKE_GH_STATE"]).read_text(encoding="utf-8").strip() == (
        CANONICAL_REPOSITORY
    )


def test_pr_command_explicitly_targets_canonical_repository() -> None:
    command = create_command(
        base="main",
        head="codex/repository-target-safety",
        title="Repository safety",
        body=None,
        body_file=None,
        draft=True,
    )

    assert command[:6] == ["gh", "pr", "create", "--repo", CANONICAL_REPOSITORY, "--base"]
    assert command[command.index("--base") + 1] == "main"
    assert command[command.index("--head") + 1] == "codex/repository-target-safety"
    assert "--draft" in command


@pytest.mark.parametrize(
    ("payload_update", "message"),
    [
        ({"base": {"repo": {"full_name": "WagnerJ-Dev/dander"}, "ref": "main"}}, "base repository"),
        ({"base": {"repo": {"full_name": CANONICAL_REPOSITORY}, "ref": "develop"}}, "base branch"),
        ({"head": {"repo": {"full_name": "someone/dander"}, "ref": "feature"}}, "head repository"),
        ({"head": {"repo": {"full_name": CANONICAL_REPOSITORY}, "ref": "other"}}, "head branch"),
    ],
)
def test_pr_target_verification_rejects_every_mismatch(
    payload_update: dict[str, object],
    message: str,
) -> None:
    payload: dict[str, object] = {
        "base": {"repo": {"full_name": CANONICAL_REPOSITORY}, "ref": "main"},
        "head": {"repo": {"full_name": CANONICAL_REPOSITORY}, "ref": "feature"},
    }
    payload.update(payload_update)

    errors = pull_request_target_errors(payload, base="main", head="feature")

    assert len(errors) == 1
    assert message in errors[0]


def test_pr_wrapper_verifies_created_target(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/harrisonoconnorhover/dander.git"],
        cwd=tmp_path,
        check=True,
    )
    environment = _fake_gh_environment(tmp_path)
    environment["FAKE_PR_JSON"] = json.dumps(
        {
            "base": {"repo": {"full_name": CANONICAL_REPOSITORY}, "ref": "main"},
            "head": {
                "repo": {"full_name": CANONICAL_REPOSITORY},
                "ref": "codex/repository-target-safety",
            },
        }
    )

    result = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts/create_pull_request.py"),
            "--repository-root",
            str(tmp_path),
            "--base",
            "main",
            "--head",
            "codex/repository-target-safety",
            "--title",
            "Repository safety",
            "--draft",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"https://github.com/{CANONICAL_REPOSITORY}/pull/999" in result.stdout
    log = Path(environment["FAKE_GH_LOG"]).read_text(encoding="utf-8")
    assert f"pr create --repo {CANONICAL_REPOSITORY}" in log
    assert f"api repos/{CANONICAL_REPOSITORY}/pulls/999" in log


def test_pr_wrapper_requires_image_smoke_for_new_rc32_redshift_objectives(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=tmp_path, check=True)
    validator = tmp_path / "scripts/validate_redshift_objective.py"
    validator.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/validate_redshift_objective.py", validator)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "switch", "-qc", "codex/redshift-objective"], cwd=tmp_path, check=True)
    objective = (
        tmp_path / "docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-bulk-objectives.json"
    )
    objective.parent.mkdir(parents=True)
    objective.write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "objective"], cwd=tmp_path, check=True)

    assert changed_rc32_redshift_objectives(
        tmp_path,
        base="main",
        head="codex/redshift-objective",
    ) == (objective,)
    with pytest.raises(SystemExit, match="--redshift-smoke-image"):
        run_redshift_objective_preflight(
            tmp_path,
            base="main",
            head="codex/redshift-objective",
            smoke_image=None,
        )


def _init_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def _copy_safety_files(destination: Path) -> None:
    for relative in (
        ".githooks/pre-push",
        "scripts/bootstrap_repository_safety.sh",
        "scripts/verify_repository_target.py",
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)


def _fake_gh_environment(tmp_path: Path) -> dict[str, str]:
    binary_directory = tmp_path / "fake-bin"
    binary_directory.mkdir()
    fake_gh = binary_directory / "gh"
    fake_gh.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$FAKE_GH_LOG"
if [ "$1 $2" = "repo set-default" ]; then
  if [ "${3:-}" = "--view" ]; then
    cat "$FAKE_GH_STATE"
  else
    printf '%s\\n' "$3" > "$FAKE_GH_STATE"
  fi
elif [ "$1 $2" = "pr create" ]; then
  printf '%s\\n' "https://github.com/harrisonoconnorhover/dander/pull/999"
elif [ "$1" = "api" ]; then
  printf '%s\\n' "$FAKE_PR_JSON"
else
  exit 2
fi
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    state = tmp_path / "fake-gh-state"
    state.write_text("", encoding="utf-8")
    log = tmp_path / "fake-gh-log"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{binary_directory}{os.pathsep}{environment['PATH']}",
            "FAKE_GH_STATE": str(state),
            "FAKE_GH_LOG": str(log),
            "FAKE_PR_JSON": "{}",
        }
    )
    return environment


def _git_config(repository: Path, key: str) -> str:
    return subprocess.run(
        ["git", "config", "--get", key],
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


def _git_remote_url(repository: Path, remote: str, *, push: bool = False) -> str:
    command = ["git", "remote", "get-url"]
    if push:
        command.append("--push")
    command.append(remote)
    return subprocess.run(
        command,
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
