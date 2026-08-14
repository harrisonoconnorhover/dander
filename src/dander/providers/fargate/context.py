"""Typed provider assembly for the qualified Fargate profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.providers.aws_secrets_manager.config import AwsSecretsManagerConfig
from dander.providers.bigquery.config import BigQueryStateConfig, BigQueryWarehouseConfig
from dander.providers.dataplex.config import DataplexCatalogConfig
from dander.providers.gcp_secret_manager.config import GcpSecretManagerConfig
from dander.providers.glue.config import GlueCatalogConfig
from dander.providers.postgresql.config import PostgreSQLStateConfig
from dander.providers.redshift.config import RedshiftWarehouseConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dander.providers.gcp_launcher import GcpLauncherContext

_CONTEXT_KEY = "fargate_profile"


@dataclass(frozen=True, slots=True)
class FargateProfileContext:
    """One exact data-plane composition selected for a Fargate launcher."""

    profile_id: str
    warehouse: BigQueryWarehouseConfig | RedshiftWarehouseConfig
    state: BigQueryStateConfig | PostgreSQLStateConfig
    catalog: DataplexCatalogConfig | GlueCatalogConfig
    secrets: GcpSecretManagerConfig | AwsSecretsManagerConfig
    gcp: GcpLauncherContext | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"^[a-z][a-z0-9_]{0,62}$", self.profile_id):
            raise ValueError("Fargate profile id is invalid")
        if self.is_gcp:
            if self.gcp is None:
                raise ValueError("Fargate GCP profile requires a GCP launcher context")
            return
        if self.is_aws_native:
            if self.gcp is not None:
                raise ValueError("Fargate AWS-native profile cannot receive GCP context")
            return
        raise ValueError("Fargate profile uses an unsupported provider composition")

    @property
    def is_gcp(self) -> bool:
        return (
            isinstance(self.warehouse, BigQueryWarehouseConfig)
            and isinstance(self.state, BigQueryStateConfig)
            and isinstance(self.catalog, DataplexCatalogConfig)
            and isinstance(self.secrets, GcpSecretManagerConfig)
        )

    @property
    def is_aws_native(self) -> bool:
        return (
            isinstance(self.warehouse, RedshiftWarehouseConfig)
            and isinstance(self.state, PostgreSQLStateConfig)
            and isinstance(self.catalog, GlueCatalogConfig)
            and isinstance(self.secrets, AwsSecretsManagerConfig)
        )


def fargate_profile_factory_context(context: FargateProfileContext) -> dict[str, object]:
    """Return the registry construction context for a typed Fargate profile."""
    return {_CONTEXT_KEY: context}


def optional_fargate_profile_context(
    context: Mapping[str, object],
) -> FargateProfileContext | None:
    """Read and validate an optional typed Fargate profile."""
    value = context.get(_CONTEXT_KEY)
    if value is None:
        return None
    if not isinstance(value, FargateProfileContext):
        raise TypeError("Fargate launcher received an invalid typed profile context")
    return value


def require_fargate_profile_context(
    context: Mapping[str, object],
) -> FargateProfileContext:
    """Require one typed Fargate profile in a registry construction context."""
    value = optional_fargate_profile_context(context)
    if value is None:
        raise TypeError("Fargate launcher requires a typed profile context")
    return value


__all__ = [
    "FargateProfileContext",
    "fargate_profile_factory_context",
    "optional_fargate_profile_context",
    "require_fargate_profile_context",
]
