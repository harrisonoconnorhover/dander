"""Digest-preserving promotion of accepted runtime images into Azure ACR."""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from dander.bootstrap.project import (
    _ARTIFACT_SCHEMA,
    _DIGEST,
    _OCI_IMAGE,
    ProjectBootstrapError,
    _platform_manifests,
    _write_artifact_record,
)

if TYPE_CHECKING:
    from pathlib import Path

_ACR_NAME = re.compile(r"^[a-z][a-z0-9]{4,49}$")
_ACR_REPOSITORY = re.compile(r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
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


class AzureRuntimeImagePromoter:
    """Copy one accepted OCI index into ACR without rebuilding it."""

    def __init__(self, project_dir: Path, *, runner: _Runner | None = None) -> None:
        self._project_dir = project_dir.resolve()
        self._runner = runner or _subprocess_runner
        self._artifact_record_path: Path | None = None

    @property
    def artifact_record_path(self) -> Path | None:
        """Return the Azure artifact record written by the latest successful promotion."""
        return self._artifact_record_path

    def promote(
        self,
        *,
        source_image: str,
        subscription_id: str,
        acr_name: str,
        repository_name: str,
        tag_prefix: str = "promoted",
    ) -> str:
        """Promote an accepted image and require byte-identical OCI content in ACR."""
        self._artifact_record_path = None
        source_match = _OCI_IMAGE.fullmatch(source_image)
        if source_match is None:
            raise ProjectBootstrapError("Source image must be an immutable OCI digest reference")
        _validate_subscription_id(subscription_id)
        if _ACR_NAME.fullmatch(acr_name) is None:
            raise ProjectBootstrapError("Invalid Azure Container Registry name")
        if _ACR_REPOSITORY.fullmatch(repository_name) is None:
            raise ProjectBootstrapError("Invalid Azure Container Registry repository name")
        if _TAG_PREFIX.fullmatch(tag_prefix) is None:
            raise ProjectBootstrapError("Invalid Azure image-promotion tag prefix")

        source_record = self._source_record(source_image=source_image)
        expected_digest = source_match.group("digest")
        expected_platforms = source_record["platform_manifests"]
        assert isinstance(expected_platforms, dict)
        host = f"{acr_name}.azurecr.io"
        destination_repository = f"{host}/{repository_name}"
        tag = f"{tag_prefix}-{expected_digest.removeprefix('sha256:')[:12]}"
        tagged_destination = f"{destination_repository}:{tag}"

        try:
            login_server = self._runner(
                (
                    "az",
                    "acr",
                    "show",
                    "--name",
                    acr_name,
                    "--subscription",
                    subscription_id,
                    "--query",
                    "loginServer",
                    "--output",
                    "tsv",
                    "--only-show-errors",
                ),
                cwd=self._project_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if login_server != host:
                raise ProjectBootstrapError("Azure returned an unexpected ACR login server")
            self._runner(
                (
                    "az",
                    "acr",
                    "login",
                    "--name",
                    acr_name,
                    "--subscription",
                    subscription_id,
                    "--only-show-errors",
                ),
                cwd=self._project_dir,
                check=True,
            )
            source_raw = self._inspect(source_image, check=True).stdout
            if (
                _platform_manifests(source_raw, default_digest=expected_digest)
                != expected_platforms
            ):
                raise ProjectBootstrapError(
                    "Source registry content no longer matches the accepted artifact record"
                )

            describe_destination = (
                "az",
                "acr",
                "repository",
                "show",
                "--name",
                acr_name,
                "--image",
                f"{repository_name}:{tag}",
                "--subscription",
                subscription_id,
                "--query",
                "digest",
                "--output",
                "tsv",
                "--only-show-errors",
            )
            existing = self._runner(
                describe_destination,
                cwd=self._project_dir,
                check=False,
                capture_output=True,
                text=True,
            )
            created = existing.returncode != 0
            if created:
                self._runner(
                    (
                        "docker",
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
                    describe_destination,
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
                        "ACR rewrote the OCI index; the promoted artifact is not byte-identical"
                    )
                raise ProjectBootstrapError(
                    "Existing immutable ACR tag does not match the accepted OCI index"
                )
            immutable_destination = f"{destination_repository}@{destination_digest}"
            immutable_raw = self._inspect(immutable_destination, check=True).stdout
            if (
                _platform_manifests(immutable_raw, default_digest=destination_digest)
                != expected_platforms
            ):
                raise ProjectBootstrapError(
                    "ACR platform manifests differ from the accepted source-free artifact"
                )
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise ProjectBootstrapError("Could not promote the runtime image into ACR") from error

        record_path = self._project_dir / ".dander" / "runtime-artifact-azure.json"
        try:
            _write_artifact_record(
                record_path,
                {
                    **source_record,
                    "image": immutable_destination,
                    "tagged_image": tagged_destination,
                    "source_image": source_image,
                    "promotion": "registry-copy",
                },
            )
        except OSError as error:
            raise ProjectBootstrapError(
                "Runtime image was promoted but its Azure artifact record could not be written"
            ) from error
        self._artifact_record_path = record_path
        return immutable_destination

    def _source_record(self, *, source_image: str) -> dict[str, object]:
        source_record_path = self._project_dir / ".dander" / "runtime-artifact.json"
        try:
            source_record = json.loads(source_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectBootstrapError(
                "A valid source-free runtime artifact record is required before Azure promotion"
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

    def _inspect(self, image: str, *, check: bool) -> subprocess.CompletedProcess[str]:
        return self._runner(
            ("docker", "buildx", "imagetools", "inspect", "--raw", image),
            cwd=self._project_dir,
            check=check,
            capture_output=True,
            text=True,
        )


def _validate_subscription_id(subscription_id: str) -> None:
    try:
        parsed = UUID(subscription_id)
    except ValueError as error:
        raise ProjectBootstrapError("Invalid Azure subscription id") from error
    if parsed.variant != "specified in RFC 4122":
        raise ProjectBootstrapError("Invalid Azure subscription id")


__all__ = ["AzureRuntimeImagePromoter"]
