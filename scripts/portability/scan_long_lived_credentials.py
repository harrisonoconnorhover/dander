"""Fail when Phase 1B proof artifacts contain recognizable long-lived cloud credentials."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_PATTERNS = {
    "aws_static_access_key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "aws_temporary_access_key": re.compile(rb"\bASIA[0-9A-Z]{16}\b"),
    "aws_secret_key_value": re.compile(
        rb"(?i)(?:aws_)?secret_access_key\s*[\"']?\s*[:=]\s*[\"'][A-Za-z0-9/+=]{40}[\"']"
    ),
    "gcp_private_key": re.compile(
        rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"
        rb"(?:\\n|\r?\n)"
        rb"(?:[A-Za-z0-9+/=]{20,}(?:\\n|\r?\n)){2,}"
        rb"-----END (?:RSA |EC )?PRIVATE KEY-----"
    ),
}


def scan(paths: list[Path]) -> list[dict[str, str]]:
    """Return sanitized path/pattern findings without returning matched material."""
    findings: list[dict[str, str]] = []
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(candidate for candidate in path.rglob("*") if candidate.is_file())
        elif path.is_file():
            files.append(path)
    for path in sorted(set(files)):
        try:
            content = path.read_bytes()
        except OSError:
            findings.append({"path": str(path), "pattern": "unreadable"})
            continue
        for name, pattern in _PATTERNS.items():
            if pattern.search(content):
                findings.append({"path": str(path), "pattern": name})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    findings = scan([path.resolve() for path in args.paths])
    print(
        json.dumps(
            {"schema": "io.dander.portability.credential-scan/v1", "findings": findings},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
