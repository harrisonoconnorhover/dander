"""Digest-preserving promotion of accepted runtime images into OCI OCIR."""

from __future__ import annotations

import json
import os
import re
import subprocess
from contextlib import contextmanager
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
        source_raw = self._inspect(source_image).stdout
        if _platform_manifests(source_raw, default_digest=expected_digest) != expected_platforms:
            raise ProjectBootstrapError(
                "Source registry content no longer matches the accepted artifact record"
            )

        host = f"ocir.{region}.oci.oraclecloud.com"
        destination_repository = f"{host}/{namespace}/{repository_name}"
        tag = f"{tag_prefix}-{expected_digest.removeprefix('sha256:')[:12]}"
        tagged_destination = f"{destination_repository}:{tag}"
        image_uri = f"{namespace}/{repository_name}:{tag}"
        oci_prefix = (
            "oci",
            "--profile",
            oci_profile,
            "--auth",
            "security_token",
            "--region",
            region,
        )
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
            self._verify_repository(
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
            token = self._registry_token(
                oci_prefix=oci_prefix,
                host=host,
                namespace=namespace,
                repository_name=repository_name,
            )
            with self._temporary_docker_config(host=host, identity_token=token) as config_dir:
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
                        "Existing immutable OCIR tag does not match the accepted OCI index"
                    )
                immutable_destination = f"{destination_repository}@{destination_digest}"
                destination_raw = self._inspect(
                    immutable_destination,
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

    def _verify_repository(
        self,
        *,
        oci_prefix: tuple[str, ...],
        compartment_id: str,
        repository_name: str,
    ) -> None:
        response = self._runner(
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
            cwd=self._project_dir,
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
        repository = matches[0]
        if (
            repository.get("is-immutable") is not True
            or repository.get("is-public") is not False
            or repository.get("lifecycle-state") != "AVAILABLE"
        ):
            raise ProjectBootstrapError(
                "OCI runtime repository must be private, immutable, and available"
            )

    def _registry_token(
        self,
        *,
        oci_prefix: tuple[str, ...],
        host: str,
        namespace: str,
        repository_name: str,
    ) -> str:
        scope = f"repository:{namespace}/{repository_name}:pull,push"
        response = self._runner(
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
            cwd=self._project_dir,
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
    def _temporary_docker_config(self, *, host: str, identity_token: str) -> Iterator[Path]:
        try:
            source_dir = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker"))
            source_path = source_dir / "config.json"
            if source_path.is_file():
                document = json.loads(source_path.read_text(encoding="utf-8"))
                if not isinstance(document, dict):
                    raise ProjectBootstrapError("Docker configuration is not a JSON object")
            else:
                document = {}
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
        self,
        image: str,
        *,
        docker_config: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        prefix: tuple[str, ...] = ("docker",)
        if docker_config is not None:
            prefix = ("docker", "--config", str(docker_config))
        return self._runner(
            (*prefix, "buildx", "imagetools", "inspect", "--raw", image),
            cwd=self._project_dir,
            check=True,
            capture_output=True,
            text=True,
        )


__all__ = ["OciRuntimeImagePromoter"]
