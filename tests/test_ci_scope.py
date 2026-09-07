"""CI selection must see removed runtime files, not only surviving documents."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from scripts.check_ci_scope import changed_paths, classify_ci_scope

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ([], "full"),
        (["HANDOFF.md", "docs/support-status.md", "infra/README.md"], "documentation"),
        (["src/dander/templates/project/README.md"], "full"),
        (["src/dander/core/config.py", "HANDOFF.md"], "full"),
        (["tests/test_ci_scope.py"], "full"),
        ([".github/workflows/ci.yml"], "full"),
        (["docs/evidence/phase8/date/rc32-redshift-objective.json", "README.md"], "objective"),
        (["docs/evidence/phase8/date/other-objective.json"], "full"),
        (["scripts/benchmarks/redshift_bulk_phase8.py", "docs/ci.md"], "benchmark"),
    ],
)
def test_scope(paths: list[str], expected: str) -> None:
    assert classify_ci_scope(paths) == expected


@pytest.mark.parametrize("move_to_docs", [False, True])
def test_git_diff_keeps_deleted_runtime_paths(tmp_path: Path, move_to_docs: bool) -> None:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init")
    git("config", "user.name", "CI scope test")
    git("config", "user.email", "ci@example.invalid")
    source = tmp_path / "src/dander/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("example = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "initial")
    base = git("rev-parse", "HEAD")
    if move_to_docs:
        destination = tmp_path / "docs/example.md"
        destination.parent.mkdir()
        source.rename(destination)
    else:
        source.unlink()
    (tmp_path / "HANDOFF.md").write_text("Updated\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "remove runtime file")

    paths = changed_paths(base, "HEAD", repository=tmp_path)
    assert "src/dander/example.py" in paths
    assert "HANDOFF.md" in paths
    if move_to_docs:
        assert "docs/example.md" in paths
    assert classify_ci_scope(paths) == "full"
