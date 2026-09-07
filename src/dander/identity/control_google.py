"""Select ambient Google credentials for Control without requiring an AWS host."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from dander.identity.aws_google import prepare_fargate_google_identity

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_FEDERATION_SETTINGS = (
    "DANDER_GCP_WIF_AUDIENCE",
    "DANDER_GCP_SERVICE_ACCOUNT",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
)


class GoogleControlIdentityError(RuntimeError):
    """The selected Google identity is unavailable or incomplete."""


def prepare_control_google_identity(*, environ: MutableMapping[str, str] | None = None) -> object:
    """Use configured Fargate federation or Google's Application Default Credentials."""
    environment = os.environ if environ is None else environ
    try:
        if any(name in environment for name in _FEDERATION_SETTINGS):
            return prepare_fargate_google_identity(environ=environment)

        import google.auth

        credentials, _ = google.auth.default(
            scopes=("https://www.googleapis.com/auth/cloud-platform",)
        )
        return credentials
    except Exception:  # noqa: BLE001 - credential errors can contain secret material
        raise GoogleControlIdentityError("Google Control credentials are unavailable.") from None
