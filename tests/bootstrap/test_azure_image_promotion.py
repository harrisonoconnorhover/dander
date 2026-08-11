"""Registry-copy promotion coverage for accepted Azure runtime images."""

from __future__ import annotations

import hashlib
import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from dander.bootstrap import AzureRuntimeImagePromoter, ProjectBootstrapError

if TYPE_CHECKING:
    from pathlib import Path

_AMD64_DIGEST = "sha256:" + "b" * 64
_ARM64_DIGEST = "sha256:" + "c" * 64
_SUBSCRIPTION_ID = "11111111-1111-4111-8111-111111111111"


def _index(*, arm64_digest: str = _ARM64_DIGEST) -> str:
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": _AMD64_DIGEST,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
                {
                    "digest": arm64_digest,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
            ],
        },
        separators=(",", ":"),
    )


_INDEX = _index()
_INDEX_DIGEST = "sha256:" + hashlib.sha256(_INDEX.encode()).hexdigest()
_SOURCE_IMAGE = f"us-central1-docker.pkg.dev/unit-project/dander/dander@{_INDEX_DIGEST}"


def _write_source_record(project_dir: Path, *, image: str = _SOURCE_IMAGE) -> None:
    artifact_dir = project_dir / ".dander"
    artifact_dir.mkdir()
    (artifact_dir / "runtime-artifact.json").write_text(
        json.dumps(
            {
                "schema": "io.dander.runtime.artifact/v1",
                "image": image,
                "tagged_image": "source:accepted",
                "index_digest": _INDEX_DIGEST,
                "platform_manifests": {
                    "linux/amd64": _AMD64_DIGEST,
                    "linux/arm64": _ARM64_DIGEST,
                },
                "source": "https://github.com/harrisonoconnorhover/dander",
                "revision": "unit",
                "created": "2026-08-11T00:00:00Z",
                "sbom_attached": True,
                "provenance_attached": True,
            }
        ),
        encoding="utf-8",
    )


class _Runner:
    def __init__(
        self,
        *,
        existing: bool = False,
        destination_digest: str = _INDEX_DIGEST,
        destination_raw: str = _INDEX,
    ) -> None:
        self.existing = existing
        self.destination_digest = destination_digest
        self.destination_raw = destination_raw
        self.created = False
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text
        assert cwd.is_absolute()
        self.commands.append(args)
        if args[:3] == ("az", "acr", "show"):
            return subprocess.CompletedProcess(args, 0, stdout="danderphase6.azurecr.io\n")
        if args[:3] == ("az", "acr", "login"):
            return subprocess.CompletedProcess(args, 0, stdout="")
        if args[:4] == ("az", "acr", "repository", "show"):
            if not self.existing and not self.created:
                assert not check
                return subprocess.CompletedProcess(args, 1, stdout="")
            return subprocess.CompletedProcess(args, 0, stdout=self.destination_digest + "\n")
        if args[:4] == ("docker", "buildx", "imagetools", "create"):
            self.created = True
            return subprocess.CompletedProcess(args, 0, stdout="")
        if args[:4] == ("docker", "buildx", "imagetools", "inspect"):
            image = args[-1]
            if image == _SOURCE_IMAGE:
                return subprocess.CompletedProcess(args, 0, stdout=_INDEX)
            return subprocess.CompletedProcess(args, 0, stdout=self.destination_raw)
        raise AssertionError(f"Unexpected command: {args}")


def test_azure_promoter_copies_and_verifies_the_exact_accepted_index(tmp_path: Path) -> None:
    _write_source_record(tmp_path)
    runner = _Runner()
    promoter = AzureRuntimeImagePromoter(tmp_path, runner=runner)

    image = promoter.promote(
        source_image=_SOURCE_IMAGE,
        subscription_id=_SUBSCRIPTION_ID,
        acr_name="danderphase6",
        repository_name="dander/runtime",
    )

    assert image == f"danderphase6.azurecr.io/dander/runtime@{_INDEX_DIGEST}"
    create = next(
        command
        for command in runner.commands
        if command[:4] == ("docker", "buildx", "imagetools", "create")
    )
    assert "--prefer-index=false" in create
    assert create[-1] == _SOURCE_IMAGE
    assert all(command[:3] != ("docker", "buildx", "build") for command in runner.commands)
    assert all("password" not in " ".join(command).lower() for command in runner.commands)
    assert promoter.artifact_record_path == tmp_path / ".dander" / "runtime-artifact-azure.json"
    record = json.loads(promoter.artifact_record_path.read_text(encoding="utf-8"))
    assert record["source_image"] == _SOURCE_IMAGE
    assert record["image"] == image
    assert record["promotion"] == "registry-copy"


def test_azure_promoter_is_idempotent_for_an_existing_identical_tag(tmp_path: Path) -> None:
    _write_source_record(tmp_path)
    runner = _Runner(existing=True)

    image = AzureRuntimeImagePromoter(tmp_path, runner=runner).promote(
        source_image=_SOURCE_IMAGE,
        subscription_id=_SUBSCRIPTION_ID,
        acr_name="danderphase6",
        repository_name="dander/runtime",
    )

    assert image.endswith("@" + _INDEX_DIGEST)
    assert not any(
        command[:4] == ("docker", "buildx", "imagetools", "create") for command in runner.commands
    )


def test_azure_promoter_rejects_registry_digest_rewrites(tmp_path: Path) -> None:
    _write_source_record(tmp_path)

    with pytest.raises(ProjectBootstrapError, match="rewrote the OCI index"):
        AzureRuntimeImagePromoter(
            tmp_path,
            runner=_Runner(destination_digest="sha256:" + "d" * 64),
        ).promote(
            source_image=_SOURCE_IMAGE,
            subscription_id=_SUBSCRIPTION_ID,
            acr_name="danderphase6",
            repository_name="dander/runtime",
        )


def test_azure_promoter_rejects_platform_manifest_drift(tmp_path: Path) -> None:
    _write_source_record(tmp_path)

    with pytest.raises(ProjectBootstrapError, match="platform manifests differ"):
        AzureRuntimeImagePromoter(
            tmp_path,
            runner=_Runner(destination_raw=_index(arm64_digest="sha256:" + "e" * 64)),
        ).promote(
            source_image=_SOURCE_IMAGE,
            subscription_id=_SUBSCRIPTION_ID,
            acr_name="danderphase6",
            repository_name="dander/runtime",
        )


def test_azure_promoter_rejects_source_record_mismatch_before_provider_access(
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path, image="registry.example.invalid/dander@" + _INDEX_DIGEST)
    runner = _Runner()

    with pytest.raises(ProjectBootstrapError, match="does not match"):
        AzureRuntimeImagePromoter(tmp_path, runner=runner).promote(
            source_image=_SOURCE_IMAGE,
            subscription_id=_SUBSCRIPTION_ID,
            acr_name="danderphase6",
            repository_name="dander/runtime",
        )

    assert runner.commands == []
