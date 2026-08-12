"""Reviewed runtime and lifecycle-controller publication into OCI OCIR."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from contextlib import contextmanager
from email.parser import Parser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Protocol

from dander.bootstrap.project import (
    _ARTIFACT_SCHEMA,
    _DIGEST,
    _OCI_IMAGE,
    ProjectBootstrapError,
    _platform_manifests,
    _write_artifact_record,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_REGION = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-[1-9][0-9]*$")
_COMPARTMENT_OCID = re.compile(r"^ocid1\.compartment\.oc[0-9]+\.\.[A-Za-z0-9]+$")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_REPOSITORY = re.compile(r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_TAG_PREFIX = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_WHEEL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTROLLER_ARTIFACT_SCHEMA = "io.dander.oci.controller.artifact/v1"
_CONTROLLER_BUILD_FILES = (
    "dander/templates/project/infra/oci/controller/Dockerfile",
    "dander/templates/project/infra/oci/controller/func.py",
    "dander/templates/project/infra/oci/controller/requirements.txt",
)


class _Runner(Protocol):
    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]: ...


def _subprocess_runner(
    args: tuple[str, ...],
    *,
    cwd: Path,
    check: bool,
    capture_output: bool = False,
    text: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=text,
    )


def _oci_prefix(*, profile: str, region: str) -> tuple[str, ...]:
    return (
        "oci",
        "--profile",
        profile,
        "--auth",
        "security_token",
        "--region",
        region,
    )


def _verify_repository(
    *,
    runner: _Runner,
    cwd: Path,
    oci_prefix: tuple[str, ...],
    compartment_id: str,
    repository_name: str,
) -> bool:
    response = runner(
        (
            *oci_prefix,
            "artifacts",
            "container",
            "repository",
            "list",
            "--compartment-id",
            compartment_id,
            "--all",
            "--output",
            "json",
        ),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        document = json.loads(response.stdout)
        items = document["data"]["items"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ProjectBootstrapError("OCI returned invalid repository metadata") from error
    if not isinstance(items, list):
        raise ProjectBootstrapError("OCI returned invalid repository metadata")
    matches = [
        item
        for item in items
        if isinstance(item, dict) and item.get("display-name") == repository_name
    ]
    if len(matches) != 1:
        raise ProjectBootstrapError("The exact OCI runtime repository does not exist")
    repository_summary = matches[0]
    repository_id = repository_summary.get("id")
    if not isinstance(repository_id, str) or not repository_id:
        raise ProjectBootstrapError("OCI returned invalid repository metadata")
    response = runner(
        (
            *oci_prefix,
            "artifacts",
            "container",
            "repository",
            "get",
            "--repository-id",
            repository_id,
            "--output",
            "json",
        ),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        document = json.loads(response.stdout)
        repository = document["data"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ProjectBootstrapError("OCI returned invalid repository metadata") from error
    if not isinstance(repository, dict):
        raise ProjectBootstrapError("OCI returned invalid repository metadata")
    repository_immutable = repository.get("is-immutable")
    if not isinstance(repository_immutable, bool):
        raise ProjectBootstrapError("OCI returned invalid repository metadata")
    if (
        repository.get("id") != repository_id
        or repository.get("display-name") != repository_name
        or repository.get("is-public") is not False
        or repository.get("lifecycle-state") != "AVAILABLE"
    ):
        raise ProjectBootstrapError("OCI runtime repository must be private and available")
    return repository_immutable


def _registry_token(
    *,
    runner: _Runner,
    cwd: Path,
    oci_prefix: tuple[str, ...],
    host: str,
    namespace: str,
    repository_name: str,
) -> str:
    scope = f"repository:{namespace}/{repository_name}:pull,push"
    response = runner(
        (
            *oci_prefix,
            "container-registry",
            "access-token",
            "get",
            "--service",
            host,
            "--scope",
            scope,
            "--output",
            "json",
        ),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        document = json.loads(response.stdout)
        data = document["data"]
        token = data["token"]
        expires_in = data["expires-in"]
        actual_scope = data["scope"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ProjectBootstrapError("OCI returned an invalid registry access token") from error
    if (
        not isinstance(token, str)
        or not token
        or not isinstance(expires_in, int)
        or expires_in < 300
        or actual_scope != scope
    ):
        raise ProjectBootstrapError("OCI returned an invalid registry access token")
    return token


@contextmanager
def _temporary_docker_config(*, host: str, identity_token: str) -> Iterator[Path]:
    try:
        source_dir = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker"))
        source_path = source_dir / "config.json"
        if source_path.is_file():
            document = json.loads(source_path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ProjectBootstrapError("Docker configuration is not a JSON object")
        else:
            document = {}
        # Buildx keeps named-builder metadata below DOCKER_CONFIG. Copying a Docker
        # Desktop currentContext into this one-use directory selects metadata that
        # is intentionally absent, so registry-only imagetools operations must use
        # the context-independent default instead.
        document.pop("currentContext", None)
        source_plugin_dir = source_dir / "cli-plugins"
        if source_plugin_dir.is_dir():
            plugin_dirs = document.get("cliPluginsExtraDirs", [])
            if not isinstance(plugin_dirs, list) or not all(
                isinstance(path, str) for path in plugin_dirs
            ):
                raise ProjectBootstrapError("Docker configuration has invalid plugin paths")
            plugin_path = str(source_plugin_dir)
            document["cliPluginsExtraDirs"] = list(dict.fromkeys((*plugin_dirs, plugin_path)))
        auths = document.get("auths", {})
        if not isinstance(auths, dict):
            raise ProjectBootstrapError("Docker configuration has invalid registry entries")
        document["auths"] = {**auths, host: {"identitytoken": identity_token}}
        with TemporaryDirectory(prefix="dander-ocir-") as directory:
            config_dir = Path(directory)
            config_path = config_dir / "config.json"
            config_path.write_text(
                json.dumps(document, separators=(",", ":")),
                encoding="utf-8",
            )
            config_path.chmod(0o600)
            yield config_dir
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectBootstrapError(
            "Could not create an isolated Docker configuration for OCIR"
        ) from error


def _inspect(
    *,
    runner: _Runner,
    cwd: Path,
    image: str,
    docker_config: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    prefix: tuple[str, ...] = ("docker",)
    if docker_config is not None:
        prefix = ("docker", "--config", str(docker_config))
    return runner(
        (*prefix, "buildx", "imagetools", "inspect", "--raw", image),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class OciRuntimeImagePromoter:
    """Copy one accepted OCI index into OCIR without rebuilding or static credentials."""

    def __init__(self, project_dir: Path, *, runner: _Runner | None = None) -> None:
        self._project_dir = project_dir.resolve()
        self._runner = runner or _subprocess_runner
        self._artifact_record_path: Path | None = None

    @property
    def artifact_record_path(self) -> Path | None:
        """Return the OCI artifact record written by the latest successful promotion."""
        return self._artifact_record_path

    def promote(
        self,
        *,
        source_image: str,
        compartment_id: str,
        region: str,
        namespace: str,
        repository_name: str,
        oci_profile: str = "DEFAULT",
        tag_prefix: str = "promoted",
    ) -> str:
        """Promote an accepted image with a scoped token and verify identical OCI content."""
        self._artifact_record_path = None
        source_match = _OCI_IMAGE.fullmatch(source_image)
        if source_match is None:
            raise ProjectBootstrapError("Source image must be an immutable OCI digest reference")
        if _COMPARTMENT_OCID.fullmatch(compartment_id) is None:
            raise ProjectBootstrapError("Invalid OCI image-promotion compartment")
        if _REGION.fullmatch(region) is None:
            raise ProjectBootstrapError("Invalid OCI image-promotion region")
        if _NAMESPACE.fullmatch(namespace) is None:
            raise ProjectBootstrapError("Invalid OCI registry namespace")
        if _REPOSITORY.fullmatch(repository_name) is None:
            raise ProjectBootstrapError("Invalid OCI registry repository name")
        if _PROFILE.fullmatch(oci_profile) is None:
            raise ProjectBootstrapError("Invalid OCI SecurityToken profile")
        if _TAG_PREFIX.fullmatch(tag_prefix) is None:
            raise ProjectBootstrapError("Invalid OCI image-promotion tag prefix")

        source_record = self._source_record(source_image=source_image)
        expected_digest = source_match.group("digest")
        expected_platforms = source_record["platform_manifests"]
        assert isinstance(expected_platforms, dict)
        source_raw = _inspect(
            runner=self._runner,
            cwd=self._project_dir,
            image=source_image,
        ).stdout
        if _platform_manifests(source_raw, default_digest=expected_digest) != expected_platforms:
            raise ProjectBootstrapError(
                "Source registry content no longer matches the accepted artifact record"
            )

        host = f"ocir.{region}.oci.oraclecloud.com"
        destination_repository = f"{host}/{namespace}/{repository_name}"
        tag = f"{tag_prefix}-{expected_digest.removeprefix('sha256:')[:12]}"
        tagged_destination = f"{destination_repository}:{tag}"
        image_uri = f"{namespace}/{repository_name}:{tag}"
        oci_prefix = _oci_prefix(profile=oci_profile, region=region)
        lookup = (
            *oci_prefix,
            "artifacts",
            "container",
            "image",
            "lookup",
            "--image-uri",
            image_uri,
            "--query",
            "data.digest",
            "--raw-output",
        )

        try:
            repository_immutable = _verify_repository(
                runner=self._runner,
                cwd=self._project_dir,
                oci_prefix=oci_prefix,
                compartment_id=compartment_id,
                repository_name=repository_name,
            )
            existing = self._runner(
                lookup,
                cwd=self._project_dir,
                check=False,
                capture_output=True,
                text=True,
            )
            created = existing.returncode != 0
            token = _registry_token(
                runner=self._runner,
                cwd=self._project_dir,
                oci_prefix=oci_prefix,
                host=host,
                namespace=namespace,
                repository_name=repository_name,
            )
            with _temporary_docker_config(host=host, identity_token=token) as config_dir:
                if created:
                    self._runner(
                        (
                            "docker",
                            "--config",
                            str(config_dir),
                            "buildx",
                            "imagetools",
                            "create",
                            "--prefer-index=false",
                            "--tag",
                            tagged_destination,
                            source_image,
                        ),
                        cwd=self._project_dir,
                        check=True,
                    )
                    destination_digest = self._runner(
                        lookup,
                        cwd=self._project_dir,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                else:
                    destination_digest = existing.stdout.strip()

                if destination_digest != expected_digest:
                    if created:
                        raise ProjectBootstrapError(
                            "OCIR rewrote the OCI index; the promoted artifact is not "
                            "byte-identical"
                        )
                    raise ProjectBootstrapError(
                        "Existing OCIR tag does not match the accepted OCI index"
                    )
                immutable_destination = f"{destination_repository}@{destination_digest}"
                destination_raw = _inspect(
                    runner=self._runner,
                    cwd=self._project_dir,
                    image=immutable_destination,
                    docker_config=config_dir,
                ).stdout
                if (
                    _platform_manifests(
                        destination_raw,
                        default_digest=destination_digest,
                    )
                    != expected_platforms
                ):
                    raise ProjectBootstrapError(
                        "OCIR platform manifests differ from the accepted source-free artifact"
                    )
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise ProjectBootstrapError("Could not promote the runtime image into OCIR") from error

        record_path = self._project_dir / ".dander" / "runtime-artifact-oci.json"
        try:
            _write_artifact_record(
                record_path,
                {
                    **source_record,
                    "image": immutable_destination,
                    "tagged_image": tagged_destination,
                    "source_image": source_image,
                    "promotion": "registry-copy",
                    "authentication": "SecurityToken-scoped-access-token",
                    "repository_tag_immutability": (
                        "enabled" if repository_immutable else "provider-unavailable"
                    ),
                    "deployment_reference": "digest",
                },
            )
        except OSError as error:
            raise ProjectBootstrapError(
                "Runtime image was promoted but its OCI artifact record could not be written"
            ) from error
        self._artifact_record_path = record_path
        return immutable_destination

    def _source_record(self, *, source_image: str) -> dict[str, object]:
        source_record_path = self._project_dir / ".dander" / "runtime-artifact.json"
        try:
            source_record = json.loads(source_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectBootstrapError(
                "A valid source-free runtime artifact record is required before OCI promotion"
            ) from error
        source_match = _OCI_IMAGE.fullmatch(source_image)
        assert source_match is not None
        expected_digest = source_match.group("digest")
        if not isinstance(source_record, dict):
            raise ProjectBootstrapError(
                "Source image does not match its accepted runtime artifact record"
            )
        expected_platforms = source_record.get("platform_manifests")
        if (
            source_record.get("schema") != _ARTIFACT_SCHEMA
            or source_record.get("image") != source_image
            or source_record.get("index_digest") != expected_digest
            or not isinstance(expected_platforms, dict)
            or not expected_platforms
            or any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or _DIGEST.fullmatch(value) is None
                for key, value in expected_platforms.items()
            )
        ):
            raise ProjectBootstrapError(
                "Source image does not match its accepted runtime artifact record"
            )
        return source_record


class OciControllerImagePublisher:
    """Build one OCI Functions controller solely from an exact reviewed wheel."""

    def __init__(self, project_dir: Path, *, runner: _Runner | None = None) -> None:
        self._project_dir = project_dir.resolve()
        self._runner = runner or _subprocess_runner
        self._artifact_record_path: Path | None = None

    @property
    def artifact_record_path(self) -> Path | None:
        """Return the controller artifact record written by the latest publication."""
        return self._artifact_record_path

    def publish(
        self,
        *,
        wheel: Path,
        wheel_sha256: str,
        compartment_id: str,
        region: str,
        namespace: str,
        repository_name: str,
        oci_profile: str = "DEFAULT",
    ) -> str:
        """Publish an amd64 controller with a wheel-bound tag and scoped registry token."""
        self._artifact_record_path = None
        wheel_path = wheel.expanduser().resolve()
        if _WHEEL_SHA256.fullmatch(wheel_sha256) is None:
            raise ProjectBootstrapError("Invalid reviewed wheel SHA-256")
        if _COMPARTMENT_OCID.fullmatch(compartment_id) is None:
            raise ProjectBootstrapError("Invalid OCI controller-publication compartment")
        if _REGION.fullmatch(region) is None:
            raise ProjectBootstrapError("Invalid OCI controller-publication region")
        if _NAMESPACE.fullmatch(namespace) is None:
            raise ProjectBootstrapError("Invalid OCI registry namespace")
        if _REPOSITORY.fullmatch(repository_name) is None:
            raise ProjectBootstrapError("Invalid OCI registry repository name")
        if _PROFILE.fullmatch(oci_profile) is None:
            raise ProjectBootstrapError("Invalid OCI SecurityToken profile")

        version = self._verify_wheel(wheel_path=wheel_path, wheel_sha256=wheel_sha256)
        host = f"ocir.{region}.oci.oraclecloud.com"
        destination_repository = f"{host}/{namespace}/{repository_name}"
        tag = f"controller-{wheel_sha256[:12]}"
        tagged_destination = f"{destination_repository}:{tag}"
        image_uri = f"{namespace}/{repository_name}:{tag}"
        oci_prefix = _oci_prefix(profile=oci_profile, region=region)
        lookup = (
            *oci_prefix,
            "artifacts",
            "container",
            "image",
            "lookup",
            "--image-uri",
            image_uri,
            "--query",
            "data.digest",
            "--raw-output",
        )

        try:
            repository_immutable = _verify_repository(
                runner=self._runner,
                cwd=self._project_dir,
                oci_prefix=oci_prefix,
                compartment_id=compartment_id,
                repository_name=repository_name,
            )
            existing = self._runner(
                lookup,
                cwd=self._project_dir,
                check=False,
                capture_output=True,
                text=True,
            )
            created = existing.returncode != 0
            if created:
                destination_digest = ""
            else:
                destination_digest = existing.stdout.strip()
                self._require_matching_record(
                    wheel_sha256=wheel_sha256,
                    tagged_image=tagged_destination,
                    digest=destination_digest,
                )

            token = _registry_token(
                runner=self._runner,
                cwd=self._project_dir,
                oci_prefix=oci_prefix,
                host=host,
                namespace=namespace,
                repository_name=repository_name,
            )
            with _temporary_docker_config(host=host, identity_token=token) as config_dir:
                if created:
                    with TemporaryDirectory(prefix="dander-oci-controller-") as directory:
                        context_dir = Path(directory)
                        self._extract_controller_context(
                            wheel_path=wheel_path,
                            context_dir=context_dir,
                        )
                        self._runner(
                            (
                                "docker",
                                "--config",
                                str(config_dir),
                                "buildx",
                                "build",
                                "--platform",
                                "linux/amd64",
                                "--file",
                                "infra/oci/controller/Dockerfile",
                                "--build-arg",
                                "DANDER_WHEEL=dander-controller.whl",
                                "--provenance=false",
                                "--sbom=false",
                                "--push",
                                "--tag",
                                tagged_destination,
                                ".",
                            ),
                            cwd=context_dir,
                            check=True,
                        )
                    destination_digest = self._runner(
                        lookup,
                        cwd=self._project_dir,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                if _DIGEST.fullmatch(destination_digest) is None:
                    raise ProjectBootstrapError("OCIR returned an invalid controller image digest")
                immutable_destination = f"{destination_repository}@{destination_digest}"
                raw = _inspect(
                    runner=self._runner,
                    cwd=self._project_dir,
                    image=immutable_destination,
                    docker_config=config_dir,
                ).stdout
                platforms = _platform_manifests(raw, default_digest=destination_digest)
                if set(platforms) != {"linux/amd64"}:
                    raise ProjectBootstrapError(
                        "OCI controller image must contain only the linux/amd64 runtime platform"
                    )
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise ProjectBootstrapError("Could not publish the OCI lifecycle controller") from error

        record_path = self._project_dir / ".dander" / "oci-controller-artifact.json"
        try:
            _write_artifact_record(
                record_path,
                {
                    "schema": _CONTROLLER_ARTIFACT_SCHEMA,
                    "image": immutable_destination,
                    "tagged_image": tagged_destination,
                    "image_digest": destination_digest,
                    "platform_manifests": platforms,
                    "wheel": wheel_path.name,
                    "wheel_sha256": wheel_sha256,
                    "package": "dander-platform",
                    "version": version,
                    "build": "exact-reviewed-wheel",
                    "authentication": "SecurityToken-scoped-access-token",
                    "repository_tag_immutability": (
                        "enabled" if repository_immutable else "provider-unavailable"
                    ),
                    "deployment_reference": "tag-and-digest",
                },
            )
        except OSError as error:
            raise ProjectBootstrapError(
                "OCI controller was published but its artifact record could not be written"
            ) from error
        self._artifact_record_path = record_path
        return immutable_destination

    def _verify_wheel(self, *, wheel_path: Path, wheel_sha256: str) -> str:
        if not wheel_path.is_file() or not wheel_path.name.startswith("dander_platform-"):
            raise ProjectBootstrapError("The reviewed Dander wheel does not exist")
        hasher = hashlib.sha256()
        try:
            with wheel_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
        except OSError as error:
            raise ProjectBootstrapError("Could not read the reviewed Dander wheel") from error
        if hasher.hexdigest() != wheel_sha256:
            raise ProjectBootstrapError("The Dander wheel does not match the reviewed SHA-256")

        try:
            with zipfile.ZipFile(wheel_path) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                if len(names) != len(set(names)) or any(
                    name.startswith("/") or "\\" in name or ".." in Path(name).parts
                    for name in names
                ):
                    raise ProjectBootstrapError("The reviewed Dander wheel has unsafe paths")
                for required in _CONTROLLER_BUILD_FILES:
                    try:
                        info = archive.getinfo(required)
                    except KeyError as error:
                        raise ProjectBootstrapError(
                            "The reviewed Dander wheel is missing OCI controller build files"
                        ) from error
                    if info.is_dir():
                        raise ProjectBootstrapError(
                            "The reviewed Dander wheel has invalid OCI controller build files"
                        )
                metadata_names = [
                    name
                    for name in names
                    if re.fullmatch(r"dander_platform-[^/]+\.dist-info/METADATA", name)
                ]
                if len(metadata_names) != 1:
                    raise ProjectBootstrapError("The reviewed Dander wheel has invalid metadata")
                metadata = Parser().parsestr(
                    archive.read(metadata_names[0]).decode("utf-8", errors="strict")
                )
        except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as error:
            raise ProjectBootstrapError("The reviewed Dander wheel is invalid") from error
        version = metadata.get("Version", "")
        if metadata.get("Name") != "dander-platform" or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9.!+-]*", version
        ):
            raise ProjectBootstrapError("The reviewed Dander wheel has invalid metadata")
        return version

    def _extract_controller_context(self, *, wheel_path: Path, context_dir: Path) -> None:
        try:
            with zipfile.ZipFile(wheel_path) as archive:
                for source in _CONTROLLER_BUILD_FILES:
                    relative = source.removeprefix("dander/templates/project/")
                    destination = context_dir / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(source))
            shutil.copyfile(wheel_path, context_dir / "dander-controller.whl")
        except (KeyError, OSError, zipfile.BadZipFile) as error:
            raise ProjectBootstrapError(
                "Could not create the reviewed OCI controller build context"
            ) from error

    def _require_matching_record(
        self,
        *,
        wheel_sha256: str,
        tagged_image: str,
        digest: str,
    ) -> None:
        record_path = self._project_dir / ".dander" / "oci-controller-artifact.json"
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectBootstrapError(
                "Existing immutable OCI controller tag has no matching local artifact record"
            ) from error
        if (
            not isinstance(record, dict)
            or record.get("schema") != _CONTROLLER_ARTIFACT_SCHEMA
            or record.get("wheel_sha256") != wheel_sha256
            or record.get("tagged_image") != tagged_image
            or record.get("image_digest") != digest
            or record.get("image") != tagged_image.rsplit(":", 1)[0] + f"@{digest}"
        ):
            raise ProjectBootstrapError(
                "Existing immutable OCI controller tag does not match the reviewed wheel"
            )


__all__ = ["OciControllerImagePublisher", "OciRuntimeImagePromoter"]
