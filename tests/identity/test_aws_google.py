"""Fargate task-role adaptation for Google workload federation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from google.auth.aws import Credentials

from dander.identity import (
    FargateIdentityError,
    google_client_options,
    launcher_identity,
    prepare_fargate_google_identity,
    prepare_fargate_task_credentials,
    prepare_launcher_identity,
)
from dander.runtime_contract import LauncherContext

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from dander.identity.aws_google import GoogleCredentialFactory

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


def test_fargate_google_identity_renews_from_the_fixed_ecs_endpoint() -> None:
    environment = {
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": _RELATIVE_URI,
        "AWS_REGION": "us-east-1",
        "DANDER_GCP_SERVICE_ACCOUNT": ("dander-runtime@unit-project.iam.gserviceaccount.com"),
        "DANDER_GCP_WIF_AUDIENCE": (
            "//iam.googleapis.com/projects/1009770943166/locations/global/"
            "workloadIdentityPools/dander-phase1b-aws/providers/fargate"
        ),
    }
    urls: list[str] = []
    sequence = iter(("A", "B", "C"))

    def fetch(url: str) -> object:
        urls.append(url)
        marker = next(sequence)
        return {
            "AccessKeyId": "ASIA" + marker * 16,
            "SecretAccessKey": f"temporary-secret-{marker}",
            "Token": f"temporary-session-{marker}",
            "Expiration": "2026-08-08T13:00:00Z",
        }

    def capture_factory(**values: object) -> object:
        assert values["audience"] == environment["DANDER_GCP_WIF_AUDIENCE"]
        assert values["service_account"] == environment["DANDER_GCP_SERVICE_ACCOUNT"]
        return values["supplier"]

    supplier = prepare_fargate_google_identity(
        environ=environment,
        fetch=fetch,
        clock=lambda: _NOW,
        credential_factory=cast("GoogleCredentialFactory", capture_factory),
    )
    first = supplier.get_aws_security_credentials(None, None)  # type: ignore[attr-defined]
    second = supplier.get_aws_security_credentials(None, None)  # type: ignore[attr-defined]

    assert first.access_key_id == "ASIA" + "B" * 16
    assert second.access_key_id == "ASIA" + "C" * 16
    assert urls == [f"http://169.254.170.2{_RELATIVE_URI}"] * 3
    assert not set(environment).intersection(
        {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
    )


def test_fargate_google_identity_builds_scoped_impersonated_credentials() -> None:
    environment = {
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": _RELATIVE_URI,
        "AWS_REGION": "us-east-1",
        "DANDER_GCP_SERVICE_ACCOUNT": ("dander-runtime@unit-project.iam.gserviceaccount.com"),
        "DANDER_GCP_WIF_AUDIENCE": (
            "//iam.googleapis.com/projects/1009770943166/locations/global/"
            "workloadIdentityPools/dander-phase1b-aws/providers/fargate"
        ),
    }

    credentials = prepare_fargate_google_identity(
        environ=environment,
        fetch=lambda _url: _document(),
        clock=lambda: _NOW,
    )

    assert isinstance(credentials, Credentials)
    assert credentials._scopes == ("https://www.googleapis.com/auth/cloud-platform",)
    assert credentials._service_account_impersonation_options == {"token_lifetime_seconds": 600}
    assert credentials._credential_source is None
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in environment


def test_launcher_identity_scopes_google_credentials(
    monkeypatch: MonkeyPatch,
) -> None:
    sentinel = object()
    context = LauncherContext.from_environment(
        {"DANDER_LAUNCHER": "fargate", "DANDER_RUN_ID": "run-123"}
    )
    monkeypatch.setattr(
        "dander.identity.aws_google.prepare_launcher_identity",
        lambda _context: sentinel,
    )

    assert google_client_options() == {}
    with launcher_identity(context):
        assert google_client_options() == {"credentials": sentinel}
    assert google_client_options() == {}


def test_aws_native_fargate_leaves_task_role_identity_ambient(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("DANDER_GCP_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("DANDER_GCP_WIF_AUDIENCE", raising=False)
    context = LauncherContext.from_environment(
        {"DANDER_LAUNCHER": "fargate", "DANDER_RUN_ID": "aws-native-run"}
    )

    assert prepare_launcher_identity(context) is None


def test_fargate_partial_google_federation_configuration_fails_closed(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("DANDER_GCP_WIF_AUDIENCE", "incomplete")
    monkeypatch.delenv("DANDER_GCP_SERVICE_ACCOUNT", raising=False)
    context = LauncherContext.from_environment(
        {"DANDER_LAUNCHER": "fargate", "DANDER_RUN_ID": "invalid-federation-run"}
    )

    with pytest.raises(FargateIdentityError, match="invalid"):
        prepare_launcher_identity(context)


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
