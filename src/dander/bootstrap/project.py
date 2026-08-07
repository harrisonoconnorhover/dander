"""Batteries-included project bootstrap helpers used by ``dander init``."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_REGION = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
_ACCOUNT = re.compile(r"^[^\s@]+@[^\s@]+$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLATFORM_PART = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_ARTIFACT_SCHEMA = "io.dander.runtime.artifact/v1"
_SOURCE_URL = "https://github.com/harrisonoconnorhover/dander"
_RUNTIME_PLATFORMS = ("linux/amd64", "linux/arm64")


class ProjectBootstrapError(RuntimeError):
    """Raised when the automatic state/image bootstrap cannot complete safely."""


class _Runner(Protocol):
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
        """Execute one argument-vector command."""


def _subprocess_runner(
    args: tuple[str, ...],
    *,
    cwd: Path,
    check: bool,
    capture_output: bool = False,
    text: bool = False,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=text,
        input=input,
    )


class StateBucketBootstrap:
    """Create the one stage-zero GCS backend that Terraform cannot create inside itself."""

    def __init__(self, *, cwd: Path, runner: _Runner | None = None) -> None:
        self._cwd = cwd.resolve()
        self._runner = runner or _subprocess_runner

    def ensure(
        self,
        *,
        project: str,
        bucket: str,
        location: str,
        apply: bool,
    ) -> bool:
        """Ensure a hardened, versioned backend exists; return whether it was created."""
        if not _PROJECT_ID.fullmatch(project):
            raise ProjectBootstrapError("Invalid GCP project id")
        if not _BUCKET_NAME.fullmatch(bucket):
            raise ProjectBootstrapError("Invalid Terraform state bucket")
        described = self._runner(
            (
                "gcloud",
                "storage",
                "buckets",
                "describe",
                f"gs://{bucket}",
                f"--project={project}",
            ),
            cwd=self._cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        if described.returncode == 0:
            return False
        if not apply:
            raise ProjectBootstrapError(
                "The Terraform state bucket does not exist; rerun dander init with --apply"
            )
        try:
            self._runner(
                (
                    "gcloud",
                    "services",
                    "enable",
                    "serviceusage.googleapis.com",
                    "storage.googleapis.com",
                    f"--project={project}",
                    "--quiet",
                ),
                cwd=self._cwd,
                check=True,
            )
            self._runner(
                (
                    "gcloud",
                    "storage",
                    "buckets",
                    "create",
                    f"gs://{bucket}",
                    f"--project={project}",
                    f"--location={location}",
                    "--uniform-bucket-level-access",
                    "--public-access-prevention",
                    "--quiet",
                ),
                cwd=self._cwd,
                check=True,
            )
            self._runner(
                (
                    "gcloud",
                    "storage",
                    "buckets",
                    "update",
                    f"gs://{bucket}",
                    "--versioning",
                    "--update-labels=managed-by=dander,purpose=terraform-state",
                    "--quiet",
                ),
                cwd=self._cwd,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise ProjectBootstrapError("Could not create the Terraform state bucket") from error
        return True


class RuntimeImagePublisher:
    """Build the current Dander project and return its immutable Artifact Registry reference."""

    def __init__(self, repository_dir: Path, *, runner: _Runner | None = None) -> None:
        self._repository_dir = repository_dir.resolve()
        self._runner = runner or _subprocess_runner
        self._artifact_record_path: Path | None = None

    @property
    def artifact_record_path(self) -> Path | None:
        """Return the local artifact record written by the latest successful publication."""
        return self._artifact_record_path

    def publish(
        self,
        *,
        project: str,
        region: str,
        tag_prefix: str = "init",
        impersonate_service_account: str = "",
        require_source_free: bool = False,
    ) -> str:
        self._artifact_record_path = None
        if not _PROJECT_ID.fullmatch(project) or not _REGION.fullmatch(region):
            raise ProjectBootstrapError("Invalid runtime image project or region")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", tag_prefix):
            raise ProjectBootstrapError("Invalid runtime image tag prefix")
        if impersonate_service_account and not _ACCOUNT.fullmatch(impersonate_service_account):
            raise ProjectBootstrapError("Invalid runtime image impersonation account")
        if require_source_free and (self._repository_dir / "src").exists():
            raise ProjectBootstrapError(
                "image-publish requires a generated source-free project without a src directory"
            )
        host = f"{region}-docker.pkg.dev"
        repository = f"{host}/{project}/dander/dander"
        revision = self._content_digest()
        created = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        tag = f"{tag_prefix}-{revision[:12]}"
        tagged_image = f"{repository}:{tag}"
        try:
            if impersonate_service_account:
                token_result = self._runner(
                    (
                        "gcloud",
                        "auth",
                        "print-access-token",
                        f"--impersonate-service-account={impersonate_service_account}",
                        f"--project={project}",
                    ),
                    cwd=self._repository_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                token = token_result.stdout.strip()
                if not token:
                    raise ProjectBootstrapError(
                        "Bootstrap impersonation returned an empty Artifact Registry token"
                    )
                self._runner(
                    (
                        "docker",
                        "login",
                        host,
                        "--username",
                        "oauth2accesstoken",
                        "--password-stdin",
                    ),
                    cwd=self._repository_dir,
                    check=True,
                    text=True,
                    input=token,
                )
            else:
                self._runner(
                    ("gcloud", "auth", "configure-docker", host, "--quiet"),
                    cwd=self._repository_dir,
                    check=True,
                )
            self._runner(
                (
                    "docker",
                    "buildx",
                    "build",
                    "--platform",
                    ",".join(_RUNTIME_PLATFORMS),
                    "--build-arg",
                    f"DANDER_BUILD_REVISION={revision}",
                    "--build-arg",
                    f"DANDER_BUILD_CREATED={created}",
                    "--sbom=true",
                    "--provenance=mode=max",
                    "--push",
                    "-t",
                    tagged_image,
                    ".",
                ),
                cwd=self._repository_dir,
                check=True,
            )
            describe_command: tuple[str, ...] = (
                "gcloud",
                "artifacts",
                "docker",
                "images",
                "describe",
                tagged_image,
                "--format=value(image_summary.digest)",
            )
            if impersonate_service_account:
                describe_command = (
                    *describe_command,
                    f"--impersonate-service-account={impersonate_service_account}",
                )
            described = self._runner(
                describe_command,
                cwd=self._repository_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise ProjectBootstrapError("Could not publish the Dander runtime image") from error
        digest = described.stdout.strip()
        if not _DIGEST.fullmatch(digest):
            raise ProjectBootstrapError("Artifact Registry returned an invalid image digest")
        immutable_image = f"{repository}@{digest}"
        try:
            inspected = self._runner(
                ("docker", "buildx", "imagetools", "inspect", "--raw", tagged_image),
                cwd=self._repository_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            platforms = _platform_manifests(inspected.stdout, default_digest=digest)
            missing_platforms = tuple(
                required
                for required in _RUNTIME_PLATFORMS
                if not any(
                    actual == required or actual.startswith(f"{required}/") for actual in platforms
                )
            )
            if missing_platforms:
                raise ProjectBootstrapError(
                    "Runtime image index is missing required platform manifests: "
                    + ", ".join(missing_platforms)
                )
            record_path = self._repository_dir / ".dander" / "runtime-artifact.json"
            _write_artifact_record(
                record_path,
                {
                    "schema": _ARTIFACT_SCHEMA,
                    "image": immutable_image,
                    "tagged_image": tagged_image,
                    "index_digest": digest,
                    "platform_manifests": platforms,
                    "source": _SOURCE_URL,
                    "revision": revision,
                    "created": created,
                    "sbom_attached": True,
                    "provenance_attached": True,
                },
            )
        except (OSError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
            raise ProjectBootstrapError(
                "Runtime image was pushed but its artifact record could not be verified"
            ) from error
        self._artifact_record_path = record_path
        return immutable_image

    def _content_digest(self) -> str:
        hasher = hashlib.sha256()
        required = (
            self._repository_dir / "Dockerfile",
            self._repository_dir / "dander.yaml",
            self._repository_dir / "connectors",
            self._repository_dir / "models",
        )
        if any(not path.exists() for path in required):
            raise ProjectBootstrapError("Runtime build context is incomplete")
        roots = tuple(
            path
            for path in (
                self._repository_dir / "src",
                self._repository_dir / "connectors",
                self._repository_dir / "graphs",
                self._repository_dir / "models",
                self._repository_dir / "infra",
            )
            if path.is_dir()
        )
        files = [
            self._repository_dir / name
            for name in (
                ".dockerignore",
                "Dockerfile",
                "README.md",
                "dander.yaml",
                "pyproject.toml",
                "uv.lock",
            )
            if (self._repository_dir / name).is_file()
        ]
        files.extend(_explicit_docker_context_files(self._repository_dir))
        for root in roots:
            files.extend(
                path for path in root.rglob("*") if path.is_file() and _is_build_context_file(path)
            )
        try:
            for path in sorted(set(files)):
                hasher.update(str(path.relative_to(self._repository_dir)).encode())
                hasher.update(path.read_bytes())
        except OSError as error:
            raise ProjectBootstrapError("Could not hash the runtime build context") from error
        return hasher.hexdigest()


def _is_build_context_file(path: Path) -> bool:
    if any(part in {".terraform", "__pycache__"} for part in path.parts):
        return False
    name = path.name
    return not (
        name == ".DS_Store"
        or name.endswith(".tfplan")
        or ".tfstate" in name
        or (name.endswith(".tfvars") and not name.endswith(".tfvars.example"))
    )


def _explicit_docker_context_files(repository_dir: Path) -> tuple[Path, ...]:
    dockerignore = repository_dir / ".dockerignore"
    if not dockerignore.is_file():
        return ()
    try:
        lines = dockerignore.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ProjectBootstrapError("Could not read the Docker build-context rules") from error
    files: list[Path] = []
    for raw in lines:
        rule = raw.strip()
        if not rule.startswith("!") or any(character in rule for character in "*?["):
            continue
        relative = rule[1:].lstrip("/")
        path = Path(relative)
        if not relative or relative.endswith("/") or ".." in path.parts:
            continue
        candidate = repository_dir / path
        if candidate.is_file() and _is_build_context_file(candidate):
            files.append(candidate)
    return tuple(files)


def _platform_manifests(raw: str, *, default_digest: str) -> dict[str, str]:
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ProjectBootstrapError("Runtime image manifest is not an OCI document")
    descriptors = document.get("manifests")
    if descriptors is None:
        if not _DIGEST.fullmatch(default_digest):
            raise ProjectBootstrapError("Runtime image manifest has an invalid digest")
        return {"linux/amd64": default_digest}
    if not isinstance(descriptors, list):
        raise ProjectBootstrapError("Runtime image index has invalid platform manifests")

    platforms: dict[str, str] = {}
    for descriptor in descriptors:
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("platform"), dict):
            raise ProjectBootstrapError("Runtime image index has an invalid descriptor")
        platform = descriptor["platform"]
        operating_system = platform.get("os")
        architecture = platform.get("architecture")
        if operating_system == "unknown" and architecture == "unknown":
            continue
        digest = descriptor.get("digest")
        if (
            not isinstance(operating_system, str)
            or not _PLATFORM_PART.fullmatch(operating_system)
            or not isinstance(architecture, str)
            or not _PLATFORM_PART.fullmatch(architecture)
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
        ):
            raise ProjectBootstrapError("Runtime image index has an invalid platform descriptor")
        variant = platform.get("variant")
        if variant is not None and (
            not isinstance(variant, str) or not _PLATFORM_PART.fullmatch(variant)
        ):
            raise ProjectBootstrapError("Runtime image index has an invalid platform variant")
        key = f"{operating_system}/{architecture}"
        if variant:
            key = f"{key}/{variant}"
        if key in platforms:
            raise ProjectBootstrapError("Runtime image index repeats a platform")
        platforms[key] = digest
    if not platforms:
        raise ProjectBootstrapError("Runtime image index contains no runnable platform")
    return dict(sorted(platforms.items()))


def _write_artifact_record(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def active_admin_member(*, cwd: Path, runner: _Runner | None = None) -> str:
    """Resolve the active gcloud user as the stage-zero impersonation administrator."""
    command_runner = runner or _subprocess_runner
    try:
        result = command_runner(
            (
                "gcloud",
                "auth",
                "list",
                "--filter=status:ACTIVE",
                "--format=value(account)",
            ),
            cwd=cwd.resolve(),
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise ProjectBootstrapError("Could not resolve the active gcloud account") from error
    account = result.stdout.strip()
    if not _ACCOUNT.fullmatch(account):
        raise ProjectBootstrapError("No active gcloud user account was found")
    return f"user:{account}"


def wait_for_service_account_impersonation(
    *,
    service_account: str,
    project: str,
    cwd: Path,
    runner: _Runner | None = None,
    attempts: int = 12,
    delay_seconds: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait for a new stage-zero token-creator grant to become usable."""
    if not _PROJECT_ID.fullmatch(project) or not _ACCOUNT.fullmatch(service_account):
        raise ProjectBootstrapError("Invalid bootstrap impersonation target")
    if attempts < 1 or delay_seconds < 0:
        raise ProjectBootstrapError("Invalid bootstrap impersonation retry policy")
    command_runner = runner or _subprocess_runner
    command = (
        "gcloud",
        "auth",
        "print-access-token",
        f"--impersonate-service-account={service_account}",
        f"--project={project}",
    )
    try:
        for attempt in range(attempts):
            result = command_runner(
                command,
                cwd=cwd.resolve(),
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return
            if attempt + 1 < attempts:
                sleep(delay_seconds)
    except FileNotFoundError as error:
        raise ProjectBootstrapError(
            "gcloud is not installed or is not available on PATH"
        ) from error
    raise ProjectBootstrapError(
        "The bootstrap service-account impersonation grant did not become usable within "
        f"{attempts * delay_seconds:g} seconds; wait briefly and rerun dander init --apply"
    )
