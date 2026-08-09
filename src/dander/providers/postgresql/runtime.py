"""PostgreSQL implementation of Dander's warehouse capability bundle."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from psycopg import Connection
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.providers.postgresql.config import PostgreSQLWarehouseConfig
from dander.providers.postgresql.fence import PostgreSQLTargetFence
from dander.providers.postgresql.transform import PostgreSQLTransformRunner
from dander.providers.postgresql.writer import PostgreSQLScd1Writer, PostgreSQLTimeouts
from dander.providers.registry import PROVIDER_API_VERSION, ProviderFactory, ProviderKind
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.warehouse import CanonicalField, RelationRef, RelationSchema
from dander.warehouse.runtime import WarehouseCapabilities, WarehouseRuntime
from dander.writer import (
    SchemaEvolution,
    WriteField,
    WriteMode,
    WritePattern,
    WriteTransport,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import BaseModel

PostgreSQLRow = dict[str, object]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


@dataclass(frozen=True, slots=True)
class PostgreSQLRelationCodec:
    """Render one database-local PostgreSQL schema/relation coordinate."""

    database: str
    provider_id: str = "postgresql"

    def render(self, relation: RelationRef) -> str:
        if relation.catalog != self.database:
            raise ValueError("PostgreSQL relation belongs to another database")
        return f"{_quote(relation.namespace)}.{_quote(relation.name)}"


@dataclass(frozen=True, slots=True)
class PostgreSQLSchemaMapper:
    """Map compatibility `WriteField` declarations into canonical schema v1."""

    def canonical_schema(self, fields: Sequence[object]) -> RelationSchema:
        canonical: list[CanonicalField] = []
        for field in fields:
            if isinstance(field, WriteField):
                canonical.append(field.to_canonical())
            elif isinstance(field, CanonicalField):
                canonical.append(field)
            else:
                raise TypeError("PostgreSQL schema mapper received an unsupported field")
        return RelationSchema(fields=tuple(canonical))


@dataclass(frozen=True, slots=True)
class PostgreSQLWriterFactory:
    """Construct PostgreSQL's first bounded hosted ingestion writer."""

    database: str
    pool: PostgreSQLPool
    target_fence: PostgreSQLTargetFence
    timeouts: PostgreSQLTimeouts

    def build_ingestion_writer(
        self,
        *,
        sandbox: bool,
        batch_rows: int,
        schema_evolution: SchemaEvolution,
    ) -> WritePattern:
        del batch_rows
        if sandbox:
            raise ValueError("PostgreSQL warehouse does not use Dander's BigQuery sandbox mode")
        return PostgreSQLScd1Writer(
            database=self.database,
            pool=self.pool,
            target_fence=self.target_fence,
            schema_evolution=schema_evolution,
            timeouts=self.timeouts,
        )


@dataclass(frozen=True, slots=True)
class PostgreSQLTransformFactory:
    """Construct PostgreSQL model execution while rejecting graph plans."""

    database: str
    pool: PostgreSQLPool
    target_fence: PostgreSQLTargetFence
    timeouts: PostgreSQLTimeouts

    def build_transform_runner(
        self,
        *,
        graph_plan: object | None,
        build_models: bool,
        raw_namespace: str = "raw",
    ) -> PostgreSQLTransformRunner | None:
        if graph_plan is not None:
            raise ValueError("PostgreSQL graph execution is not available")
        if not build_models:
            return None
        return PostgreSQLTransformRunner(
            database=self.database,
            pool=self.pool,
            target_fence=self.target_fence,
            timeouts=self.timeouts,
            raw_namespace=raw_namespace,
        )


@dataclass(frozen=True, slots=True)
class PostgreSQLTelemetry:
    """Normalize stable Psycopg cursor counters without exposing statements or values."""

    def operation(
        self,
        job: object,
        *,
        operation: TelemetryOperation,
        duration_ms: int = 0,
        retry_count: int = 0,
    ) -> OperationTelemetry:
        rowcount = getattr(job, "rowcount", 0)
        affected = rowcount if isinstance(rowcount, int) and rowcount >= 0 else 0
        return OperationTelemetry(
            provider="postgresql",
            operation=operation,
            duration_ms=duration_ms,
            retry_count=retry_count,
            rows_affected=affected,
        )


POSTGRESQL_CAPABILITIES = WarehouseCapabilities(
    provider_id="postgresql",
    schema_contract_version=1,
    write_modes=frozenset({WriteMode.SCD1}),
    transports=frozenset({WriteTransport.COPY}),
    supports_transforms=True,
    supports_graphs=False,
    supports_target_fencing=True,
)


def build_postgresql_warehouse(
    config: BaseModel,
    context: Mapping[str, object],
) -> WarehouseRuntime:
    """Build a TLS-required PostgreSQL runtime or use an injected conformance pool."""
    if not isinstance(config, PostgreSQLWarehouseConfig):
        raise TypeError("PostgreSQL warehouse factory received the wrong configuration")
    supplied_pool = context.get("pool")
    if supplied_pool is None:
        dsn = os.environ.get(config.dsn_env)
        if not dsn:
            raise ValueError(
                f"PostgreSQL warehouse requires a connection string in {config.dsn_env}"
            )
        pool = cast(
            "PostgreSQLPool",
            ConnectionPool(
                conninfo=dsn,
                min_size=config.pool_min_size,
                max_size=config.pool_max_size,
                timeout=config.pool_timeout_seconds,
                kwargs={"row_factory": dict_row, "sslmode": _required_sslmode(dsn)},
                open=True,
            ),
        )
        pool.wait(timeout=config.pool_timeout_seconds)
    elif isinstance(supplied_pool, ConnectionPool):
        pool = supplied_pool
    else:
        raise TypeError("PostgreSQL warehouse context pool must be a psycopg ConnectionPool")

    with pool.connection() as connection:
        row = connection.execute("SELECT current_database() AS database").fetchone()
    if row is None or row["database"] != config.database:
        raise ValueError("PostgreSQL warehouse database does not match the selected connection")

    target_fence = PostgreSQLTargetFence(pool=pool, catalog=config.database)
    timeouts = PostgreSQLTimeouts(
        statement_ms=config.statement_timeout_ms,
        lock_ms=config.lock_timeout_ms,
        idle_transaction_ms=config.idle_transaction_timeout_ms,
    )
    return WarehouseRuntime(
        provider_id="postgresql",
        relation_codec=PostgreSQLRelationCodec(config.database),
        schema_mapper=PostgreSQLSchemaMapper(),
        writers=PostgreSQLWriterFactory(config.database, pool, target_fence, timeouts),
        transforms=PostgreSQLTransformFactory(config.database, pool, target_fence, timeouts),
        target_fence=target_fence,
        telemetry=PostgreSQLTelemetry(),
        capabilities=POSTGRESQL_CAPABILITIES,
    )


POSTGRESQL_WAREHOUSE_FACTORY = ProviderFactory[WarehouseRuntime](
    kind=ProviderKind.WAREHOUSE,
    provider_id="postgresql",
    api_version=PROVIDER_API_VERSION,
    build=build_postgresql_warehouse,
)


def _quote(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _required_sslmode(dsn: str) -> str:
    """Require encrypted transport without weakening stricter caller verification."""
    configured = conninfo_to_dict(dsn).get("sslmode")
    if configured in {"require", "verify-ca", "verify-full"}:
        return configured
    return "require"


__all__ = [
    "POSTGRESQL_CAPABILITIES",
    "POSTGRESQL_WAREHOUSE_FACTORY",
    "PostgreSQLRelationCodec",
    "PostgreSQLSchemaMapper",
]
