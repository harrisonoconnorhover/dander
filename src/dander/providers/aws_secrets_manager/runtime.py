"""AWS Secrets Manager resolution selected through the provider registry."""

from __future__ import annotations

import re
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from dander.providers.aws_secrets_manager.config import AwsSecretsManagerConfig
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.security.runtime import SecretCapabilities, SecretRuntime
from dander.security.secret_manager import (
    EnvironmentSecretStore,
    SecretResolutionError,
    audit_secret_access,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from pydantic import BaseModel

_AWS_REFERENCE = re.compile(
    r"^aws-sm://arn:(?:aws|aws-us-gov):secretsmanager:(?P<region>[a-z0-9-]+):[0-9]{12}:"
    r"secret:[A-Za-z0-9/_+=.@-]{1,512}$"
)


class _SecretsManagerClient(Protocol):
    def get_secret_value(self, *, SecretId: str) -> Mapping[str, object]:  # noqa: N803
        """Return one AWS Secrets Manager value."""


class AwsSecretStore:
    """Resolve text secrets by fully qualified AWS Secrets Manager ARN."""

    def __init__(self, *, region: str, client: _SecretsManagerClient | None = None) -> None:
        self._region = region
        self._client = client

    def get_secret(self, reference: str) -> str:
        """Resolve an `aws-sm://` ARN without accepting ambiguous secret names."""
        match = _AWS_REFERENCE.fullmatch(reference)
        if match is None:
            raise SecretResolutionError("AWS secret references must use a full aws-sm:// ARN")
        if match.group("region") != self._region:
            raise SecretResolutionError("AWS secret reference region does not match the provider")
        arn = reference.removeprefix("aws-sm://")
        response = self._secrets_client().get_secret_value(SecretId=arn)
        value = response.get("SecretString")
        if not isinstance(value, str) or not value:
            raise SecretResolutionError("AWS secret value is missing or is not text")
        audit_secret_access(reference, "aws_secret_manager")
        return value

    def _secrets_client(self) -> _SecretsManagerClient:
        if self._client is None:
            boto3 = cast("Any", import_module("boto3"))
            self._client = cast(
                "_SecretsManagerClient",
                boto3.client("secretsmanager", region_name=self._region),
            )
        return self._client


class AwsSecretRuntimeStore:
    """Resolve direct AWS ARNs or environment names that contain an AWS ARN."""

    def __init__(
        self,
        *,
        aws: AwsSecretStore,
        environment: EnvironmentSecretStore | None = None,
    ) -> None:
        self._aws = aws
        self._environment = environment or EnvironmentSecretStore()

    def get_secret(self, reference: str) -> str:
        """Resolve one AWS secret reference with legacy environment indirection."""
        if reference.startswith("aws-sm://"):
            return self._aws.get_secret(reference)
        value = self._environment.get_secret(reference)
        if value.startswith("aws-sm://"):
            return self._aws.get_secret(value)
        return value


def build_aws_secret_manager(
    config: BaseModel,
    context: Mapping[str, object],
) -> SecretRuntime:
    """Build AWS resolution without loading boto3 or making a provider call."""
    if not isinstance(config, AwsSecretsManagerConfig):
        raise TypeError("AWS Secrets Manager factory received the wrong configuration")
    environment_value = context.get("environment")
    client = context.get("client")
    environment = (
        EnvironmentSecretStore(cast("Mapping[str, str]", environment_value))
        if environment_value is not None
        else None
    )
    return SecretRuntime(
        provider_id="aws_secret_manager",
        store=AwsSecretRuntimeStore(
            aws=AwsSecretStore(
                region=config.region,
                client=cast("_SecretsManagerClient", client) if client is not None else None,
            ),
            environment=environment,
        ),
        capabilities=SecretCapabilities(
            provider_id="aws_secret_manager",
            reference_forms=frozenset({"aws_secret_arn", "environment_name"}),
            environment_indirection=True,
            audited_access=True,
        ),
    )


AWS_SECRET_MANAGER_FACTORY: ProviderFactory[SecretRuntime] = ProviderFactory(
    kind=ProviderKind.SECRETS,
    provider_id="aws_secret_manager",
    api_version=PROVIDER_API_VERSION,
    build=build_aws_secret_manager,
)

__all__ = ["AWS_SECRET_MANAGER_FACTORY"]
