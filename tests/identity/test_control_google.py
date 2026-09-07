"""Control selects native Google identity without bypassing configured AWS federation."""

from __future__ import annotations

import google.auth
import pytest
from google.auth.exceptions import DefaultCredentialsError

from dander.identity import control_google
from dander.identity.control_google import (
    GoogleControlIdentityError,
    prepare_control_google_identity,
)


def test_native_control_uses_scoped_application_default_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = object()
    observed: list[object] = []

    def default(*, scopes: object) -> tuple[object, str]:
        observed.append(scopes)
        return credentials, "default-project"

    monkeypatch.setattr(google.auth, "default", default)
    assert prepare_control_google_identity(environ={}) is credentials
    assert observed == [("https://www.googleapis.com/auth/cloud-platform",)]


def test_configured_fargate_federation_retains_its_credential_supplier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = object()
    environment = {"DANDER_GCP_WIF_AUDIENCE": "configured-audience"}
    observed: list[object] = []

    def federated(*, environ: object) -> object:
        observed.append(environ)
        return credentials

    monkeypatch.setattr(control_google, "prepare_fargate_google_identity", federated)
    assert prepare_control_google_identity(environ=environment) is credentials
    assert observed == [environment]


@pytest.mark.parametrize(
    "setting",
    (
        "DANDER_GCP_WIF_AUDIENCE",
        "DANDER_GCP_SERVICE_ACCOUNT",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    ),
)
def test_incomplete_federation_never_falls_back_to_native_credentials(
    monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    def forbidden_default(**kwargs: object) -> object:
        pytest.fail("Incomplete federation must not change the selected identity")

    monkeypatch.setattr(google.auth, "default", forbidden_default)
    with pytest.raises(GoogleControlIdentityError, match="credentials are unavailable"):
        prepare_control_google_identity(environ={setting: ""})


def test_native_identity_error_does_not_expose_credential_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(**kwargs: object) -> object:
        raise DefaultCredentialsError("private-credential-material")  # type: ignore[no-untyped-call]

    monkeypatch.setattr(google.auth, "default", unavailable)
    with pytest.raises(GoogleControlIdentityError) as caught:
        prepare_control_google_identity(environ={})
    assert "private-credential-material" not in str(caught.value)
    assert caught.value.__suppress_context__
