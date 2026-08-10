"""Amazon Redshift implementation of Dander's warehouse capability bundle."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dander.providers.redshift.config import RedshiftWarehouseConfig, validate_redshift_relation
from dander.providers.redshift.fence import RedshiftTargetFence
from dander.providers.redshift.session import (
    RedshiftConnectionFactory,
    execute,
    open_connection,
)
from dander.providers.redshift.transform import RedshiftGraphRunner, RedshiftTransformRunner
from dander.providers.redshift.writer import (
    RedshiftS3Client,
    RedshiftScd1Writer,
    RedshiftStagedWriter,
    RedshiftStagingSettings,
    RedshiftWriteError,
    default_staging_settings,
    validate_redshift_schema,
)
from dander.providers.registry import (
    PROVIDER_API_VERSION,
    ProviderFactory,
    ProviderFactoryError,
    ProviderKind,
)
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.warehouse import CanonicalField, LogicalTypeKind, RelationRef, RelationSchema
from dander.warehouse.runtime import (
    WarehouseCapabilities,
    WarehouseRuntime,
    WarehouseSchemaSupport,
    WarehouseSchemaSupportError,
)
from dander.writer import SchemaEvolution, WriteField, WriteMode, WritePattern, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import BaseModel


REDSHIFT_SCHEMA_SUPPORT = WarehouseSchemaSupport(
    provider_id="redshift",
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
    max_temporal_precision=6,
)


@dataclass(frozen=True, slots=True)
class RedshiftRelationCodec:
    """Render canonical database/schema/relation coordinates for Redshift."""

    database: str
    provider_id: str = "redshift"

    def render(self, relation: RelationRef) -> str:
        validate_redshift_relation(relation)
        if relation.catalog != self.database:
            raise ValueError("Redshift relation belongs to another database")
        return ".".join(_quote(value) for value in relation.coordinates)


@dataclass(frozen=True, slots=True)
class RedshiftSchemaMapper:
    """Map compatibility declarations into canonical schema v1."""

    def canonical_schema(self, fields: Sequence[object]) -> RelationSchema:
        canonical: list[CanonicalField] = []
        for field in fields:
            if isinstance(field, WriteField):
                canonical.append(field.to_canonical())
            elif isinstance(field, CanonicalField):
                canonical.append(field)
            else:
                raise TypeError("Redshift schema mapper received an unsupported field")
        schema = RelationSchema(fields=tuple(canonical))
        try:
            validate_redshift_schema(schema)
        except RedshiftWriteError as error:
            raise WarehouseSchemaSupportError(str(error)) from error
        return schema


@dataclass(frozen=True, slots=True)
class RedshiftWriterFactory:
    """Construct Redshift's bounded direct-or-Parquet/COPY ingestion writer."""

    database: str
    connection_factory: RedshiftConnectionFactory
    s3_client: RedshiftS3Client
    target_fence: RedshiftTargetFence
    staging: RedshiftStagingSettings

    def build_ingestion_writer(
        self,
        *,
        sandbox: bool,
        batch_rows: int,
        schema_evolution: SchemaEvolution,
        mode: WriteMode = WriteMode.SCD1,
        cursor_field: str | None = None,
        snapshot_field: str | None = None,
    ) -> WritePattern:
        del batch_rows
        if sandbox:
            raise ValueError("Redshift warehouse does not use Dander's BigQuery sandbox mode")
        if mode is WriteMode.INCREMENTAL:
            if cursor_field is None or not cursor_field.strip():
                raise ValueError("Redshift incremental writes require cursor_field")
        elif cursor_field is not None:
            raise ValueError("cursor_field is valid only for Redshift incremental writes")
        if mode is WriteMode.SNAPSHOT:
            if snapshot_field is None or not snapshot_field.strip():
                raise ValueError("Redshift snapshot writes require snapshot_field")
        elif snapshot_field is not None:
            raise ValueError("snapshot_field is valid only for Redshift snapshot writes")
        if mode is WriteMode.SCD1:
            return RedshiftScd1Writer(
                database=self.database,
                connection_factory=self.connection_factory,
                s3_client=self.s3_client,
                target_fence=self.target_fence,
                schema_evolution=schema_evolution,
                staging=self.staging,
            )
        return RedshiftStagedWriter(
            database=self.database,
            connection_factory=self.connection_factory,
            s3_client=self.s3_client,
            target_fence=self.target_fence,
            schema_evolution=schema_evolution,
            staging=self.staging,
            mode=mode,
            cursor_field=cursor_field,
            snapshot_field=snapshot_field,
        )


