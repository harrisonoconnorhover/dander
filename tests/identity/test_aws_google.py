"""Fargate task-role adaptation for Google workload federation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from dander.identity import (
    FargateIdentityError,
    prepare_fargate_google_identity,
    prepare_fargate_task_credentials,
)

if TYPE_CHECKING:
    from pathlib import Path

_RELATIVE_URI = "/v2/credentials/12345678-1234-1234-1234-123456789abc"
_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _document() -> dict[str, str]:
    return {
        "AccessKeyId": "ASIA" + "A" * 16,
        "SecretAccessKey": "temporary-" + "secret",
        "Token": "temporary-" + "session-token",
        "Expiration": "2026-08-08T13:00:00Z",
    }


def test_fargate_identity_uses_only_the_fixed_ecs_credential_origin() -> None:
    environment = {"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": _RELATIVE_URI}
    urls: list[str] = []

    def fetch(url: str) -> object:
        urls.append(url)
        return _document()

    assert prepare_fargate_task_credentials(
        environ=environment,
        fetch=fetch,
        now=_NOW,
    )

    assert urls == [f"http://169.254.170.2{_RELATIVE_URI}"]
    assert environment["AWS_ACCESS_KEY_ID"].startswith("ASIA")
    assert environment["AWS_SESSION_TOKEN"]


def test_fargate_google_identity_writes_only_non_secret_external_config(tmp_path: Path) -> None:
    credential_path = tmp_path / "wif.json"
    environment = {
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": _RELATIVE_URI,
        "DANDER_GCP_SERVICE_ACCOUNT": ("dander-runtime@unit-project.iam.gserviceaccount.com"),
        "DANDER_GCP_WIF_AUDIENCE": (
            "//iam.googleapis.com/projects/1009770943166/locations/global/"
            "workloadIdentityPools/dander-phase1b-aws/providers/fargate"
        ),
    }

    prepare_fargate_google_identity(
        environ=environment,
        fetch=lambda _url: _document(),
        credential_path=credential_path,
        now=_NOW,
    )

    config = json.loads(credential_path.read_text(encoding="utf-8"))
    assert config["type"] == "external_account"
    assert config["credential_source"]["environment_id"] == "aws1"
    assert config["service_account_impersonation"] == {"token_lifetime_seconds": 600}
    assert environment["GOOGLE_APPLICATION_CREDENTIALS"] == str(credential_path)
    assert credential_path.stat().st_mode & 0o777 == 0o600
    serialized = credential_path.read_text(encoding="utf-8")
    assert "private_key" not in serialized
    assert "client_secret" not in serialized
    assert environment["AWS_SECRET_ACCESS_KEY"] not in serialized


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({}, "endpoint"),
        ({"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "http://example.invalid"}, "endpoint"),
        ({"AWS_ACCESS_KEY_ID": "AKIA" + "A" * 16}, "preconfigured"),
        (
            {
                "AWS_ACCESS_KEY_ID": "AKIA" + "A" * 16,
                "AWS_SECRET_ACCESS_KEY": "not-temporary",
                "AWS_SESSION_TOKEN": "not-temporary",
            },
            "preconfigured",
        ),
    ],
)
def test_fargate_identity_rejects_missing_or_long_lived_credentials(
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(FargateIdentityError, match=message):
        prepare_fargate_task_credentials(environ=environment, now=_NOW)


def test_fargate_identity_rejects_credentials_near_expiry() -> None:
    environment = {"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": _RELATIVE_URI}
    document = _document()
    document["Expiration"] = "2026-08-08T12:04:59Z"

    with pytest.raises(FargateIdentityError, match="invalid"):
        prepare_fargate_task_credentials(
            environ=environment,
            fetch=lambda _url: document,
            now=_NOW,
        )
