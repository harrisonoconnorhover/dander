"""Exact-wheel OCI lifecycle-controller publication contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from dander.bootstrap import OciControllerImagePublisher, ProjectBootstrapError

_COMPARTMENT_ID = "ocid1.compartment.oc1.." + "a" * 32
_DESTINATION_DIGEST = "sha256:" + "d" * 64
_TOKEN = "unit-controller-registry-token"
_MANIFEST = json.dumps(
    {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": "sha256:" + "e" * 64},
        "layers": [],
    }
)
_ASSETS = {
    "dander/templates/project/infra/oci/controller/Dockerfile": (
        "ARG DANDER_WHEEL\nCOPY ${DANDER_WHEEL} /tmp/dander.whl\n"
    ),
    "dander/templates/project/infra/oci/controller/func.py": "from dander import __version__\n",
    "dander/templates/project/infra/oci/controller/requirements.txt": "oci==2.184.1\n",
}


def _write_wheel(project_dir: Path, *, missing: str = "") -> tuple[Path, str]:
    wheel = project_dir / "dander_platform-0.9.0rc2-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, contents in _ASSETS.items():
            if name != missing:
                archive.writestr(name, contents)
        archive.writestr(
            "dander_platform-0.9.0rc2.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: dander-platform\nVersion: 0.9.0rc2\n",
        )
    return wheel, hashlib.sha256(wheel.read_bytes()).hexdigest()


def _docker_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    directory = tmp_path / "docker"
    directory.mkdir()
    (directory / "config.json").write_text(
        json.dumps({"credHelpers": {"us-central1-docker.pkg.dev": "gcloud"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(directory))


class _Runner:
    def __init__(
        self,
        project_dir: Path,
        *,
        existing: bool = False,
        manifest: str = _MANIFEST,
        fail_build: bool = False,
    ) -> None:
        self.project_dir = project_dir
        self.existing = existing
        self.manifest = manifest
        self.fail_build = fail_build
        self.created = False
        self.commands: list[tuple[str, ...]] = []
        self.config_paths: list[Path] = []
        self.context_paths: list[Path] = []
        self.wheel_sha256 = ""

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
        self.commands.append(args)
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
                                    "is-immutable": False,
                                    "is-public": False,
                                    "lifecycle-state": "AVAILABLE",
                                }
                            ]
                        }
                    }
                ),
            )
        if args[0] == "oci" and "lookup" in args:
            if not self.existing and not self.created:
                assert check is False
                return subprocess.CompletedProcess(args, 1, stdout="")
            return subprocess.CompletedProcess(args, 0, stdout=_DESTINATION_DIGEST + "\n")
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
                            "expires-in": 900,
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
            if "build" in args:
                assert cwd != self.project_dir
                self.context_paths.append(cwd)
                assert not (cwd / "src").exists()
                assert (cwd / "infra/oci/controller/Dockerfile").read_text() == _ASSETS[
                    "dander/templates/project/infra/oci/controller/Dockerfile"
                ]
                assert (cwd / "infra/oci/controller/func.py").read_text() == _ASSETS[
                    "dander/templates/project/infra/oci/controller/func.py"
                ]
                self.wheel_sha256 = hashlib.sha256(
                    (cwd / "dander-controller.whl").read_bytes()
                ).hexdigest()
                if self.fail_build:
                    raise subprocess.CalledProcessError(1, args)
                self.created = True
                return subprocess.CompletedProcess(args, 0, stdout="")
            if "inspect" in args:
                return subprocess.CompletedProcess(args, 0, stdout=self.manifest)
        raise AssertionError(f"Unexpected command: {args}")


def _publish(
    project_dir: Path,
    runner: _Runner,
    wheel: Path,
    wheel_sha256: str,
) -> tuple[OciControllerImagePublisher, str]:
    publisher = OciControllerImagePublisher(project_dir, runner=runner)
    image = publisher.publish(
        wheel=wheel,
        wheel_sha256=wheel_sha256,
        compartment_id=_COMPARTMENT_ID,
        region="us-ashburn-1",
        namespace="unitnamespace",
        repository_name="dander/runtime",
        oci_profile="DANDER",
    )
    return publisher, image


def test_controller_is_built_only_from_the_exact_reviewed_wheel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wheel, wheel_sha256 = _write_wheel(tmp_path)
    _docker_config(monkeypatch, tmp_path)
    runner = _Runner(tmp_path)

    publisher, image = _publish(tmp_path, runner, wheel, wheel_sha256)

    repository = "ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/dander/runtime"
    assert image == f"{repository}@{_DESTINATION_DIGEST}"
    build = next(command for command in runner.commands if "build" in command)
    assert build[build.index("--platform") + 1] == "linux/amd64"
    assert "--provenance=false" in build
    assert "--sbom=false" in build
    assert build[build.index("--tag") + 1] == f"{repository}:controller-{wheel_sha256[:12]}"
    assert runner.wheel_sha256 == wheel_sha256
    assert all(_TOKEN not in command for command in runner.commands)
    assert runner.context_paths and all(not path.exists() for path in runner.context_paths)
    assert runner.config_paths and all(not path.exists() for path in runner.config_paths)
    assert publisher.artifact_record_path == tmp_path / ".dander/oci-controller-artifact.json"
    record = json.loads(publisher.artifact_record_path.read_text(encoding="utf-8"))
    assert record["wheel_sha256"] == wheel_sha256
    assert record["version"] == "0.9.0rc2"
    assert record["platform_manifests"] == {"linux/amd64": _DESTINATION_DIGEST}
    assert record["repository_tag_immutability"] == "provider-unavailable"
    assert record["deployment_reference"] == "tag-and-digest"
    assert _TOKEN not in json.dumps(record)


def test_existing_controller_tag_requires_an_exact_local_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wheel, wheel_sha256 = _write_wheel(tmp_path)
    _docker_config(monkeypatch, tmp_path)
    runner = _Runner(tmp_path, existing=True)
    repository = "ocir.us-ashburn-1.oci.oraclecloud.com/unitnamespace/dander/runtime"
    artifact_dir = tmp_path / ".dander"
    artifact_dir.mkdir()
    (artifact_dir / "oci-controller-artifact.json").write_text(
        json.dumps(
            {
                "schema": "io.dander.oci.controller.artifact/v1",
                "image": f"{repository}@{_DESTINATION_DIGEST}",
                "tagged_image": f"{repository}:controller-{wheel_sha256[:12]}",
                "image_digest": _DESTINATION_DIGEST,
                "wheel_sha256": wheel_sha256,
            }
        ),
        encoding="utf-8",
    )

    _, image = _publish(tmp_path, runner, wheel, wheel_sha256)

    assert image == f"{repository}@{_DESTINATION_DIGEST}"
    assert not any("build" in command for command in runner.commands)


def test_existing_controller_tag_without_binding_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wheel, wheel_sha256 = _write_wheel(tmp_path)
    _docker_config(monkeypatch, tmp_path)
    runner = _Runner(tmp_path, existing=True)

    with pytest.raises(ProjectBootstrapError, match="no matching local artifact record"):
        _publish(tmp_path, runner, wheel, wheel_sha256)

    assert not any("access-token" in command for command in runner.commands)


def test_controller_rejects_wrong_wheel_hash_before_provider_access(tmp_path: Path) -> None:
    wheel, _ = _write_wheel(tmp_path)
    runner = _Runner(tmp_path)

    with pytest.raises(ProjectBootstrapError, match="does not match the reviewed SHA-256"):
        _publish(tmp_path, runner, wheel, "0" * 64)

    assert runner.commands == []


def test_controller_rejects_incomplete_wheel_before_provider_access(tmp_path: Path) -> None:
    wheel, wheel_sha256 = _write_wheel(
        tmp_path,
        missing="dander/templates/project/infra/oci/controller/func.py",
    )
    runner = _Runner(tmp_path)

    with pytest.raises(ProjectBootstrapError, match="missing OCI controller build files"):
        _publish(tmp_path, runner, wheel, wheel_sha256)

    assert runner.commands == []


def test_controller_rejects_non_amd64_registry_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wheel, wheel_sha256 = _write_wheel(tmp_path)
    _docker_config(monkeypatch, tmp_path)
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": "sha256:" + "f" * 64,
                    "platform": {"os": "linux", "architecture": "arm64"},
                }
            ],
        }
    )

    with pytest.raises(ProjectBootstrapError, match="only the linux/amd64"):
        _publish(tmp_path, _Runner(tmp_path, manifest=manifest), wheel, wheel_sha256)


def test_temporary_wheel_context_and_token_are_removed_after_build_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wheel, wheel_sha256 = _write_wheel(tmp_path)
    _docker_config(monkeypatch, tmp_path)
    runner = _Runner(tmp_path, fail_build=True)

    with pytest.raises(ProjectBootstrapError, match="Could not publish"):
        _publish(tmp_path, runner, wheel, wheel_sha256)

    assert runner.context_paths and all(not path.exists() for path in runner.context_paths)
    assert runner.config_paths and all(not path.exists() for path in runner.config_paths)
