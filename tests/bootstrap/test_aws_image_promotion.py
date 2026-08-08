"""Registry-copy promotion coverage for accepted source-free runtime images."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from dander.bootstrap import ProjectBootstrapError, RuntimeImagePromoter

if TYPE_CHECKING:
    from pathlib import Path

_INDEX_DIGEST = "sha256:" + "a" * 64
_AMD64_DIGEST = "sha256:" + "b" * 64
_ARM64_DIGEST = "sha256:" + "c" * 64
_SOURCE_IMAGE = f"us-central1-docker.pkg.dev/unit-project/dander/dander@{_INDEX_DIGEST}"


def _index() -> str:
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
                    "digest": _ARM64_DIGEST,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
            ],
        }
    )


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
                "created": "2026-08-08T00:00:00Z",
                "sbom_attached": True,
                "provenance_attached": True,
            }
        ),
        encoding="utf-8",
    )


class _Runner:
    def __init__(self, *, destination_digest: str = _INDEX_DIGEST) -> None:
        self.destination_digest = destination_digest
        self.commands: list[tuple[str, ...]] = []
        self.inputs: list[str | None] = []
        self.describe_image_calls = 0

    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text
        assert cwd.is_absolute()
        assert check or "describe-images" in args
        self.commands.append(args)
        self.inputs.append(input)
        if "describe-repositories" in args:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="184463061564.dkr.ecr.us-east-1.amazonaws.com/dander\n",
            )
        if "get-login-password" in args:
            return subprocess.CompletedProcess(args, 0, stdout="temporary-password\n")
        if "describe-images" in args:
            self.describe_image_calls += 1
            if self.describe_image_calls == 1:
                return subprocess.CompletedProcess(args, 254, stdout="")
            return subprocess.CompletedProcess(args, 0, stdout=self.destination_digest + "\n")
        if args[:4] == ("docker", "buildx", "imagetools", "inspect"):
            return subprocess.CompletedProcess(args, 0, stdout=_index())
        return subprocess.CompletedProcess(args, 0, stdout="")


def test_runtime_image_promoter_copies_and_verifies_the_accepted_index(tmp_path: Path) -> None:
    _write_source_record(tmp_path)
    runner = _Runner()
    promoter = RuntimeImagePromoter(tmp_path, runner=runner)

    image = promoter.promote(
        source_image=_SOURCE_IMAGE,
        aws_account_id="184463061564",
        region="us-east-1",
        repository_name="dander",
        aws_profile="dander-deploy",
    )

    assert image == f"184463061564.dkr.ecr.us-east-1.amazonaws.com/dander@{_INDEX_DIGEST}"
    create = next(
        command
        for command in runner.commands
        if command[:4] == ("docker", "buildx", "imagetools", "create")
    )
    assert "--prefer-index=false" in create
    assert create[-1] == _SOURCE_IMAGE
    login_index = next(
        index for index, command in enumerate(runner.commands) if command[:2] == ("docker", "login")
    )
    assert runner.inputs[login_index] == "temporary-password"
    assert all("temporary-password" not in part for command in runner.commands for part in command)
    assert all(command[:3] != ("docker", "buildx", "build") for command in runner.commands)
    assert promoter.artifact_record_path == tmp_path / ".dander" / "runtime-artifact-aws.json"
    record = json.loads(promoter.artifact_record_path.read_text(encoding="utf-8"))
    assert record["source_image"] == _SOURCE_IMAGE
    assert record["image"] == image
    assert record["promotion"] == "registry-copy"


def test_runtime_image_promoter_rejects_registry_digest_rewrites(tmp_path: Path) -> None:
    _write_source_record(tmp_path)

    with pytest.raises(ProjectBootstrapError, match="rewrote the OCI index"):
        RuntimeImagePromoter(
            tmp_path,
            runner=_Runner(destination_digest="sha256:" + "d" * 64),
        ).promote(
            source_image=_SOURCE_IMAGE,
            aws_account_id="184463061564",
            region="us-east-1",
            repository_name="dander",
        )


def test_runtime_image_promoter_is_idempotent_for_an_existing_identical_tag(
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path)
    runner = _Runner()
    runner.describe_image_calls = 1

    image = RuntimeImagePromoter(tmp_path, runner=runner).promote(
        source_image=_SOURCE_IMAGE,
        aws_account_id="184463061564",
        region="us-east-1",
        repository_name="dander",
    )

    assert image.endswith("@" + _INDEX_DIGEST)
    assert not any(
        command[:4] == ("docker", "buildx", "imagetools", "create") for command in runner.commands
    )


def test_runtime_image_promoter_rejects_a_source_record_mismatch_before_copy(
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path, image="registry.example.invalid/dander@" + _INDEX_DIGEST)
    runner = _Runner()

    with pytest.raises(ProjectBootstrapError, match="does not match"):
        RuntimeImagePromoter(tmp_path, runner=runner).promote(
            source_image=_SOURCE_IMAGE,
            aws_account_id="184463061564",
            region="us-east-1",
            repository_name="dander",
        )

    assert runner.commands == []
