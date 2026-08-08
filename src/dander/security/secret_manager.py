"""Secret resolution for GCP and local development.

References beginning with ``projects/`` are resolved directly through GCP Secret Manager.
Other references name environment variables; an environment value may itself be a Secret Manager
resource name. Every successful access emits an audit event containing the reference and backend,
never the resolved value.
"""

from __future__ import annotations

import logging
import os
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

_LOGGER = logging.getLogger(__name__)
_GCP_REFERENCE_PREFIX = "projects/"


class SecretResolutionError(RuntimeError):
    """Raised when a configured secret reference cannot be resolved."""


class _SecretPayload(Protocol):
    data: bytes


class _SecretResponse(Protocol):
    payload: _SecretPayload


class _SecretManagerClient(Protocol):
    def access_secret_version(self, *, request: Mapping[str, str]) -> object:
        """Access one Secret Manager version."""


def audit_secret_access(reference: str, backend: str) -> None:
    """Emit a credential-access audit event without exposing secret material."""
    _LOGGER.info(
        "credential_access",
        extra={
            "credential_actor": os.environ.get("DANDER_PRINCIPAL", "dander-runtime"),
            "dander_event": "credential_access",
            "secret_backend": backend,
            "secret_reference": reference,
        },
    )


class GcpSecretStore:
    """Resolve secrets from GCP Secret Manager by resource name."""

    def __init__(self, client: _SecretManagerClient | None = None) -> None:
        if client is None:
            client_type = cast(
                "Any", import_module("google.cloud.secretmanager")
            ).SecretManagerServiceClient
            client = cast("_SecretManagerClient", client_type())
        self._client = client

    def get_secret(self, reference: str) -> str:
        """Return a secret value from a fully-qualified Secret Manager reference.

        Args:
            reference: Resource name in
                ``projects/PROJECT/secrets/NAME/versions/VERSION`` form.

        Returns:
            The UTF-8 decoded secret value.

        Raises:
            SecretResolutionError: If `reference` is not a Secret Manager resource name.
        """
        if not reference.startswith(_GCP_REFERENCE_PREFIX):
            raise SecretResolutionError("Secret Manager references must begin with 'projects/'")

        response = cast(
            "_SecretResponse",
            self._client.access_secret_version(request={"name": reference}),
        )
        value = response.payload.data.decode("utf-8")
        audit_secret_access(reference, "gcp_secret_manager")
        return value


class EnvironmentSecretStore:
    """Resolve local-development secrets from environment-variable names."""

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = environment if environment is not None else os.environ

    def get_secret(self, reference: str) -> str:
        """Return the value of the environment variable named by `reference`.

        Raises:
            SecretResolutionError: If the variable is missing or empty.
        """
        value = self._environment.get(reference)
        if not value:
            raise SecretResolutionError(
                f"Secret environment reference {reference!r} is missing or empty"
            )
        audit_secret_access(reference, "environment")
        return value


class DefaultSecretStore:
    """Route secret references to Secret Manager or the local environment.

    An environment variable may contain a Secret Manager resource name. This keeps connector YAML
    stable across local and cloud execution: it names an environment key, while production points
    that key at the managed resource.
    """

    def __init__(
        self,
        *,
        environment: EnvironmentSecretStore | None = None,
        gcp: GcpSecretStore | None = None,
    ) -> None:
        self._environment = environment or EnvironmentSecretStore()
        self._gcp = gcp

    def get_secret(self, reference: str) -> str:
        """Resolve `reference` through the appropriate backing store."""
        if reference.startswith(_GCP_REFERENCE_PREFIX):
            return self._gcp_store().get_secret(reference)

        value = self._environment.get_secret(reference)
        if value.startswith(_GCP_REFERENCE_PREFIX):
            return self._gcp_store().get_secret(value)
        return value

    def _gcp_store(self) -> GcpSecretStore:
        if self._gcp is None:
            self._gcp = GcpSecretStore()
        return self._gcp
