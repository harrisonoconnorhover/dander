"""Scoped-token registry-copy coverage for accepted OCI runtime images."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from dander.bootstrap import OciRuntimeImagePromoter, ProjectBootstrapError

_AMD64_DIGEST = "sha256:" + "b" * 64
_ARM64_DIGEST = "sha256:" + "c" * 64
_COMPARTMENT_ID = "ocid1.compartment.oc1.." + "a" * 32
_TOKEN = "unit-scoped-registry-token"


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
                "created": "2026-08-12T00:00:00Z",
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
        expires_in: int = 900,
        repository_private: bool = True,
        repository_immutable: bool = False,
    ) -> None:
        self.existing = existing
        self.destination_digest = destination_digest
        self.destination_raw = destination_raw
        self.expires_in = expires_in
        self.repository_private = repository_private
        self.repository_immutable = repository_immutable
        self.created = False
        self.commands: list[tuple[str, ...]] = []
        self.config_paths: list[Path] = []

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
        if args[:4] == ("docker", "buildx", "imagetools", "inspect"):
            assert args[-1] == _SOURCE_IMAGE
            return subprocess.CompletedProcess(args, 0, stdout=_INDEX)
        if args[0] == "oci" and "repository" in args and "list" in args:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "items": [
                                {
                                    "display-name": "dander/runtime",
                                    "is-immutable": self.repository_immutable,
                                    "is-public": not self.repository_private,
                                    "lifecycle-state": "AVAILABLE",
                                }
                            ]
                        }
                    }
                ),
            )
        if args[0] == "oci" and "lookup" in args:
            if not self.existing and not self.created:
                assert not check
                return subprocess.CompletedProcess(args, 1, stdout="")
            return subprocess.CompletedProcess(args, 0, stdout=self.destination_digest + "\n")
        if args[0] == "oci" and "access-token" in args:
            scope = "repository:unitnamespace/dander/runtime:pull,push"
            assert args[args.index("--scope") + 1] == scope
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "token": _TOKEN,
                            "expires-in": self.expires_in,
                            "scope": scope,
                        }
                    }
                ),
            )
        if args[:2] == ("docker", "--config"):
            config_path = Path(args[2]) / "config.json"
            self.config_paths.append(config_path)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            assert config["auths"]["ocir.us-ashburn-1.oci.oraclecloud.com"] == {
                "identitytoken": _TOKEN
            }
            assert config["credHelpers"] == {"us-central1-docker.pkg.dev": "gcloud"}
            if "create" in args:
                self.created = True
                return subprocess.CompletedProcess(args, 0, stdout="")
            if "inspect" in args:
                return subprocess.CompletedProcess(args, 0, stdout=self.destination_raw)
        raise AssertionError(f"Unexpected command: {args}")


def _docker_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "docker"
    directory.mkdir()
    (directory / "config.json").write_text(
        json.dumps({"credHelpers": {"us-central1-docker.pkg.dev": "gcloud"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(directory))
    return directory


def _promote(project_dir: Path, runner: _Runner) -> tuple[OciRuntimeImagePromoter, str]:
    promoter = OciRuntimeImagePromoter(project_dir, runner=runner)
    image = promoter.promote(
        source_image=_SOURCE_IMAGE,
        compartment_id=_COMPARTMENT_ID,
        region="us-ashburn-1",
        namespace="unitnamespace",
        repository_name="dander/runtime",
        oci_profile="DANDER",
    )
    return promoter, image


def test_oci_promoter_copies_and_verifies_with_only_a_scoped_temporary_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path)
    _docker_config(monkeypatch, tmp_path)
    runner = _Runner()

    promoter, image = _promote(tmp_path, runner)

    assert image == (
        f"ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/dander/runtime@{_INDEX_DIGEST}"
    )
    create = next(command for command in runner.commands if "create" in command)
    assert "--prefer-index=false" in create
    assert create[-1] == _SOURCE_IMAGE
    assert all("build" not in command for command in runner.commands)
    assert all(_TOKEN not in command for command in runner.commands)
    assert any(
        "--auth" in command and command[command.index("--auth") + 1] == "security_token"
        for command in runner.commands
        if command[0] == "oci"
    )
    assert runner.config_paths
    assert all(not path.exists() for path in runner.config_paths)
    assert promoter.artifact_record_path == tmp_path / ".dander" / "runtime-artifact-oci.json"
    record = json.loads(promoter.artifact_record_path.read_text(encoding="utf-8"))
    assert record["source_image"] == _SOURCE_IMAGE
    assert record["image"] == image
    assert record["promotion"] == "registry-copy"
    assert record["authentication"] == "SecurityToken-scoped-access-token"
    assert record["repository_tag_immutability"] == "provider-unavailable"
    assert record["deployment_reference"] == "digest"


def test_oci_promoter_is_idempotent_for_an_existing_identical_tag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path)
    _docker_config(monkeypatch, tmp_path)
    runner = _Runner(existing=True)

    _, image = _promote(tmp_path, runner)

    assert image.endswith("@" + _INDEX_DIGEST)
    assert not any("create" in command for command in runner.commands)


def test_oci_promoter_rejects_registry_digest_rewrites(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path)
    _docker_config(monkeypatch, tmp_path)

    with pytest.raises(ProjectBootstrapError, match="rewrote the OCI index"):
        _promote(
            tmp_path,
            _Runner(destination_digest="sha256:" + "d" * 64),
        )


def test_oci_promoter_rejects_platform_manifest_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path)
    _docker_config(monkeypatch, tmp_path)

    with pytest.raises(ProjectBootstrapError, match="platform manifests differ"):
        _promote(
            tmp_path,
            _Runner(destination_raw=_index(arm64_digest="sha256:" + "e" * 64)),
        )


def test_oci_promoter_rejects_short_lived_or_malformed_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path)
    _docker_config(monkeypatch, tmp_path)

    with pytest.raises(ProjectBootstrapError, match="invalid registry access token"):
        _promote(tmp_path, _Runner(expires_in=60))


def test_oci_promoter_requires_the_reviewed_private_available_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path)
    _docker_config(monkeypatch, tmp_path)

    with pytest.raises(ProjectBootstrapError, match="private and available"):
        _promote(tmp_path, _Runner(repository_private=False))


def test_oci_promoter_records_repository_immutability_when_provider_supports_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path)
    _docker_config(monkeypatch, tmp_path)

    promoter, _ = _promote(tmp_path, _Runner(repository_immutable=True))

    assert promoter.artifact_record_path is not None
    record = json.loads(promoter.artifact_record_path.read_text(encoding="utf-8"))
    assert record["repository_tag_immutability"] == "enabled"


def test_oci_promoter_rejects_source_record_mismatch_before_provider_access(
    tmp_path: Path,
) -> None:
    _write_source_record(tmp_path, image="registry.example.invalid/dander@" + _INDEX_DIGEST)
    runner = _Runner()

    with pytest.raises(ProjectBootstrapError, match="does not match"):
        _promote(tmp_path, runner)

    assert runner.commands == []
