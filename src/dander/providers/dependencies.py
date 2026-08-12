"""Distribution-only dependency assembly for Dander provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version


class RuntimeDependencyError(RuntimeError):
    """The full runtime image is missing one or more declared provider dependencies."""


@dataclass(frozen=True, slots=True)
class ProviderDependencySet:
    """One public package extra and the distributions it installs."""

    extra: str
    distributions: tuple[str, ...]


PROVIDER_DEPENDENCY_SETS = (
    ProviderDependencySet(
        extra="bigquery",
        distributions=("dlt", "google-cloud-bigquery", "google-cloud-bigquery-storage"),
    ),
    ProviderDependencySet(
        extra="snowflake",
        distributions=("pyarrow", "snowflake-connector-python"),
    ),
    ProviderDependencySet(
        extra="redshift",
        distributions=("boto3", "pyarrow", "redshift-connector"),
    ),
    ProviderDependencySet(
        extra="postgres",
        distributions=("psycopg",),
    ),
    ProviderDependencySet(
        extra="gcp",
        distributions=(
            "google-auth",
            "google-cloud-dataplex",
            "google-cloud-secret-manager",
        ),
    ),
    ProviderDependencySet(extra="aws", distributions=("boto3",)),
    ProviderDependencySet(
        extra="azure",
        distributions=("azure-identity", "azure-keyvault-secrets"),
    ),
    ProviderDependencySet(extra="oci", distributions=("oci",)),
)

FULL_RUNTIME_DISTRIBUTIONS = tuple(
    sorted(
        {
            distribution
            for dependency_set in PROVIDER_DEPENDENCY_SETS
            for distribution in dependency_set.distributions
        }
    )
)


def installed_runtime_dependencies() -> dict[str, str | None]:
    """Inspect distribution metadata without importing any provider SDK."""
    installed: dict[str, str | None] = {}
    for distribution in FULL_RUNTIME_DISTRIBUTIONS:
        try:
            installed[distribution] = _distribution_version(distribution)
        except PackageNotFoundError:
            installed[distribution] = None
    return installed


def require_full_runtime() -> dict[str, str]:
    """Fail a full-image build when its declared provider dependency set is incomplete."""
    inspected = installed_runtime_dependencies()
    missing = tuple(name for name, version in inspected.items() if version is None)
    if missing:
        raise RuntimeDependencyError(
            "Full runtime is missing provider distributions: " + ", ".join(missing)
        )
    return {name: version for name, version in inspected.items() if version is not None}
