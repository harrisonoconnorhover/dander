"""PostgreSQL warehouse registration and live SCD1 conformance."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

import pytest
from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.concurrency import FencingToken
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.postgresql.config import PostgreSQLWarehouseConfig
from dander.providers.postgresql.writer import PostgreSQLWriteError
from dander.telemetry import TelemetryOperation
from dander.warehouse import LogicalTypeKind, RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterator

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


def test_postgresql_warehouse_registration_is_lazy_and_explicit() -> None:
    module_name = "dander.providers.postgresql.runtime"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()

    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {
            "provider": "postgresql",
            "database": "dander_test",
            "dsn_env": "DANDER_TEST_POSTGRES_DSN",
        },
    )

    assert module_name not in sys.modules
    assert config.model_dump(mode="json") == {
        "provider": "postgresql",
        "database": "dander_test",
        "schema": "raw",
        "dsn_env": "DANDER_TEST_POSTGRES_DSN",
        "pool_min_size": 1,
        "pool_max_size": 5,
        "pool_timeout_seconds": 10.0,
        "statement_timeout_ms": 300_000,
        "lock_timeout_ms": 30_000,
        "idle_transaction_timeout_ms": 60_000,
    }


def test_postgresql_config_uses_database_and_schema_coordinates_directly() -> None:
    config = PostgreSQLWarehouseConfig(
        provider="postgresql",
        database="warehouse_db",
        schema_name="landing",
    )

    relation = config.raw_relation(
        "accounts",
        compatibility_catalog="ignored-gcp-project",
        compatibility_namespace=None,
    )

    assert relation == RelationRef(
        catalog="warehouse_db",
        namespace="landing",
        name="accounts",
    )


def test_postgresql_sslmode_requires_encrypted_connections() -> None:
    from dander.providers.postgresql.runtime import _required_sslmode

    assert _required_sslmode("postgresql://example.invalid/db") == "require"
    assert _required_sslmode("postgresql://example.invalid/db?sslmode=disable") == "require"
    assert _required_sslmode("postgresql://example.invalid/db?sslmode=verify-full") == "verify-full"


@pytest.fixture
def postgresql_warehouse() -> Iterator[tuple[WarehouseRuntime, PostgreSQLPool, str, str]]:
    dsn = os.environ.get("DANDER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("DANDER_TEST_POSTGRES_DSN is not configured")
    pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=5,
            timeout=2,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=5)
    with pool.connection() as connection:
        row = connection.execute("SELECT current_database() AS database").fetchone()
    assert row is not None
    database = cast("str", row["database"])
    schema_name = f"dander_wh_{uuid.uuid4().hex}"
    with pool.connection() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {
            "provider": "postgresql",
            "database": database,
            "dsn_env": "DANDER_TEST_POSTGRES_DSN",
            "statement_timeout_ms": 30_000,
            "lock_timeout_ms": 5_000,
            "idle_transaction_timeout_ms": 10_000,
        },
    )
    runtime = registry.build(ProviderKind.WAREHOUSE, config, context={"pool": pool})
    assert isinstance(runtime, WarehouseRuntime)
    assert runtime.ingestion_schema_mapper is runtime.schema_mapper
    try:
        yield runtime, pool, database, schema_name
    finally:
        with pool.connection() as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
            )
        pool.close()


def test_postgresql_runtime_exposes_codec_schema_capabilities_and_telemetry(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, _pool, database, _schema_name = postgresql_warehouse
    relation = RelationRef(catalog=database, namespace="raw", name="records")
    schema = runtime.schema_mapper.canonical_schema(
        [
            WriteField(name="id", data_type="INT64", mode="REQUIRED"),
            WriteField(name="payload", data_type="JSON"),
            WriteField(name="tags", data_type="STRING", mode="REPEATED"),
        ]
    )

    class _Cursor:
        rowcount = 9

    telemetry = runtime.telemetry.operation(_Cursor(), operation=TelemetryOperation.LOAD)

    assert runtime.relation_codec.render(relation) == '"raw"."records"'
    assert schema.fields[0].data_type.kind is LogicalTypeKind.INTEGER
    assert schema.fields[1].data_type.kind is LogicalTypeKind.JSON
    assert schema.fields[2].data_type.kind is LogicalTypeKind.ARRAY
    assert runtime.capabilities.write_modes == frozenset({WriteMode.SCD1})
    assert runtime.capabilities.transports == frozenset({WriteTransport.COPY})
    assert runtime.capabilities.supports_transforms is True
    assert telemetry.rows_affected == 9


def test_postgresql_scd1_streams_copy_replays_and_evolves_nullable_columns(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, pool, database, schema_name = postgresql_warehouse
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    base_schema = (
        WriteField(name="id", data_type="STRING"),
        WriteField(name="label", data_type="STRING"),
        WriteField(name="amount", data_type="NUMERIC"),
        WriteField(name="observed_at", data_type="TIMESTAMP"),
        WriteField(name="payload", data_type="JSON"),
        WriteField(name="tags", data_type="STRING", mode="REPEATED"),
    )
    lease = FencingToken(
        lease_table=None,
        pipeline_id="postgresql_records",
        run_id="run-one",
        token=1,
        authority_id="postgresql:test-state",
    )
    relation = RelationRef(catalog=database, namespace=schema_name, name="records")
    publication = runtime.target_fence.claim(relation, lease)
    target = WriteTarget(
        project=database,
        dataset=schema_name,
        table="records",
        business_key=("id",),
        schema=base_schema,
        publication_fence=publication,
    )
    unfenced = WriteTarget(
        project=database,
        dataset=schema_name,
        table="records",
        business_key=("id",),
        schema=base_schema,
    )
    with pytest.raises(PostgreSQLWriteError, match="require a destination target fence"):
        writer.write((), unfenced)

    def initial_records() -> Iterator[dict[str, object]]:
        yield {
            "id": "one",
            "label": "old",
            "amount": Decimal("10.000000000"),
            "observed_at": datetime(2026, 8, 8, 20, 0, tzinfo=UTC),
            "payload": {"active": True},
            "tags": ["first"],
        }
        yield {
            "id": "one",
            "label": "new",
            "amount": Decimal("11.000000000"),
            "observed_at": datetime(2026, 8, 8, 20, 1, tzinfo=UTC),
            "payload": {"active": True},
            "tags": ["latest"],
        }
        yield {
            "id": "two",
            "label": "second",
            "amount": Decimal("12.000000000"),
            "observed_at": datetime(2026, 8, 8, 20, 2, tzinfo=UTC),
            "payload": {"active": False},
            "tags": [],
        }

    assert writer.write(initial_records(), target) == 2
    assert (
        writer.write(
            [
                {
                    "id": "one",
                    "label": "replayed",
                    "amount": Decimal("13.000000000"),
                    "observed_at": datetime(2026, 8, 8, 20, 3, tzinfo=UTC),
                    "payload": {"active": True},
                    "tags": ["replay"],
                }
            ],
            target,
        )
        == 1
    )

    evolved = WriteTarget(
        project=database,
        dataset=schema_name,
        table="records",
        business_key=("id",),
        schema=(*base_schema, WriteField(name="note", data_type="STRING")),
        publication_fence=publication,
    )
    assert (
        writer.write(
            [
                {
                    "id": "three",
                    "label": "third",
                    "amount": Decimal("14.000000000"),
                    "observed_at": datetime(2026, 8, 8, 20, 4, tzinfo=UTC),
                    "payload": {"active": True},
                    "tags": ["additive"],
                    "note": "new column",
                }
            ],
            evolved,
        )
        == 1
    )

    drifted = WriteTarget(
        project=database,
        dataset=schema_name,
        table="records",
        business_key=("id",),
        schema=(
            base_schema[0],
            WriteField(name="label", data_type="INT64"),
            *base_schema[2:],
            WriteField(name="note", data_type="STRING"),
        ),
        publication_fence=publication,
    )
    with pytest.raises(PostgreSQLWriteError, match="type drift.*label"):
        writer.write((), drifted)

    with pool.connection() as connection:
        rows = connection.execute(
            sql.SQL("SELECT id, label, note FROM {} ORDER BY id").format(
                sql.Identifier(schema_name, "records")
            )
        ).fetchall()
        typed = connection.execute(
            sql.SQL("SELECT amount, observed_at, payload, tags FROM {} WHERE id = 'one'").format(
                sql.Identifier(schema_name, "records")
            )
        ).fetchone()
        ledger = connection.execute(
            sql.SQL(
                "SELECT status, run_id, fencing_token FROM {} "
                "WHERE target_id = %s AND pipeline_id = %s"
            ).format(sql.Identifier(schema_name, "dander_target_commits")),
            (".".join(relation.coordinates), "postgresql_records"),
        ).fetchone()
        staging = connection.execute(
            "SELECT COUNT(*) AS count FROM pg_catalog.pg_class WHERE relname LIKE 'dander_stage_%'"
        ).fetchone()
    assert rows == [
        {"id": "one", "label": "replayed", "note": None},
        {"id": "three", "label": "third", "note": "new column"},
        {"id": "two", "label": "second", "note": None},
    ]
    assert typed == {
        "amount": Decimal("13.000000000"),
        "observed_at": datetime(2026, 8, 8, 20, 3, tzinfo=UTC),
        "payload": {"active": True},
        "tags": ["replay"],
    }
    assert ledger == {"status": "committed", "run_id": "run-one", "fencing_token": 1}
    assert staging == {"count": 0}
