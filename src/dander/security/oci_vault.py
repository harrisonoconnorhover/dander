"""Secret resolution from versionless OCI Vault references."""

from __future__ import annotations

import base64
import binascii
import re
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from dander.security.secret_manager import SecretResolutionError, audit_secret_access

if TYPE_CHECKING:
    from typing import Any

_OCID_SUFFIX = r"[A-Za-z0-9]+"
_REALM = r"oc[0-9]+"
_REGION = r"[a-z0-9-]+"
_SECRET_OCID = rf"ocid1\.vaultsecret\.{_REALM}\.{_REGION}\.{_OCID_SUFFIX}"
_VAULT_OCID = rf"ocid1\.vault\.{_REALM}\.{_REGION}\.(?:{_OCID_SUFFIX}\.)?{_OCID_SUFFIX}"
_REFERENCE = re.compile(
    rf"^oci-vault://(?:"
    rf"(?P<secret_id>{_SECRET_OCID})|"
    rf"(?P<vault_id>{_VAULT_OCID})/secrets/"
    rf"(?P<secret_name>[A-Za-z][A-Za-z0-9_-]{{0,254}})"
    rf")$"
)


class _SecretBundleContent(Protocol):
    content: str | None
    content_type: str | None


class _SecretBundle(Protocol):
    secret_bundle_content: _SecretBundleContent


class _SecretResponse(Protocol):
    data: _SecretBundle


class _SecretsClient(Protocol):
    def get_secret_bundle(self, *, secret_id: str, stage: str) -> object:
        """Read the current bundle for one exact secret OCID."""

    def get_secret_bundle_by_name(
        self,
        *,
        secret_name: str,
        vault_id: str,
        stage: str,
    ) -> object:
        """Read the current bundle for one name in one exact vault."""


class OciVaultSecretStore:
    """Resolve OCI Vault values with the ambient resource principal."""

    def __init__(self, client: _SecretsClient | None = None) -> None:
        self._client = client
        self._vault_id: str | None = None

    def get_secret(self, reference: str) -> str:
        """Return one UTF-8 secret while always selecting the current version."""
        match = _REFERENCE.fullmatch(reference)
        if match is None:
            raise SecretResolutionError(
                "OCI Vault references must use a secret OCID or exact vault/name URI"
            )
        client = self._client_for()
        secret_id = match.group("secret_id")
        vault_id = match.group("vault_id")
        if secret_id is not None:
            response = client.get_secret_bundle(secret_id=secret_id, stage="CURRENT")
        else:
            assert vault_id is not None
            secret_name = match.group("secret_name")
            assert secret_name is not None
            if self._vault_id is None:
                self._vault_id = vault_id
            elif self._vault_id != vault_id:
                raise SecretResolutionError("One OCI Vault resolver cannot cross vaults")
            response = client.get_secret_bundle_by_name(
                secret_name=secret_name,
                vault_id=vault_id,
                stage="CURRENT",
            )
        value = _decode_secret(cast("_SecretResponse", response))
        audit_secret_access(reference, "oci_vault")
        return value

    def _client_for(self) -> _SecretsClient:
        if self._client is not None:
            return self._client
        try:
            oci_module = cast("Any", import_module("oci"))
            signer = oci_module.auth.signers.get_resource_principals_signer()
            client = oci_module.secrets.SecretsClient({}, signer=signer)
        except (AttributeError, ImportError, OSError) as error:
            raise SecretResolutionError(
                "OCI Vault requires the OCI SDK and an ambient resource principal"
            ) from error
        self._client = cast("_SecretsClient", client)
        return self._client


def _decode_secret(response: _SecretResponse) -> str:
    content = response.data.secret_bundle_content
    if content.content_type != "BASE64" or not isinstance(content.content, str):
        raise SecretResolutionError("OCI Vault secret is not base64 text")
    try:
        value = base64.b64decode(content.content, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as error:
        raise SecretResolutionError("OCI Vault secret is not valid UTF-8 text") from error
    if not value:
        raise SecretResolutionError("OCI Vault secret is empty")
    return value


__all__ = ["OciVaultSecretStore"]
