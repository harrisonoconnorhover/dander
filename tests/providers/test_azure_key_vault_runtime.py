"""Azure Key Vault provider selection and safe-reference coverage."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

import pytest

from dander.providers import ProviderKind, default_provider_registry
from dander.security import SecretResolutionError, SecretRuntime


@dataclass
class _Secret:
    value: str | None


class _Client:
    def __init__(self, value: str | None = "resolved-value") -> None:
        self.value = value
        self.requests: list[tuple[str, str | None]] = []

    def get_secret(self, name: str, version: str | None = None) -> _Secret:
        self.requests.append((name, version))
        return _Secret(self.value)


def _runtime(client: _Client) -> SecretRuntime:
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.SECRETS, {"provider": "azure_key_vault"})
    runtime = registry.build(ProviderKind.SECRETS, config, context={"client": client})
    assert isinstance(runtime, SecretRuntime)
    return runtime


def test_azure_key_vault_provider_is_lazy_and_audits_without_secret_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime_module = "dander.providers.azure_key_vault.runtime"
    sys.modules.pop(runtime_module, None)
    sys.modules.pop("azure.identity", None)
    sys.modules.pop("azure.keyvault.secrets", None)
    client = _Client()
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.SECRETS, {"provider": "azure_key_vault"})

    assert runtime_module not in sys.modules
    runtime = registry.build(ProviderKind.SECRETS, config, context={"client": client})
    assert isinstance(runtime, SecretRuntime)
    assert runtime_module in sys.modules
    assert "azure.identity" not in sys.modules
    assert "azure.keyvault.secrets" not in sys.modules
    reference = (
        "azure-kv://https://dander-phase6-kv.vault.azure.net/secrets/snowflake-token/version1"
    )
    with caplog.at_level(logging.INFO):
        assert runtime.store.get_secret(reference) == "resolved-value"

    assert client.requests == [("snowflake-token", "version1")]
    assert caplog.records[-1].__dict__["secret_backend"] == "azure_key_vault"
    assert "resolved-value" not in caplog.text
    assert runtime.capabilities.reference_forms == frozenset({"azure_key_vault_secret_uri"})


@pytest.mark.parametrize(
    ("reference", "client", "message"),
    [
        ("azure-kv://short-name", _Client(), "full"),
        (
            "azure-kv://https://dander--vault.vault.azure.net/secrets/token",
            _Client(),
            "full",
        ),
        (
            "azure-kv://https://dander-phase6-kv.vault.azure.net/secrets/token",
            _Client(value=None),
            "empty",
        ),
    ],
)
def test_azure_key_vault_rejects_ambiguous_or_non_text_secrets(
    reference: str,
    client: _Client,
    message: str,
) -> None:
    with pytest.raises(SecretResolutionError, match=message):
        _runtime(client).store.get_secret(reference)


def test_azure_key_vault_resolver_cannot_cross_vaults() -> None:
    runtime = _runtime(_Client())
    runtime.store.get_secret("azure-kv://https://dander-phase6-kv.vault.azure.net/secrets/token")

    with pytest.raises(SecretResolutionError, match="cross vault"):
        runtime.store.get_secret("azure-kv://https://other-phase6-kv.vault.azure.net/secrets/token")
