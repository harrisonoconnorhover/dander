"""Read-only stage-zero permission preflight tests."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

from dander.bootstrap import AdministrativeBootstrapError, require_stage_zero_permissions

if TYPE_CHECKING:
    from pathlib import Path


class _Runner:
    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert args == (
            "gcloud",
            "auth",
            "print-access-token",
            "--project=unit-project",
        )
        assert cwd.is_absolute() and check and capture_output and text
        return subprocess.CompletedProcess(args, 0, stdout="temporary-access-token\n")


def _response(url: str, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("POST", url))


def _get_response(url: str, payload: dict[str, Any], *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def _allow_all_post(url: str, **kwargs: object) -> httpx.Response:
    payload = json.loads(str(kwargs["content"]))
    return _response(url, {"permissions": payload["permissions"]})


def test_preflight_checks_only_core_project_permissions_by_default(tmp_path: Path) -> None:
    requests: list[tuple[str, tuple[str, ...], str]] = []

    def post(url: str, **kwargs: object) -> httpx.Response:
        payload = json.loads(str(kwargs["content"]))
        permissions = tuple(payload["permissions"])
        requests.append((url, permissions, str(kwargs["headers"])))
        return _response(url, {"permissions": list(permissions)})

    require_stage_zero_permissions(
        project="unit-project",
        cwd=tmp_path,
        runner=_Runner(),
        post=post,
    )

    assert len(requests) == 1
    assert "cloudresourcemanager.googleapis.com" in requests[0][0]
    assert not any("workloadIdentityPools" in item for item in requests[0][1])
    assert "temporary-access-token" in requests[0][2]


def test_preflight_adds_wif_and_billing_permissions_only_when_requested(tmp_path: Path) -> None:
    requested: list[tuple[str, ...]] = []

    def post(url: str, **kwargs: object) -> httpx.Response:
        payload = json.loads(str(kwargs["content"]))
        permissions = tuple(payload["permissions"])
        requested.append(permissions)
        return _response(url, {"permissions": list(permissions)})

    require_stage_zero_permissions(
        project="unit-project",
        cwd=tmp_path,
        billing_account_id="ABCDEF-123456-ABCDEF",
        github_repository="owner/repository",
        runner=_Runner(),
        post=post,
    )

    assert len(requested) == 2
    assert any("workloadIdentityPools" in item for item in requested[0])
    assert requested[1] == (
        "billing.accounts.get",
        "billing.accounts.getIamPolicy",
        "billing.accounts.setIamPolicy",
    )


def test_preflight_reports_exact_missing_permissions_without_running_terraform(
    tmp_path: Path,
) -> None:
    def post(url: str, **kwargs: object) -> httpx.Response:
        payload = json.loads(str(kwargs["content"]))
        permissions = list(payload["permissions"])
        permissions.remove("resourcemanager.projects.setIamPolicy")
        return _response(url, {"permissions": permissions})

    with pytest.raises(
        AdministrativeBootstrapError,
        match=r"missing project: resourcemanager\.projects\.setIamPolicy",
    ):
        require_stage_zero_permissions(
            project="unit-project",
            cwd=tmp_path,
            runner=_Runner(),
            post=post,
        )


def test_preflight_checks_existing_state_bucket_on_the_bucket_resource(tmp_path: Path) -> None:
    requested: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def get(url: str, **kwargs: object) -> httpx.Response:
        params = tuple(cast("list[tuple[str, str]]", kwargs["params"]))
        requested.append((url, params))
        return _get_response(url, {"permissions": [value for _key, value in params]})

    require_stage_zero_permissions(
        project="unit-project",
        cwd=tmp_path,
        state_bucket="unit-state",
        runner=_Runner(),
        post=_allow_all_post,
        get=get,
    )

    assert requested == [
        (
            "https://storage.googleapis.com/storage/v1/b/unit-state/iam/testPermissions",
            (
                ("permissions", "storage.buckets.get"),
                ("permissions", "storage.buckets.update"),
            ),
        )
    ]


def test_preflight_allows_terraform_to_create_a_missing_state_bucket(tmp_path: Path) -> None:
    require_stage_zero_permissions(
        project="unit-project",
        cwd=tmp_path,
        state_bucket="new-state",
        runner=_Runner(),
        post=_allow_all_post,
        get=lambda url, **_kwargs: _get_response(url, {}, status_code=404),
    )


def test_preflight_reports_exact_missing_existing_bucket_permission(tmp_path: Path) -> None:
    with pytest.raises(
        AdministrativeBootstrapError,
        match=r"missing state bucket: storage\.buckets\.update",
    ):
        require_stage_zero_permissions(
            project="unit-project",
            cwd=tmp_path,
            state_bucket="existing-state",
            runner=_Runner(),
            post=_allow_all_post,
            get=lambda url, **_kwargs: _get_response(
                url,
                {"permissions": ["storage.buckets.get"]},
            ),
        )
