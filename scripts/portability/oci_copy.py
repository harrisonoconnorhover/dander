"""Copy one OCI index between registries and prove that no rebuild occurred."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_REFERENCE = re.compile(r"^(?P<repository>[^\s@]+)@(?P<digest>sha256:[0-9a-f]{64})$")
_TAG_REFERENCE = re.compile(r"^[^\s@:]+(?:[.:][^\s@/:]+)*(?:/[^\s@/:]+)+:[^\s@/:]+$")
_REQUIRED_PLATFORMS = ("linux/amd64", "linux/arm64")
_RECORD_SCHEMA = "io.dander.portability.oci-copy/v1"


class CopyVerificationError(RuntimeError):
    """Raised when an OCI copy cannot be proven content-identical."""


class Runner(Protocol):
    def __call__(self, command: Sequence[str]) -> bytes:
        """Run one command and return stdout bytes."""


def _run(command: Sequence[str]) -> bytes:
    try:
        result = subprocess.run(
            tuple(command),
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        executable = command[0] if command else "registry tool"
        raise CopyVerificationError(f"{executable} could not complete the OCI copy") from error
    return result.stdout


def _read_digest(reference: str, runner: Runner) -> str:
    digest = runner(("crane", "digest", reference)).decode("utf-8").strip()
    if not _DIGEST.fullmatch(digest):
        raise CopyVerificationError(f"Registry returned an invalid digest for {reference!r}")
    return digest


def _platform_manifests(raw: bytes) -> dict[str, str]:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CopyVerificationError("Registry returned an invalid OCI manifest") from error
    if not isinstance(document, dict) or not isinstance(document.get("manifests"), list):
        raise CopyVerificationError("Expected a multi-platform OCI index")

    platforms: dict[str, str] = {}
    for descriptor in document["manifests"]:
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("platform"), dict):
            raise CopyVerificationError("OCI index contains an invalid descriptor")
        platform = descriptor["platform"]
        operating_system = platform.get("os")
        architecture = platform.get("architecture")
        if operating_system == "unknown" and architecture == "unknown":
            continue
        digest = descriptor.get("digest")
        if (
            not isinstance(operating_system, str)
            or not isinstance(architecture, str)
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
        ):
            raise CopyVerificationError("OCI index contains an invalid platform manifest")
        variant = platform.get("variant")
        key = f"{operating_system}/{architecture}"
        if isinstance(variant, str) and variant:
            key = f"{key}/{variant}"
        if key in platforms:
            raise CopyVerificationError(f"OCI index repeats platform {key!r}")
        platforms[key] = digest

    missing = tuple(
        required
        for required in _REQUIRED_PLATFORMS
        if not any(actual == required or actual.startswith(f"{required}/") for actual in platforms)
    )
    if missing:
        raise CopyVerificationError(
            "OCI index is missing required platform manifests: " + ", ".join(missing)
        )
    return dict(sorted(platforms.items()))


def copy_and_verify(
    *,
    source: str,
    destination: str,
    runner: Runner = _run,
) -> dict[str, object]:
    """Copy ``source`` to ``destination`` and return a sanitized verification record."""
    source_match = _DIGEST_REFERENCE.fullmatch(source)
    if source_match is None:
        raise CopyVerificationError("Source must be an immutable digest-qualified image reference")
    if _TAG_REFERENCE.fullmatch(destination) is None:
        raise CopyVerificationError("Destination must be a tagged registry image reference")

    expected_digest = source_match.group("digest")
    source_digest = _read_digest(source, runner)
    if source_digest != expected_digest:
        raise CopyVerificationError("Source registry digest does not match the requested image")
    source_manifest = runner(("crane", "manifest", source))
    source_platforms = _platform_manifests(source_manifest)

    runner(("crane", "copy", source, destination))

    destination_digest = _read_digest(destination, runner)
    destination_manifest = runner(("crane", "manifest", destination))
    destination_platforms = _platform_manifests(destination_manifest)
    if destination_digest != source_digest:
        raise CopyVerificationError("Destination registry rewrote the OCI index digest")
    if hashlib.sha256(destination_manifest).digest() != hashlib.sha256(source_manifest).digest():
        raise CopyVerificationError("Destination registry changed the OCI index document")
    if destination_platforms != source_platforms:
        raise CopyVerificationError("Destination platform manifests differ from the source")

    return {
        "schema": _RECORD_SCHEMA,
        "source": source,
        "destination": destination.rsplit(":", 1)[0] + f"@{destination_digest}",
        "index_digest": source_digest,
        "platform_manifests": source_platforms,
        "source_manifest_sha256": hashlib.sha256(source_manifest).hexdigest(),
        "destination_manifest_sha256": hashlib.sha256(destination_manifest).hexdigest(),
        "copied_without_rebuild": True,
    }


def _write_record(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()
    try:
        record = copy_and_verify(source=args.source, destination=args.destination)
        _write_record(args.record, record)
    except CopyVerificationError as error:
        print(f"OCI copy verification failed: {error}")
        return 1
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
