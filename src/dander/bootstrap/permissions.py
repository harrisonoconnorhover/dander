"""Focused stage-zero permission preflight for the authenticated GCP caller."""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

import httpx

from dander.bootstrap.admin import AdministrativeBootstrapError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_BILLING_ACCOUNT = re.compile(r"^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$")
_PROJECT_PERMISSIONS = (
    "artifactregistry.repositories.create",
    "iam.serviceAccounts.create",
    "iam.serviceAccounts.get",
    "iam.serviceAccounts.getIamPolicy",
    "iam.serviceAccounts.setIamPolicy",
    "resourcemanager.projects.getIamPolicy",
    "resourcemanager.projects.setIamPolicy",
    "serviceusage.services.enable",
    "serviceusage.services.get",
    "storage.buckets.create",
)
_BUCKET_PERMISSIONS = ("storage.buckets.get", "storage.buckets.update")
_WIF_PERMISSIONS = (
    "iam.workloadIdentityPools.create",
    "iam.workloadIdentityPools.get",
    "iam.workloadIdentityPools.update",
)
_BILLING_PERMISSIONS = (
    "billing.accounts.get",
    "billing.accounts.getIamPolicy",
    "billing.accounts.setIamPolicy",
)


class _CompletedProcess(Protocol):
    returncode: int
    stdout: str


class _Runner(Protocol):
    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> _CompletedProcess: ...


def require_stage_zero_permissions(
    *,
    project: str,
    cwd: Path,
    state_bucket: str = "",
    billing_account_id: str = "",
    github_repository: str = "",
    runner: _Runner | None = None,
    post: Callable[..., httpx.Response] = httpx.post,
    get: Callable[..., httpx.Response] = httpx.get,
) -> None:
    """Fail before Terraform when the current identity lacks stage-zero permissions."""
    if not _PROJECT_ID.fullmatch(project):
        raise AdministrativeBootstrapError(f"Invalid project: {project!r}")
    if billing_account_id and not _BILLING_ACCOUNT.fullmatch(billing_account_id):
        raise AdministrativeBootstrapError("Billing account must use XXXXXX-XXXXXX-XXXXXX format")
    command_runner = runner or _subprocess_runner
    try:
        token_result = command_runner(
            ("gcloud", "auth", "print-access-token", f"--project={project}"),
            cwd=cwd.resolve(),
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise AdministrativeBootstrapError(
            "Could not obtain an access token for the stage-zero permission preflight"
        ) from error
    token = token_result.stdout.strip()
    if not token:
        raise AdministrativeBootstrapError(
            "The active gcloud identity did not return an access token"
        )

    project_permissions = (*_PROJECT_PERMISSIONS, *(_WIF_PERMISSIONS if github_repository else ()))
    missing_project = _missing_permissions(
        f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}:testIamPermissions",
        project_permissions,
        token=token,
        post=post,
    )
    missing_bucket = (
        _missing_bucket_permissions(state_bucket, token=token, get=get) if state_bucket else ()
    )
    missing_billing: tuple[str, ...] = ()
    if billing_account_id:
        missing_billing = _missing_permissions(
            "https://cloudbilling.googleapis.com/v1/"
            f"billingAccounts/{billing_account_id}:testIamPermissions",
            _BILLING_PERMISSIONS,
            token=token,
            post=post,
        )
    if not missing_project and not missing_bucket and not missing_billing:
        return

    details: list[str] = []
    if missing_project:
        details.append(f"project: {', '.join(missing_project)}")
    if missing_bucket:
        details.append(f"state bucket: {', '.join(missing_bucket)}")
    if missing_billing:
        details.append(f"billing account: {', '.join(missing_billing)}")
    raise AdministrativeBootstrapError(
        "Stage-zero permission preflight failed; missing " + "; ".join(details)
    )


def _missing_permissions(
    url: str,
    required: tuple[str, ...],
    *,
    token: str,
    post: Callable[..., httpx.Response],
) -> tuple[str, ...]:
    try:
        response = post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            content=json.dumps({"permissions": required}),
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise AdministrativeBootstrapError(
            "Could not complete the read-only stage-zero permission preflight"
        ) from error
    granted = payload.get("permissions") if isinstance(payload, dict) else None
    if not isinstance(granted, list) or not all(isinstance(item, str) for item in granted):
        raise AdministrativeBootstrapError("GCP returned an invalid stage-zero permission response")
    return tuple(permission for permission in required if permission not in granted)


def _missing_bucket_permissions(
    bucket: str,
    *,
    token: str,
    get: Callable[..., httpx.Response],
) -> tuple[str, ...]:
    """Test permissions on an existing bucket; a missing bucket is created by Terraform."""
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{quote(bucket, safe='')}/iam/testPermissions"
    )
    try:
        response = get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=[("permissions", permission) for permission in _BUCKET_PERMISSIONS],
            timeout=15.0,
        )
        if response.status_code == 404:
            return ()
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise AdministrativeBootstrapError(
            "Could not complete the read-only state-bucket permission preflight"
        ) from error
    granted = payload.get("permissions") if isinstance(payload, dict) else None
    if not isinstance(granted, list) or not all(isinstance(item, str) for item in granted):
        raise AdministrativeBootstrapError(
            "GCP returned an invalid state-bucket permission response"
        )
    return tuple(permission for permission in _BUCKET_PERMISSIONS if permission not in granted)


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
