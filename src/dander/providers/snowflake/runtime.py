"""Snowflake implementation of Dander's warehouse capability bundle."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dander.providers.registry import (
    PROVIDER_API_VERSION,
    ProviderFactory,
    ProviderFactoryError,
    ProviderKind,
)
from dander.providers.snowflake.config import (
    SnowflakeKeyPairAuth,
    SnowflakeOAuthAuth,
    SnowflakeWarehouseConfig,
)
from dander.providers.snowflake.fence import SnowflakeTargetFence
from dander.providers.snowflake.session import (
    SnowflakeConnectionFactory,
    execute,
    open_connection,
)
from dander.providers.snowflake.transform import SnowflakeTransformRunner
from dander.providers.snowflake.writer import (
    SnowflakeScd1Writer,
    SnowflakeStagingSettings,
    default_staging_settings,
)
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.warehouse import CanonicalField, LogicalTypeKind, RelationRef, RelationSchema
from dander.warehouse.runtime import (
    WarehouseCapabilities,
    WarehouseRuntime,
    WarehouseSchemaSupport,
)
from dander.writer import SchemaEvolution, WriteField, WriteMode, WritePattern, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import BaseModel


SNOWFLAKE_SCHEMA_SUPPORT = WarehouseSchemaSupport(
    provider_id="snowflake",
    logical_types=frozenset(
        {
            LogicalTypeKind.BOOLEAN,
            LogicalTypeKind.INTEGER,
            LogicalTypeKind.DECIMAL,
            LogicalTypeKind.FLOAT,
            LogicalTypeKind.STRING,
            LogicalTypeKind.BINARY,
            LogicalTypeKind.DATE,
            LogicalTypeKind.TIME,
            LogicalTypeKind.TIMESTAMP,
        }
    ),
    max_decimal_precision=38,
    max_temporal_precision=9,
)


@dataclass(frozen=True, slots=True)
class SnowflakeRelationCodec:
    """Render one database-local Snowflake relation coordinate."""

    database: str
    provider_id: str = "snowflake"

    def render(self, relation: RelationRef) -> str:
        if relation.catalog != self.database:
            raise ValueError("Snowflake relation belongs to another database")
        return ".".join(_quote(value) for value in relation.coordinates)


@dataclass(frozen=True, slots=True)
class SnowflakeSchemaMapper:
    """Map compatibility declarations into canonical schema v1."""

    def canonical_schema(self, fields: Sequence[object]) -> RelationSchema:
        canonical: list[CanonicalField] = []
        for field in fields:
            if isinstance(field, WriteField):
                canonical.append(field.to_canonical())
            elif isinstance(field, CanonicalField):
                canonical.append(field)
            else:
                raise TypeError("Snowflake schema mapper received an unsupported field")
        return SNOWFLAKE_SCHEMA_SUPPORT.require(RelationSchema(fields=tuple(canonical)))


@dataclass(frozen=True, slots=True)
class SnowflakeWriterFactory:
    """Construct the bounded Snowflake SCD1 ingestion writer."""

    database: str
    connection_factory: SnowflakeConnectionFactory
    target_fence: SnowflakeTargetFence
    staging: SnowflakeStagingSettings

    def build_ingestion_writer(
        self,
        *,
        sandbox: bool,
        batch_rows: int,
        schema_evolution: SchemaEvolution,
    ) -> WritePattern:
        del batch_rows
        if sandbox:
            raise ValueError("Snowflake warehouse does not use Dander's BigQuery sandbox mode")
        return SnowflakeScd1Writer(
            database=self.database,
            connection_factory=self.connection_factory,
            target_fence=self.target_fence,
            schema_evolution=schema_evolution,
            staging=self.staging,
        )


@dataclass(frozen=True, slots=True)
class SnowflakeTransformFactory:
    """Construct the experimental fenced Snowflake transform runner."""

    database: str
    connection_factory: SnowflakeConnectionFactory
    target_fence: SnowflakeTargetFence

    def build_transform_runner(
        self,
        *,
        graph_plan: object | None,
        build_models: bool,
        raw_namespace: str = "raw",
    ) -> SnowflakeTransformRunner | None:
        if graph_plan is not None:
            raise ValueError(
                "Snowflake graph execution is not available in this experimental slice"
            )
        if not build_models:
            return None
        return SnowflakeTransformRunner(
            database=self.database,
            connection_factory=self.connection_factory,
            target_fence=self.target_fence,
            raw_namespace=raw_namespace,
        )


@dataclass(frozen=True, slots=True)
class SnowflakeTelemetry:
    """Normalize bounded connector counters without retaining SQL or response payloads."""

    warehouse: str

    def operation(
        self,
        job: object,
        *,
        operation: TelemetryOperation,
        duration_ms: int = 0,
        retry_count: int = 0,
    ) -> OperationTelemetry:
        rowcount = getattr(job, "rowcount", 0)
        query_id = getattr(job, "query_id", None) or getattr(job, "sfqid", None)
        affected = rowcount if isinstance(rowcount, int) and rowcount >= 0 else 0
        return OperationTelemetry(
            provider="snowflake",
            operation=operation,
            duration_ms=duration_ms,
            retry_count=retry_count,
            rows_affected=affected,
            query_id=query_id if isinstance(query_id, str) else None,
        )


SNOWFLAKE_CAPABILITIES = WarehouseCapabilities(
    provider_id="snowflake",
    schema_contract_version=1,
    write_modes=frozenset({WriteMode.SCD1}),
    transports=frozenset({WriteTransport.COPY}),
    supports_transforms=True,
    supports_graphs=False,
    supports_target_fencing=True,
    schema_support=SNOWFLAKE_SCHEMA_SUPPORT,
)


def build_snowflake_warehouse(
    config: BaseModel,
    context: Mapping[str, object],
) -> WarehouseRuntime:
    """Build a credential-referenced Snowflake runtime or injected conformance session."""
    if not isinstance(config, SnowflakeWarehouseConfig):
        raise TypeError("Snowflake warehouse factory received the wrong configuration")
    catalog = context.get("catalog")
    if not isinstance(catalog, str) or catalog.casefold() != config.database.casefold():
        raise ValueError("Snowflake warehouse factory requires its configured database catalog")
    supplied_factory = context.get("connection_factory")
    if supplied_factory is None:
        connection_factory = _sdk_connection_factory(config)
    elif callable(supplied_factory):
        connection_factory = cast("SnowflakeConnectionFactory", supplied_factory)
    else:
        raise TypeError("Snowflake context connection_factory must be callable")

    try:
        with open_connection(connection_factory) as connection:
            current = execute(
                connection,
                "SELECT CURRENT_DATABASE(), CURRENT_WAREHOUSE()",
                fetch="one",
            ).row
    except Exception as error:
        raise ProviderFactoryError("Snowflake connection validation failed") from error
    if not isinstance(current, (tuple, list)) or len(current) < 2:
        raise ProviderFactoryError("Snowflake connection validation returned an invalid result")
    if str(current[0]).casefold() != config.database.casefold():
        raise ProviderFactoryError("Snowflake connection selected the wrong database")
    if str(current[1]).casefold() != config.warehouse.casefold():
        raise ProviderFactoryError("Snowflake connection selected the wrong warehouse")

    target_fence = SnowflakeTargetFence(
        connection_factory=connection_factory,
        database=config.database,
    )
    staging = default_staging_settings(
        max_rows_per_file=config.max_rows_per_file,
        max_logical_bytes_per_file=config.max_logical_bytes_per_file,
        compression=config.compression,
    )
    supplied_root = context.get("staging_root")
    if supplied_root is not None:
        if not isinstance(supplied_root, Path):
            raise TypeError("Snowflake context staging_root must be a pathlib.Path")
        staging = SnowflakeStagingSettings(
            root=supplied_root,
            max_rows_per_file=staging.max_rows_per_file,
            max_logical_bytes_per_file=staging.max_logical_bytes_per_file,
            compression=staging.compression,
        )
    schema_mapper = SnowflakeSchemaMapper()
    return WarehouseRuntime(
        provider_id="snowflake",
        relation_codec=SnowflakeRelationCodec(config.database),
        schema_mapper=schema_mapper,
        writers=SnowflakeWriterFactory(
            config.database,
            connection_factory,
            target_fence,
            staging,
        ),
        transforms=SnowflakeTransformFactory(
            config.database,
            connection_factory,
            target_fence,
        ),
        target_fence=target_fence,
        telemetry=SnowflakeTelemetry(config.warehouse),
        capabilities=SNOWFLAKE_CAPABILITIES,
        ingestion_schema_mapper=schema_mapper,
    )


def _sdk_connection_factory(config: SnowflakeWarehouseConfig) -> SnowflakeConnectionFactory:
    parameters: dict[str, object] = {
        "account": config.account,
        "user": config.user,
        "database": config.database,
        "warehouse": config.warehouse,
        "paramstyle": "qmark",
        "login_timeout": config.login_timeout_seconds,
        "network_timeout": config.network_timeout_seconds,
        "validate_default_parameters": True,
        "session_parameters": {
            "STATEMENT_TIMEOUT_IN_SECONDS": config.statement_timeout_seconds,
            "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": config.queued_timeout_seconds,
            "TIMEZONE": "UTC",
        },
    }
    if config.role is not None:
        parameters["role"] = config.role
    if isinstance(config.auth, SnowflakeOAuthAuth):
        token = os.environ.get(config.auth.token_env)
        if not token:
            raise ProviderFactoryError(
                f"Snowflake OAuth requires a token in {config.auth.token_env}"
            )
        parameters.update({"authenticator": "oauth", "token": token})
    elif isinstance(config.auth, SnowflakeKeyPairAuth):
        key_file = os.environ.get(config.auth.private_key_file_env)
        if not key_file:
            raise ProviderFactoryError(
                "Snowflake key-pair authentication requires its configured key-file reference"
            )
        parameters["private_key_file"] = key_file
        if config.auth.private_key_password_env is not None:
            password = os.environ.get(config.auth.private_key_password_env)
            if not password:
                raise ProviderFactoryError(
                    "Snowflake key-pair authentication requires its configured password reference"
                )
            parameters["private_key_file_pwd"] = password

    def connect() -> object:
        try:
            import snowflake.connector  # type: ignore[import-not-found]
        except ModuleNotFoundError as error:
            raise ProviderFactoryError(
                "Snowflake warehouse requires the dander-platform[snowflake] extra"
            ) from error
        return snowflake.connector.connect(**parameters)

    return cast("SnowflakeConnectionFactory", connect)


SNOWFLAKE_WAREHOUSE_FACTORY = ProviderFactory[WarehouseRuntime](
    kind=ProviderKind.WAREHOUSE,
    provider_id="snowflake",
    api_version=PROVIDER_API_VERSION,
    build=build_snowflake_warehouse,
)


def _quote(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


__all__ = [
    "SNOWFLAKE_CAPABILITIES",
    "SNOWFLAKE_SCHEMA_SUPPORT",
    "SNOWFLAKE_WAREHOUSE_FACTORY",
    "SnowflakeRelationCodec",
    "SnowflakeSchemaMapper",
]
