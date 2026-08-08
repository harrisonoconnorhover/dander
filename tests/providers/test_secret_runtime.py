"""Secret-provider selection and composition coverage."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.cli.provider_runtime import build_secret_store
from dander.providers import ProviderKind, default_provider_registry
from dander.security import SecretRuntime

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass
class _Payload:
    data: bytes


@dataclass
class _Response:
    payload: _Payload


class _Client:
    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []

    def access_secret_version(self, *, request: Mapping[str, str]) -> _Response:
        self.requests.append(dict(request))
        return _Response(payload=_Payload(data=b"resolved-value"))


def test_environment_provider_loads_without_gcp_runtime_or_sdk() -> None:
    gcp_runtime = "dander.providers.gcp_secret_manager.runtime"
    google_sdk = "google.cloud.secretmanager"
    environment_runtime = "dander.providers.environment_secrets.runtime"
    sys.modules.pop(gcp_runtime, None)
    sys.modules.pop(google_sdk, None)
    sys.modules.pop(environment_runtime, None)
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.SECRETS, {"provider": "environment"})

    runtime = registry.build(
        ProviderKind.SECRETS,
        config,
        context={"environment": {"DANDER_TEST_TOKEN": "resolved-value"}},
    )

    assert isinstance(runtime, SecretRuntime)
    assert runtime.store.get_secret("DANDER_TEST_TOKEN") == "resolved-value"
    assert runtime.capabilities.environment_indirection is False
    assert environment_runtime in sys.modules
    assert gcp_runtime not in sys.modules
    assert google_sdk not in sys.modules


def test_gcp_provider_preserves_environment_indirection_without_sdk_eagerness() -> None:
    google_sdk = "google.cloud.secretmanager"
    sys.modules.pop(google_sdk, None)
    reference = "projects/unit/secrets/runtime/versions/latest"
    client = _Client()
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.SECRETS, {"provider": "gcp_secret_manager"})

    runtime = registry.build(
        ProviderKind.SECRETS,
        config,
        context={"environment": {"DANDER_TEST_TOKEN": reference}, "client": client},
    )

    assert isinstance(runtime, SecretRuntime)
    assert runtime.store.get_secret("DANDER_TEST_TOKEN") == "resolved-value"
    assert client.requests == [{"name": reference}]
    assert runtime.capabilities.environment_indirection is True
    assert google_sdk not in sys.modules


def test_cli_builds_environment_store_through_provider_registry() -> None:
    store = build_secret_store("environment")
    assert type(store).__name__ == "EnvironmentSecretStore"
