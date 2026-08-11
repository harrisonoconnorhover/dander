"""Azure Container Apps managed-identity adaptation for Google federation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest
from google.auth import external_account

from dander.identity import AzureContainerAppsIdentityError, prepare_azure_google_identity

if TYPE_CHECKING:
    from dander.identity.azure_google import AzureCredentialFactory, GoogleCredentialFactory

_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
_CLIENT_ID = "22222222-2222-4222-8222-222222222222"
_ENVIRONMENT = {
    "AZURE_CLIENT_ID": _CLIENT_ID,
    "DANDER_AZURE_GCP_APPLICATION_ID_URI": ("api://33333333-3333-4333-8333-333333333333"),
    "DANDER_GCP_SERVICE_ACCOUNT": "dander-runtime@unit-project.iam.gserviceaccount.com",
    "DANDER_GCP_WIF_AUDIENCE": (
        "//iam.googleapis.com/projects/1009770943166/locations/global/"
        "workloadIdentityPools/dander-phase6-azure/providers/container-apps"
    ),
}


@dataclass(frozen=True)
class _Token:
    token: str
    expires_on: int


class _Credential:
    def __init__(self, markers: list[str], *, expires_on: int = 1_786_454_000) -> None:
        self.markers = markers
        self.scopes: list[str] = []
        self.expires_on = expires_on

    def get_token(self, *scopes: str) -> _Token:
        self.scopes.extend(scopes)
        marker = chr(ord("a") + len(self.scopes) - 1)
        self.markers.append(marker)
        return _Token(f"header.payload-{marker}.signature", self.expires_on)


def test_azure_google_identity_renews_from_one_user_assigned_identity() -> None:
    markers: list[str] = []
    credential = _Credential(markers)
    client_ids: list[str] = []

    def azure_factory(*, client_id: str) -> _Credential:
        client_ids.append(client_id)
        return credential

    def capture_google(**values: object) -> object:
        assert values["audience"] == _ENVIRONMENT["DANDER_GCP_WIF_AUDIENCE"]
        assert values["service_account"] == _ENVIRONMENT["DANDER_GCP_SERVICE_ACCOUNT"]
        return values["supplier"]

    supplier = prepare_azure_google_identity(
        environ=dict(_ENVIRONMENT),
        clock=lambda: _NOW,
        azure_credential_factory=cast("AzureCredentialFactory", azure_factory),
        google_credential_factory=cast("GoogleCredentialFactory", capture_google),
    )

    first = supplier.subject_token()  # type: ignore[attr-defined]
    second = supplier.subject_token()  # type: ignore[attr-defined]

    assert first != second
    assert markers == ["a", "b", "c"]
    assert client_ids == [_CLIENT_ID]
    assert credential.scopes == ["api://33333333-3333-4333-8333-333333333333/.default"] * 3


def test_azure_google_identity_builds_short_scoped_external_credentials() -> None:
    credentials = prepare_azure_google_identity(
        environ=dict(_ENVIRONMENT),
        clock=lambda: _NOW,
        azure_credential_factory=cast("AzureCredentialFactory", lambda **_values: _Credential([])),
    )

    assert isinstance(credentials, external_account.Credentials)
    assert credentials._scopes == ("https://www.googleapis.com/auth/cloud-platform",)
    assert credentials._service_account_impersonation_options == {"token_lifetime_seconds": 600}
    assert credentials._credential_source == {"environment_id": "azure-container-apps"}
    assert credentials._client_id is None
    assert credentials._client_secret is None


def test_azure_google_identity_survives_google_auth_impersonation_clone() -> None:
    markers: list[str] = []
    credentials = prepare_azure_google_identity(
        environ=dict(_ENVIRONMENT),
        clock=lambda: _NOW,
        azure_credential_factory=cast(
            "AzureCredentialFactory", lambda **_values: _Credential(markers)
        ),
    )

    impersonated = cast("Any", credentials)._initialize_impersonated_credentials()
    source_credentials = impersonated._source_credentials

    assert source_credentials.retrieve_subject_token(None).endswith("payload-b.signature")
    assert markers == ["a", "b"]


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"AZURE_CLIENT_ID": "not-a-uuid"}, "configuration"),
        ({"DANDER_AZURE_GCP_APPLICATION_ID_URI": "https://example.invalid"}, "configuration"),
        ({"DANDER_GCP_SERVICE_ACCOUNT": "not-an-account"}, "configuration"),
        ({"DANDER_GCP_WIF_AUDIENCE": "not-an-audience"}, "configuration"),
    ],
)
def test_azure_google_identity_rejects_invalid_configuration(
    update: dict[str, str],
    message: str,
) -> None:
    environment = {**_ENVIRONMENT, **update}

    with pytest.raises(AzureContainerAppsIdentityError, match=message):
        prepare_azure_google_identity(
            environ=environment,
            clock=lambda: _NOW,
            azure_credential_factory=cast(
                "AzureCredentialFactory", lambda **_values: _Credential([])
            ),
        )


def test_azure_google_identity_rejects_near_expiry_or_non_jwt_tokens() -> None:
    near_expiry = int(_NOW.timestamp()) + 299

    with pytest.raises(AzureContainerAppsIdentityError, match="invalid"):
        prepare_azure_google_identity(
            environ=dict(_ENVIRONMENT),
            clock=lambda: _NOW,
            azure_credential_factory=cast(
                "AzureCredentialFactory",
                lambda **_values: _Credential([], expires_on=near_expiry),
            ),
        )
