"""Fail when Phase 1B proof artifacts contain recognizable long-lived cloud credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Set

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

# Public boto3/botocore example documents contain AWS's published placeholder keys.  Accept only
# these exact dependency-file contents; a dependency update or modified file is scanned normally.
_PUBLIC_FIXTURE_SHA256 = frozenset(
    {
        "2beb01599c682e30010991eba8066ce7b71879fc0f9838abd37a66e8f13f9a1e",
        "4f912aac51590625652fd76c37e4f90e78a05355273125df555c012b4d005ab5",
        "c83fc27073767fdb7d3e5190e4dcce25a09871c7b118fa289db056d93e0e31c9",
    }
)


def scan(
    paths: list[Path],
    *,
    public_fixture_sha256: Set[str] = _PUBLIC_FIXTURE_SHA256,
) -> list[dict[str, str]]:
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
        if hashlib.sha256(content).hexdigest() in public_fixture_sha256:
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
