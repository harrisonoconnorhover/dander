"""Secret-provider selection and composition coverage."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from dander.cli.provider_runtime import build_secret_store
from dander.providers import ProviderKind, default_provider_registry
from dander.security import SecretResolutionError, SecretRuntime

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


class _AwsClient:
    def __init__(self, value: object = "resolved-value") -> None:
        self.value = value
        self.secret_ids: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> dict[str, object]:  # noqa: N803
        self.secret_ids.append(SecretId)
        return {"SecretString": self.value}


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


def test_aws_provider_is_lazy_and_audits_environment_indirection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime_module = "dander.providers.aws_secrets_manager.runtime"
    sys.modules.pop(runtime_module, None)
    sys.modules.pop("boto3", None)
    client = _AwsClient()
    reference = "aws-sm://arn:aws:secretsmanager:us-east-1:123456789012:secret:dander/source-token"
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.SECRETS,
        {"provider": "aws_secret_manager", "region": "us-east-1"},
    )

    assert runtime_module not in sys.modules
    runtime = registry.build(
        ProviderKind.SECRETS,
        config,
        context={"environment": {"DANDER_TEST_TOKEN": reference}, "client": client},
    )
    assert isinstance(runtime, SecretRuntime)
    assert "boto3" not in sys.modules
    with caplog.at_level(logging.INFO):
        assert runtime.store.get_secret("DANDER_TEST_TOKEN") == "resolved-value"

    assert client.secret_ids == [reference.removeprefix("aws-sm://")]
    assert caplog.records[-1].__dict__["secret_backend"] == "aws_secret_manager"
    assert "resolved-value" not in caplog.text
    assert runtime.capabilities.reference_forms == frozenset({"aws_secret_arn", "environment_name"})


@pytest.mark.parametrize(
    ("reference", "client", "message"),
    [
        ("aws-sm://short-name", _AwsClient(), "full"),
        (
            "aws-sm://arn:aws:secretsmanager:us-west-2:123456789012:secret:dander/token",
            _AwsClient(),
            "region",
        ),
        (
            "aws-sm://arn:aws:secretsmanager:us-east-1:123456789012:secret:dander/token",
            _AwsClient(value=b"binary"),
            "not text",
        ),
    ],
)
def test_aws_provider_rejects_ambiguous_cross_region_or_binary_secrets(
    reference: str,
    client: _AwsClient,
    message: str,
) -> None:
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.SECRETS,
        {"provider": "aws_secret_manager", "region": "us-east-1"},
    )
    runtime = registry.build(
        ProviderKind.SECRETS,
        config,
        context={"client": client, "environment": {}},
    )
    assert isinstance(runtime, SecretRuntime)

    with pytest.raises(SecretResolutionError, match=message):
        runtime.store.get_secret(reference)
