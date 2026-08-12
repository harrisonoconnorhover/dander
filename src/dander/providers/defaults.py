"""Built-in provider registrations with lazy implementation loading."""

from dander.providers.aws_secrets_manager import AwsSecretsManagerConfig
from dander.providers.azure_container_apps import AzureContainerAppsLauncherConfig
from dander.providers.azure_key_vault import AzureKeyVaultConfig
from dander.providers.bigquery import BigQueryStateConfig, BigQueryWarehouseConfig
from dander.providers.cloud_run import CloudRunLauncherConfig
from dander.providers.dataplex import DataplexCatalogConfig
from dander.providers.environment_secrets import EnvironmentSecretConfig
from dander.providers.fargate import FargateLauncherConfig
from dander.providers.gcp_secret_manager import GcpSecretManagerConfig
from dander.providers.glue import GlueCatalogConfig
from dander.providers.kubernetes import KubernetesLauncherConfig
from dander.providers.no_catalog import NoCatalogConfig
from dander.providers.oci_vault import OciVaultConfig
from dander.providers.postgresql import PostgreSQLStateConfig, PostgreSQLWarehouseConfig
from dander.providers.redshift import RedshiftWarehouseConfig
from dander.providers.registry import ProviderKind, ProviderRegistry, lazy_provider_factory
from dander.providers.snowflake import SnowflakeWarehouseConfig


def default_provider_registry() -> ProviderRegistry:
    """Return fresh built-in registrations without importing provider SDK modules."""
    registry = ProviderRegistry()
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="bigquery",
        config_model=BigQueryWarehouseConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.bigquery.runtime:BIGQUERY_WAREHOUSE_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.STATE,
        provider_id="bigquery",
        config_model=BigQueryStateConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.bigquery.state:BIGQUERY_STATE_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="snowflake",
        config_model=SnowflakeWarehouseConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.snowflake.runtime:SNOWFLAKE_WAREHOUSE_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="redshift",
        config_model=RedshiftWarehouseConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.redshift.runtime:REDSHIFT_WAREHOUSE_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.WAREHOUSE,
        provider_id="postgresql",
        config_model=PostgreSQLWarehouseConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.postgresql.runtime:POSTGRESQL_WAREHOUSE_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.STATE,
        provider_id="postgresql",
        config_model=PostgreSQLStateConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.postgresql.state:POSTGRESQL_STATE_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.CATALOG,
        provider_id="dataplex",
        config_model=DataplexCatalogConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.dataplex.runtime:DATAPLEX_CATALOG_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.CATALOG,
        provider_id="none",
        config_model=NoCatalogConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.no_catalog.runtime:NO_CATALOG_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.CATALOG,
        provider_id="glue",
        config_model=GlueCatalogConfig,
        load_factory=lazy_provider_factory("dander.providers.glue.runtime:GLUE_CATALOG_FACTORY"),
    )
    registry.register(
        kind=ProviderKind.SECRETS,
        provider_id="gcp_secret_manager",
        config_model=GcpSecretManagerConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.gcp_secret_manager.runtime:GCP_SECRET_MANAGER_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.SECRETS,
        provider_id="environment",
        config_model=EnvironmentSecretConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.environment_secrets.runtime:ENVIRONMENT_SECRET_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.SECRETS,
        provider_id="aws_secret_manager",
        config_model=AwsSecretsManagerConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.aws_secrets_manager.runtime:AWS_SECRET_MANAGER_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.SECRETS,
        provider_id="azure_key_vault",
        config_model=AzureKeyVaultConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.azure_key_vault.runtime:AZURE_KEY_VAULT_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.SECRETS,
        provider_id="oci_vault",
        config_model=OciVaultConfig,
        load_factory=lazy_provider_factory("dander.providers.oci_vault.runtime:OCI_VAULT_FACTORY"),
    )
    registry.register(
        kind=ProviderKind.LAUNCHER,
        provider_id="cloud_run",
        config_model=CloudRunLauncherConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.cloud_run.runtime:CLOUD_RUN_LAUNCHER_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.LAUNCHER,
        provider_id="fargate",
        config_model=FargateLauncherConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.fargate.runtime:FARGATE_LAUNCHER_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.LAUNCHER,
        provider_id="kubernetes",
        config_model=KubernetesLauncherConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.kubernetes.runtime:KUBERNETES_LAUNCHER_FACTORY"
        ),
    )
    registry.register(
        kind=ProviderKind.LAUNCHER,
        provider_id="azure_container_apps",
        config_model=AzureContainerAppsLauncherConfig,
        load_factory=lazy_provider_factory(
            "dander.providers.azure_container_apps.runtime:AZURE_CONTAINER_APPS_LAUNCHER_FACTORY"
        ),
    )
    return registry
