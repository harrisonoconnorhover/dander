"""OCI Vault provider selection and resource-principal-safe resolution."""

from __future__ import annotations

import base64
import logging
import sys
from dataclasses import dataclass

import pytest

from dander.providers import ProviderKind, default_provider_registry
from dander.security import SecretResolutionError, SecretRuntime

_VAULT_ID = "ocid1.vault.oc1.iad." + "a" * 32
_TEST_RESOURCE_OCID = "ocid1.vaultsecret.oc1.iad." + "b" * 32


@dataclass
class _Content:
    content: str | None
    content_type: str | None = "BASE64"


@dataclass
class _Bundle:
    secret_bundle_content: _Content


@dataclass
class _Response:
    data: _Bundle


class _Client:
    def __init__(self, value: bytes = b"resolved-value") -> None:
        self.content = base64.b64encode(value).decode("ascii")
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get_secret_bundle(self, *, secret_id: str, stage: str) -> _Response:
        self.requests.append(("id", {"secret_id": secret_id, "stage": stage}))
        return _Response(_Bundle(_Content(self.content)))

    def get_secret_bundle_by_name(
        self,
        *,
        secret_name: str,
        vault_id: str,
        stage: str,
    ) -> _Response:
        self.requests.append(
            (
                "name",
                {"secret_name": secret_name, "vault_id": vault_id, "stage": stage},
            )
        )
        return _Response(_Bundle(_Content(self.content)))


def _runtime(client: _Client) -> SecretRuntime:
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.SECRETS, {"provider": "oci_vault"})
    runtime = registry.build(ProviderKind.SECRETS, config, context={"client": client})
    assert isinstance(runtime, SecretRuntime)
    return runtime


def test_oci_vault_provider_is_lazy_and_reads_current_secret_by_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime_module = "dander.providers.oci_vault.runtime"
    sys.modules.pop(runtime_module, None)
    sys.modules.pop("oci", None)
    client = _Client()
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.SECRETS, {"provider": "oci_vault"})

    assert runtime_module not in sys.modules
    runtime = registry.build(ProviderKind.SECRETS, config, context={"client": client})
    assert isinstance(runtime, SecretRuntime)
    assert runtime_module in sys.modules
    assert "oci" not in sys.modules
    reference = f"oci-vault://{_VAULT_ID}/secrets/postgres-dsn"
    with caplog.at_level(logging.INFO):
        assert runtime.store.get_secret(reference) == "resolved-value"

    assert client.requests == [
        (
            "name",
            {"secret_name": "postgres-dsn", "vault_id": _VAULT_ID, "stage": "CURRENT"},
        )
    ]
    assert caplog.records[-1].__dict__["secret_backend"] == "oci_vault"
    assert "resolved-value" not in caplog.text
    assert runtime.capabilities.reference_forms == frozenset(
        {"oci_vault_secret_ocid", "oci_vault_secret_name"}
    )


def test_oci_vault_reads_current_secret_by_ocid() -> None:
    client = _Client()

    assert (
        _runtime(client).store.get_secret(f"oci-vault://{_TEST_RESOURCE_OCID}") == "resolved-value"
    )
    assert client.requests == [("id", {"secret_id": _TEST_RESOURCE_OCID, "stage": "CURRENT"})]


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("oci-vault://short-name", "secret OCID"),
        (f"oci-vault://{_VAULT_ID}/secrets/not/a/name", "secret OCID"),
    ],
)
def test_oci_vault_rejects_ambiguous_references(reference: str, message: str) -> None:
    with pytest.raises(SecretResolutionError, match=message):
        _runtime(_Client()).store.get_secret(reference)


@pytest.mark.parametrize(
    ("content", "content_type", "message"),
    [
        ("not base64", "BASE64", "valid UTF-8"),
        (None, "BASE64", "base64 text"),
        (base64.b64encode(b"").decode("ascii"), "BASE64", "empty"),
        (base64.b64encode(b"value").decode("ascii"), "PLAIN_TEXT", "base64 text"),
    ],
)
def test_oci_vault_rejects_invalid_or_empty_content(
    content: str | None,
    content_type: str,
    message: str,
) -> None:
    client = _Client()
    client.content = content  # type: ignore[assignment]
    response = client.get_secret_bundle(secret_id=_TEST_RESOURCE_OCID, stage="CURRENT")
    response.data.secret_bundle_content.content_type = content_type

    class InvalidClient(_Client):
        def get_secret_bundle(self, *, secret_id: str, stage: str) -> _Response:
            return response

    with pytest.raises(SecretResolutionError, match=message):
        _runtime(InvalidClient()).store.get_secret(f"oci-vault://{_TEST_RESOURCE_OCID}")


def test_oci_vault_resolver_cannot_cross_vaults() -> None:
    runtime = _runtime(_Client())
    runtime.store.get_secret(f"oci-vault://{_VAULT_ID}/secrets/postgres-dsn")
    other = "ocid1.vault.oc1.iad." + "c" * 32

    with pytest.raises(SecretResolutionError, match="cross vault"):
        runtime.store.get_secret(f"oci-vault://{other}/secrets/postgres-dsn")
