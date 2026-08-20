#!/usr/bin/env python3
"""Fail closed unless a Git remote resolves to Dander's canonical writable fork."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

CANONICAL_REPOSITORY = "harrisonoconnorhover/dander"
GITHUB_HOST = "github.com"


class RepositoryTargetError(ValueError):
    """A remote URL or configured remote does not identify the writable fork."""


def normalize_repository_url(value: str) -> str:
    """Return a lower-case ``owner/repository`` for supported GitHub remote URLs."""
    remote_url = value.strip()
    if not remote_url:
        raise RepositoryTargetError("repository URL is empty")

    scp_match = re.fullmatch(
        r"(?P<user>[^@/:]+)@(?P<host>[^/:]+):(?P<path>.+)",
        remote_url,
    )
    if scp_match:
        if scp_match.group("user").lower() != "git":
            raise RepositoryTargetError("GitHub SSH remotes must use the git user")
        if scp_match.group("host").lower() != GITHUB_HOST:
            raise RepositoryTargetError("repository host must be github.com")
        path = scp_match.group("path")
    else:
        parsed = urlsplit(remote_url)
        path = _validated_url_path(parsed)

    repository_path = path.strip("/")
    if repository_path.lower().endswith(".git"):
        repository_path = repository_path[:-4]
    parts = repository_path.split("/")
    if len(parts) != 2 or not all(parts):
        raise RepositoryTargetError("repository URL must contain exactly owner/repository")
    return "/".join(part.lower() for part in parts)


def _validated_url_path(parsed: SplitResult) -> str:
    if parsed.query or parsed.fragment:
        raise RepositoryTargetError("repository URL must not contain a query or fragment")
    try:
        port = parsed.port
    except ValueError as error:
        raise RepositoryTargetError(f"repository URL has an invalid port: {error}") from error

    if parsed.scheme == "https":
        if parsed.username or parsed.password:
            raise RepositoryTargetError("HTTPS repository URLs must not contain credentials")
        if port not in (None, 443):
            raise RepositoryTargetError("HTTPS repository URL must use the default port")
    elif parsed.scheme == "ssh":
        if parsed.username != "git" or parsed.password:
            raise RepositoryTargetError("GitHub SSH remotes must use the git user")
        if port not in (None, 22):
            raise RepositoryTargetError("SSH repository URL must use the default port")
    else:
        raise RepositoryTargetError("repository URL must use HTTPS or SSH")

    if (parsed.hostname or "").lower() != GITHUB_HOST:
        raise RepositoryTargetError("repository host must be github.com")
    return parsed.path


def verify_repository_url(value: str) -> str:
    """Verify one URL and return the canonical repository name."""
    normalized = normalize_repository_url(value)
    if normalized != CANONICAL_REPOSITORY:
        raise RepositoryTargetError(
            f"repository target {normalized!r} is not writable; expected {CANONICAL_REPOSITORY!r}"
        )
    return normalized


def configured_remote_urls(
    repository_root: Path,
    remote: str,
    *,
    push: bool = False,
) -> tuple[str, ...]:
    """Return every configured fetch or push URL for a Git remote."""
    command = ["git", "-C", str(repository_root), "remote", "get-url"]
    if push:
        command.append("--push")
    command.extend(("--all", remote))
    result = subprocess.run(command, capture_output=True, check=False, text=True)
    urls = tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
    if result.returncode != 0 or not urls:
        detail = result.stderr.strip() or "remote has no configured URL"
        raise RepositoryTargetError(f"could not resolve remote {remote!r}: {detail}")
    return urls


def verify_remote(
    repository_root: Path,
    remote: str,
    *,
    push: bool = False,
) -> tuple[str, ...]:
    """Verify every URL configured for a named remote."""
    urls = configured_remote_urls(repository_root, remote, push=push)
    for url in urls:
        verify_repository_url(url)
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url", help="Remote URL to verify.")
    target.add_argument("--remote", help="Configured Git remote to verify.")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository containing the configured remote.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Verify push URLs instead of fetch URLs when using --remote.",
    )
    arguments = parser.parse_args()

    try:
        if arguments.url is not None:
            verify_repository_url(arguments.url)
        else:
            verify_remote(
                arguments.repository_root.resolve(),
                arguments.remote,
                push=arguments.push,
            )
    except RepositoryTargetError as error:
        raise SystemExit(f"Repository target rejected: {error}") from error
    print(f"Verified writable repository target: {CANONICAL_REPOSITORY}")


if __name__ == "__main__":
    main()
