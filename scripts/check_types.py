#!/usr/bin/env python3
"""Run Dander's canonical strict type check in its locked environment."""

from __future__ import annotations

import subprocess
import sys

_MYPY_COMMAND = (
    "uv",
    "run",
    "--isolated",
    "--frozen",
    "--extra",
    "dev",
    "--extra",
    "postgres",
    "mypy",
)


def main() -> int:
    """Return mypy's exit status for the repository-configured target set."""
    return subprocess.run(_MYPY_COMMAND, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