@dataclass(frozen=True, slots=True)
class RedshiftTransformFactory:
    """Construct fenced Redshift model or provider-neutral graph execution."""

    database: str
    connection_factory: RedshiftConnectionFactory
    target_fence: RedshiftTargetFence
    statement_timeout_ms: int

    def build_transform_runner(
        self,
        *,
        graph_plan: object | None,
        build_models: bool,
        raw_namespace: str = "raw",
    ) -> RedshiftGraphRunner | RedshiftTransformRunner | None:
        if graph_plan is not None:
            from dander.pipeline.runtime import GraphExecutionPlan

            if not isinstance(graph_plan, GraphExecutionPlan):
                raise TypeError("Redshift graph plan has the wrong type")
            return RedshiftGraphRunner(
                plan=graph_plan,
                database=self.database,
                connection_factory=self.connection_factory,
                target_fence=self.target_fence,
                statement_timeout_ms=self.statement_timeout_ms,
            )
        if not build_models:
            return None
        return RedshiftTransformRunner(
            database=self.database,
            connection_factory=self.connection_factory,
            target_fence=self.target_fence,
            statement_timeout_ms=self.statement_timeout_ms,
            raw_namespace=raw_namespace,
        )


@dataclass(frozen=True, slots=True)
class RedshiftTelemetry:
    """Normalize DB-API counters without retaining SQL or provider payloads."""

    def operation(
        self,
        job: object,
        *,
        operation: TelemetryOperation,
        duration_ms: int = 0,
        retry_count: int = 0,
    ) -> OperationTelemetry:
        rowcount = getattr(job, "rowcount", 0)
        query_id = getattr(job, "query_id", None)
        affected = rowcount if isinstance(rowcount, int) and rowcount >= 0 else 0
        return OperationTelemetry(
            provider="redshift",
            operation=operation,
            duration_ms=duration_ms,
            retry_count=retry_count,
            rows_affected=affected,
            query_id=query_id if isinstance(query_id, str) else None,
        )


REDSHIFT_CAPABILITIES = WarehouseCapabilities(
    provider_id="redshift",
    schema_contract_version=1,
    write_modes=frozenset(WriteMode),
    transports=frozenset({WriteTransport.COPY, WriteTransport.DIRECT}),
    supports_transforms=True,
    supports_graphs=True,
    supports_target_fencing=True,
    schema_support=REDSHIFT_SCHEMA_SUPPORT,
)


