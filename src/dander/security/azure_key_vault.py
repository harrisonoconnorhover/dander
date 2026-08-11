"""Secret resolution from versionless or versioned Azure Key Vault references."""

from __future__ import annotations

import re
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from dander.security.secret_manager import SecretResolutionError, audit_secret_access

if TYPE_CHECKING:
    from typing import Any

_REFERENCE = re.compile(
    r"^azure-kv://(?P<vault>https://[a-z][a-z0-9-]{1,22}[a-z0-9]\.vault\.azure\.net)"
    r"/secrets/(?P<name>[A-Za-z0-9-]{1,127})(?:/(?P<version>[A-Za-z0-9]+))?$"
)


class _Secret(Protocol):
    value: str | None


class _SecretClient(Protocol):
    def get_secret(self, name: str, version: str | None = None) -> object:
        """Read one Azure Key Vault secret."""


class AzureKeyVaultSecretStore:
    """Resolve full Key Vault references without accepting literal secret values."""

    def __init__(self, client: _SecretClient | None = None) -> None:
        self._client = client
        self._vault_uri: str | None = None

    def get_secret(self, reference: str) -> str:
        """Return one UTF-8 text secret from an exact Key Vault URI."""
        match = _REFERENCE.fullmatch(reference)
        if match is None or "--" in match.group("vault"):
            raise SecretResolutionError(
                "Azure Key Vault references must be full azure-kv://https://.../secrets/... URIs"
            )
        vault_uri = match.group("vault")
        client = self._client_for(vault_uri)
        secret = cast("_Secret", client.get_secret(match.group("name"), match.group("version")))
        if not isinstance(secret.value, str) or not secret.value:
            raise SecretResolutionError("Azure Key Vault secret is empty or not text")
        audit_secret_access(reference, "azure_key_vault")
        return secret.value

    def _client_for(self, vault_uri: str) -> _SecretClient:
        if self._client is not None:
            if self._vault_uri is None:
                self._vault_uri = vault_uri
            elif self._vault_uri != vault_uri:
                raise SecretResolutionError(
                    "One Azure Key Vault resolver cannot cross vault boundaries"
                )
            return self._client
        identity_module = import_module("azure.identity")
        secrets_module = import_module("azure.keyvault.secrets")
        credential = cast("Any", identity_module).DefaultAzureCredential()
        client = cast("Any", secrets_module).SecretClient(
            vault_url=vault_uri,
            credential=credential,
        )
        self._client = cast("_SecretClient", client)
        self._vault_uri = vault_uri
        return self._client