def build_redshift_warehouse(
    config: BaseModel,
    context: Mapping[str, object],
) -> WarehouseRuntime:
    """Build an IAM-authenticated Redshift runtime or injected conformance session."""
    if not isinstance(config, RedshiftWarehouseConfig):
        raise TypeError("Redshift warehouse factory received the wrong configuration")
    catalog = context.get("catalog")
    if catalog != config.database:
        raise ValueError("Redshift warehouse factory requires its configured database catalog")
    supplied_factory = context.get("connection_factory")
    if supplied_factory is None:
        connection_factory = _sdk_connection_factory(config)
    elif callable(supplied_factory):
        connection_factory = cast("RedshiftConnectionFactory", supplied_factory)
    else:
        raise TypeError("Redshift context connection_factory must be callable")

    supplied_s3 = context.get("s3_client")
    if supplied_s3 is None:
        s3_client = _sdk_s3_client(config.region)
    else:
        s3_client = cast("RedshiftS3Client", supplied_s3)

    try:
        with open_connection(connection_factory) as connection:
            current = execute(
                connection,
                "SELECT current_database(), current_user",
                fetch="one",
            ).row
    except Exception as error:
        raise ProviderFactoryError("Redshift connection validation failed") from error
    if not isinstance(current, (tuple, list)) or len(current) < 2:
        raise ProviderFactoryError("Redshift connection validation returned an invalid result")
    if current[0] != config.database:
        raise ProviderFactoryError("Redshift connection selected the wrong database")
    if not isinstance(current[1], str) or not current[1]:
        raise ProviderFactoryError("Redshift connection returned an invalid database user")
    if config.db_user is not None and not _matches_iam_user(current[1], config.db_user):
        raise ProviderFactoryError("Redshift connection selected the wrong database user")

    target_fence = RedshiftTargetFence(
        connection_factory=connection_factory,
        database=config.database,
    )
    staging = default_staging_settings(
        bucket=config.staging_bucket,
        prefix=config.staging_prefix,
        region=config.region,
        copy_role_arn=config.copy_role_arn,
        max_rows_per_file=config.max_rows_per_file,
        max_logical_bytes_per_file=config.max_logical_bytes_per_file,
        compression=config.compression,
        statement_timeout_ms=config.statement_timeout_ms,
        direct_max_rows=config.direct_max_rows,
        direct_max_logical_bytes=config.direct_max_logical_bytes,
    )
    supplied_root = context.get("staging_root")
    if supplied_root is not None:
        if not isinstance(supplied_root, Path):
            raise TypeError("Redshift context staging_root must be a pathlib.Path")
        staging = RedshiftStagingSettings(
            root=supplied_root,
            bucket=staging.bucket,
            prefix=staging.prefix,
            region=staging.region,
            copy_role_arn=staging.copy_role_arn,
            max_rows_per_file=staging.max_rows_per_file,
            max_logical_bytes_per_file=staging.max_logical_bytes_per_file,
            compression=staging.compression,
            statement_timeout_ms=staging.statement_timeout_ms,
            direct_max_rows=staging.direct_max_rows,
            direct_max_logical_bytes=staging.direct_max_logical_bytes,
        )
    schema_mapper = RedshiftSchemaMapper()
    return WarehouseRuntime(
        provider_id="redshift",
        relation_codec=RedshiftRelationCodec(config.database),
        schema_mapper=schema_mapper,
        writers=RedshiftWriterFactory(
            config.database,
            connection_factory,
            s3_client,
            target_fence,
            staging,
        ),
        transforms=RedshiftTransformFactory(
            config.database,
            connection_factory,
            target_fence,
            config.statement_timeout_ms,
        ),
        target_fence=target_fence,
        telemetry=RedshiftTelemetry(),
        capabilities=REDSHIFT_CAPABILITIES,
        ingestion_schema_mapper=schema_mapper,
    )


def _sdk_connection_factory(config: RedshiftWarehouseConfig) -> RedshiftConnectionFactory:
    def connect() -> object:
        try:
            redshift_connector = importlib.import_module("redshift_connector")
        except ModuleNotFoundError as error:
            raise ProviderFactoryError(
                "Redshift warehouse requires the dander-platform[redshift] extra"
            ) from error
        connector = cast("Callable[..., object]", redshift_connector.connect)
        common: dict[str, object] = {
            "iam": True,
            "ssl": True,
            "sslmode": "verify-full",
            "host": config.host,
            "port": config.port,
            "database": config.database,
            "region": config.region,
            "timeout": config.connect_timeout_seconds,
            "application_name": "dander",
        }
        if config.deployment == "provisioned":
            assert config.db_user is not None
            return connector(
                **common,
                cluster_identifier=config.cluster_identifier,
                db_user=config.db_user,
            )
        return connector(
            **common,
            is_serverless=True,
            serverless_work_group=config.workgroup_name,
        )

    return cast("RedshiftConnectionFactory", connect)


def _sdk_s3_client(region: str) -> RedshiftS3Client:
    try:
        boto3 = importlib.import_module("boto3")
    except ModuleNotFoundError as error:
        raise ProviderFactoryError(
            "Redshift warehouse requires the dander-platform[redshift] extra"
        ) from error
    return cast("RedshiftS3Client", boto3.client("s3", region_name=region))


REDSHIFT_WAREHOUSE_FACTORY = ProviderFactory[WarehouseRuntime](
    kind=ProviderKind.WAREHOUSE,
    provider_id="redshift",
    api_version=PROVIDER_API_VERSION,
    build=build_redshift_warehouse,
)


def _quote(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _matches_iam_user(actual: str, configured: str) -> bool:
    return actual in {configured, f"IAM:{configured}"} or actual.startswith(f"IAMA:{configured}:")


__all__ = [
    "REDSHIFT_CAPABILITIES",
    "REDSHIFT_SCHEMA_SUPPORT",
    "REDSHIFT_WAREHOUSE_FACTORY",
    "RedshiftRelationCodec",
    "RedshiftSchemaMapper",
]
