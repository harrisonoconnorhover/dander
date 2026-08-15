"""PostgreSQL warehouse registration and live writer conformance."""

from __future__ import annotations

import hashlib
import os
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

import pytest
from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.concurrency import FencingToken, TargetFence, TargetFenceLostError
from dander.ingestion.source import Endpoint, RawField, Source, SourceConfig
from dander.pipeline.graph import PipelineGraph
from dander.pipeline.runtime import GraphExecutionPlan, GraphRuntimeError, plan_graph_execution
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.postgresql.config import PostgreSQLWarehouseConfig
from dander.providers.postgresql.transform import PostgreSQLGraphRunner
from dander.providers.postgresql.writer import (
    PostgreSQLCopyWriter,
    PostgreSQLTimeouts,
    PostgreSQLWriteError,
    _select_direct_batch,
)
from dander.runtime import PipelineRunner
from dander.state import WatermarkStore
from dander.telemetry import TelemetryOperation
from dander.warehouse import (
    CanonicalType,
    LogicalTypeKind,
    RelationRef,
    RelationSchema,
    WarehouseRuntime,
)
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


class _StaticSource(Source):
    def __init__(self, *, name: str, records: list[Mapping[str, object]]) -> None:
        super().__init__(
            SourceConfig(
                name=name,
                base_url="https://example.invalid",
                auth_strategy="none",
                endpoints=[
                    Endpoint(
                        name="records",
                        path="/records",
                        primary_key=["id"],
                        raw_schema=[
                            RawField(name="id", data_type="STRING", mode="REQUIRED"),
                            RawField(name="label", data_type="STRING"),
                        ],
                    )
                ],
            )
        )
        self._records = records

    def discover(self) -> Mapping[str, object]:
        return {}

    def extract(
        self,
        endpoint: str,
        *,
        since: str | None = None,
    ) -> Iterator[Mapping[str, object]]:
        assert endpoint == "records"
        assert since is None
        yield from self._records


class _NoWatermarks(WatermarkStore):
    def get(self, source: str, entity: str) -> str | None:
        del source, entity
        return None

    def set(self, source: str, entity: str, cursor: str) -> None:
        raise AssertionError((source, entity, cursor))


class _Ownership:
    def __init__(self, fence: FencingToken) -> None:
        self.fence = fence
        self.verifications = 0

    def verify(self) -> None:
        self.verifications += 1


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
        "direct_max_rows": 0,
        "direct_max_logical_bytes": 0,
    }


def test_postgresql_direct_limits_are_explicit_and_paired() -> None:
    with pytest.raises(ValueError, match="must both be zero or positive"):
        PostgreSQLWarehouseConfig(
            provider="postgresql",
            database="warehouse",
            direct_max_rows=10,
        )
    with pytest.raises(ValueError, match="must both be zero or positive"):
        PostgreSQLWarehouseConfig(
            provider="postgresql",
            database="warehouse",
            direct_max_logical_bytes=1_024,
        )

    config = PostgreSQLWarehouseConfig(
        provider="postgresql",
        database="warehouse",
        direct_max_rows=10,
        direct_max_logical_bytes=1_024,
    )

    assert config.direct_max_rows == 10
    assert config.direct_max_logical_bytes == 1_024


def test_postgresql_direct_selection_is_bounded_and_preserves_copy_fallback() -> None:
    schema = WriteTarget(
        project="warehouse",
        dataset="raw",
        table="records",
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="label", data_type="STRING"),
        ),
    ).canonical_schema
    records = [
        {"id": "one", "label": "first"},
        {"id": "two", "label": "second"},
    ]

    direct, remaining = _select_direct_batch(
        iter(records),
        schema,
        max_rows=2,
        max_logical_bytes=1_024,
    )
    assert direct is not None
    assert direct.records == tuple(records)
    assert direct.logical_bytes > 0
    assert tuple(remaining) == ()

    direct, remaining = _select_direct_batch(
        iter(records),
        schema,
        max_rows=1,
        max_logical_bytes=1_024,
    )
    assert direct is None
    assert list(remaining) == records

    direct, remaining = _select_direct_batch(
        iter(records),
        schema,
        max_rows=2,
        max_logical_bytes=1,
    )
    assert direct is None
    assert list(remaining) == records


def test_postgresql_direct_selection_finishes_before_opening_a_transaction() -> None:
    exhausted = False

    def records() -> Iterator[Mapping[str, object]]:
        nonlocal exhausted
        yield {"id": "one", "label": "first"}
        exhausted = True

    class _PoolProbe:
        def connection(self) -> object:
            assert exhausted
            raise RuntimeError("connection opened after bounded selection")

    writer = PostgreSQLCopyWriter(
        database="warehouse",
        pool=cast("PostgreSQLPool", _PoolProbe()),
        target_fence=cast("Any", object()),
        timeouts=PostgreSQLTimeouts(statement_ms=1, lock_ms=1, idle_transaction_ms=1),
        mode=WriteMode.SCD1,
        direct_max_rows=10,
        direct_max_logical_bytes=1_024,
    )
    target = WriteTarget(
        project="warehouse",
        dataset="raw",
        table="records",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=TargetFence(
            fence_table="raw.dander_target_commits",
            target_id="warehouse.raw.records",
            authority_id="postgresql:test-state",
            authority_epoch=1,
            pipeline_id="postgresql_direct",
            run_id="run-direct",
            token=1,
        ),
    )

    with pytest.raises(RuntimeError, match="connection opened after bounded selection"):
        writer.write(records(), target)

    assert exhausted


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
    assert runtime.capabilities.write_modes == frozenset(WriteMode)
    assert runtime.capabilities.transports == frozenset(
        {WriteTransport.COPY, WriteTransport.DIRECT}
    )
    assert runtime.capabilities.supports_transforms is True
    assert runtime.capabilities.supports_graphs is True
    assert telemetry.rows_affected == 9


@pytest.mark.parametrize(
    ("mode", "batched", "streaming"),
    [
        (WriteMode.SCD1, True, False),
        (WriteMode.INCREMENTAL, True, False),
        (WriteMode.SNAPSHOT, True, False),
        (WriteMode.SCD2, False, True),
        (WriteMode.REPLACE, False, True),
    ],
)
def test_postgresql_writer_modes_expose_safe_executor_boundaries(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
    mode: WriteMode,
    batched: bool,
    streaming: bool,
) -> None:
    runtime, _pool, _database, _schema_name = postgresql_warehouse
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.STRICT,
        mode=mode,
        cursor_field="updated_at" if mode is WriteMode.INCREMENTAL else None,
        snapshot_field="snapshot_at" if mode is WriteMode.SNAPSHOT else None,
    )

    assert writer.supports_batched_writes is batched
    assert writer.accepts_streaming_input is streaming


@pytest.mark.parametrize("mode", [WriteMode.REPLACE, WriteMode.SCD2])
def test_postgresql_whole_endpoint_modes_cross_runner_batch_boundary(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
    mode: WriteMode,
) -> None:
    runtime, pool, database, schema_name = postgresql_warehouse
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.STRICT,
        mode=mode,
    )
    relation = RelationRef(
        catalog=database,
        namespace=schema_name,
        name=f"whole_{mode.value}",
    )
    fence = FencingToken(
        lease_table=None,
        pipeline_id=f"postgresql_{mode.value}",
        run_id="run-whole",
        token=1,
        authority_id="postgresql:test-state",
    )
    source = _StaticSource(
        name=f"postgresql_{mode.value}",
        records=[
            {"id": "one", "label": "old"},
            {"id": "one", "label": "new"},
            {"id": "two", "label": "second"},
        ],
    )
    runner = PipelineRunner(
        source=source,
        writer=writer,
        watermarks=_NoWatermarks(),
        endpoint_relations={"records": relation},
        batch_rows=1,
        target_fence=runtime.target_fence,
        schema_mapper=runtime.ingestion_schema_mapper,
    )

    result = runner.run(run_id=fence.run_id, ownership=_Ownership(fence))

    assert result.endpoints[0].extracted == 3
    assert result.endpoints[0].affected == (3 if mode is WriteMode.REPLACE else 2)
    with pool.connection() as connection:
        if mode is WriteMode.SCD2:
            rows = connection.execute(
                sql.SQL("SELECT id, label, is_current FROM {} ORDER BY id, label").format(
                    sql.Identifier(schema_name, relation.name)
                )
            ).fetchall()
            assert rows == [
                {"id": "one", "label": "new", "is_current": True},
                {"id": "two", "label": "second", "is_current": True},
            ]
        else:
            rows = connection.execute(
                sql.SQL("SELECT id, label FROM {} ORDER BY id, label").format(
                    sql.Identifier(schema_name, relation.name)
                )
            ).fetchall()
            assert rows == [
                {"id": "one", "label": "new"},
                {"id": "one", "label": "old"},
                {"id": "two", "label": "second"},
            ]

    if mode is WriteMode.REPLACE:
        empty_fence = FencingToken(
            lease_table=None,
            pipeline_id="postgresql_replace",
            run_id="run-empty",
            token=2,
            authority_id="postgresql:test-state",
        )
        empty_runner = PipelineRunner(
            source=_StaticSource(name="postgresql_replace", records=[]),
            writer=writer,
            watermarks=_NoWatermarks(),
            endpoint_relations={"records": relation},
            batch_rows=1,
            target_fence=runtime.target_fence,
            schema_mapper=runtime.ingestion_schema_mapper,
        )

        empty_result = empty_runner.run(
            run_id=empty_fence.run_id,
            ownership=_Ownership(empty_fence),
        )

        assert empty_result.endpoints[0].affected == 0
        with pool.connection() as connection:
            remaining = connection.execute(
                sql.SQL("SELECT COUNT(*) AS count FROM {}").format(
                    sql.Identifier(schema_name, relation.name)
                )
            ).fetchone()
        assert remaining == {"count": 0}


def test_postgresql_scd1_reuses_pre_upgrade_unique_index(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, pool, database, schema_name = postgresql_warehouse
    relation = RelationRef(catalog=database, namespace=schema_name, name="legacy_scd1")
    legacy_digest = hashlib.sha256(f"{schema_name}.legacy_scd1:id".encode()).hexdigest()[:10]
    legacy_index = f"dander_uq_legacy_scd1_{legacy_digest}"
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("CREATE TABLE {} (id text NOT NULL PRIMARY KEY, label text)").format(
                sql.Identifier(schema_name, relation.name)
            )
        )
        connection.execute(
            sql.SQL("CREATE UNIQUE INDEX {} ON {} (id)").format(
                sql.Identifier(legacy_index),
                sql.Identifier(schema_name, relation.name),
            )
        )
    fence = FencingToken(
        lease_table=None,
        pipeline_id="postgresql_legacy_scd1",
        run_id="run-legacy-index",
        token=1,
        authority_id="postgresql:test-state",
    )
    publication = runtime.target_fence.claim(relation, fence)
    target = WriteTarget(
        project=database,
        dataset=schema_name,
        table=relation.name,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=publication,
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.STRICT,
    )

    writer.write([{"id": "one", "label": "value"}], target)

    with pool.connection() as connection:
        indexes = connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND tablename = %s "
            "AND indexname LIKE 'dander_uq_%%' ORDER BY indexname",
            (schema_name, relation.name),
        ).fetchall()
    assert indexes == [{"indexname": legacy_index}]


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


def test_postgresql_selects_bounded_direct_then_copy_with_explicit_telemetry(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    from dander.providers.postgresql.runtime import PostgreSQLWriterFactory

    runtime, pool, database, schema_name = postgresql_warehouse
    assert isinstance(runtime.writers, PostgreSQLWriterFactory)
    direct_factory = replace(
        runtime.writers,
        direct_max_rows=1,
        direct_max_logical_bytes=1_024,
    )
    writer = direct_factory.build_ingestion_writer(
        sandbox=False,
        batch_rows=100,
        schema_evolution=SchemaEvolution.STRICT,
    )
    relation = RelationRef(catalog=database, namespace=schema_name, name="direct_records")
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=runtime.target_fence.claim(
            relation,
            FencingToken(
                lease_table=None,
                pipeline_id="postgresql_direct",
                run_id="run-direct",
                token=1,
                authority_id="postgresql:test-state",
            ),
        ),
    )

    assert writer.supports_batched_writes is False
    assert writer.accepts_streaming_input is True
    assert writer.write([{"id": "one", "label": "direct"}], target) == 1
    direct_operations = writer.drain_telemetry()
    assert len(direct_operations) == 1
    assert direct_operations[0].transport is WriteTransport.DIRECT
    assert direct_operations[0].rows_written == 1
    assert direct_operations[0].bytes_written > 0

    assert (
        writer.write(
            [
                {"id": "one", "label": "copy-one"},
                {"id": "two", "label": "copy-two"},
            ],
            target,
        )
        == 2
    )
    copy_operations = writer.drain_telemetry()
    assert len(copy_operations) == 1
    assert copy_operations[0].transport is WriteTransport.COPY
    assert copy_operations[0].rows_written == 2
    assert copy_operations[0].bytes_written == 0
    assert writer.drain_telemetry() == ()

    with pool.connection() as connection:
        rows = connection.execute(
            sql.SQL("SELECT id, label FROM {} ORDER BY id").format(
                sql.Identifier(schema_name, relation.name)
            )
        ).fetchall()
    assert rows == [
        {"id": "one", "label": "copy-one"},
        {"id": "two", "label": "copy-two"},
    ]


@pytest.mark.parametrize("mode", list(WriteMode))
def test_postgresql_direct_transport_reaches_every_fenced_write_mode(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
    mode: WriteMode,
) -> None:
    from dander.providers.postgresql.runtime import PostgreSQLWriterFactory

    runtime, pool, database, schema_name = postgresql_warehouse
    assert isinstance(runtime.writers, PostgreSQLWriterFactory)
    factory = replace(
        runtime.writers,
        direct_max_rows=10,
        direct_max_logical_bytes=1_024,
    )
    cursor_field = "observed_at" if mode is WriteMode.INCREMENTAL else None
    snapshot_field = "observed_at" if mode is WriteMode.SNAPSHOT else None
    writer = factory.build_ingestion_writer(
        sandbox=False,
        batch_rows=100,
        schema_evolution=SchemaEvolution.STRICT,
        mode=mode,
        cursor_field=cursor_field,
        snapshot_field=snapshot_field,
    )
    relation = RelationRef(
        catalog=database,
        namespace=schema_name,
        name=f"direct_{mode.value}",
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",)
        if mode in {WriteMode.SCD1, WriteMode.SCD2, WriteMode.INCREMENTAL}
        else (),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="label", data_type="STRING"),
            WriteField(name="observed_at", data_type="TIMESTAMP", mode="REQUIRED"),
        ),
        publication_fence=runtime.target_fence.claim(
            relation,
            FencingToken(
                lease_table=None,
                pipeline_id=f"postgresql_direct_{mode.value}",
                run_id=f"run-direct-{mode.value}",
                token=1,
                authority_id="postgresql:test-state",
            ),
        ),
    )
    observed_at = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)

    assert (
        writer.write(
            [{"id": "one", "label": mode.value, "observed_at": observed_at}],
            target,
        )
        == 1
    )
    operations = writer.drain_telemetry()
    assert len(operations) == 1
    assert operations[0].transport is WriteTransport.DIRECT
    assert operations[0].rows_written == 1
    with pool.connection() as connection:
        count = connection.execute(
            sql.SQL("SELECT COUNT(*) AS count FROM {}").format(
                sql.Identifier(schema_name, relation.name)
            )
        ).fetchone()
    assert count == {"count": 1}


def test_postgresql_incremental_keeps_newest_cursor_and_rejects_regression(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, pool, database, schema_name = postgresql_warehouse
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=10,
        schema_evolution=SchemaEvolution.STRICT,
        mode=WriteMode.INCREMENTAL,
        cursor_field="updated_at",
    )
    relation = RelationRef(catalog=database, namespace=schema_name, name="incremental_records")
    fence = FencingToken(
        lease_table=None,
        pipeline_id="postgresql_incremental",
        run_id="run-incremental",
        token=1,
        authority_id="postgresql:test-state",
    )
    publication = runtime.target_fence.claim(relation, fence)
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
            WriteField(name="updated_at", data_type="TIMESTAMP"),
        ),
        publication_fence=publication,
    )
    newest = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    older = datetime(2026, 8, 10, 11, 0, tzinfo=UTC)
    later = datetime(2026, 8, 10, 13, 0, tzinfo=UTC)

    assert (
        writer.write(
            [
                {"id": "one", "label": "newest", "updated_at": newest},
                {"id": "one", "label": "older-last", "updated_at": older},
            ],
            target,
        )
        == 1
    )
    assert (
        writer.write(
            [{"id": "one", "label": "must-not-regress", "updated_at": older}],
            target,
        )
        == 0
    )
    assert (
        writer.write(
            [{"id": "one", "label": "later", "updated_at": later}],
            target,
        )
        == 1
    )

    with pool.connection() as connection:
        row = connection.execute(
            sql.SQL("SELECT id, label, updated_at FROM {}").format(
                sql.Identifier(schema_name, relation.name)
            )
        ).fetchone()
    assert row == {"id": "one", "label": "later", "updated_at": later}


def test_postgresql_snapshot_is_null_safe_and_replay_safe(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, pool, database, schema_name = postgresql_warehouse
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=10,
        schema_evolution=SchemaEvolution.STRICT,
        mode=WriteMode.SNAPSHOT,
        snapshot_field="snapshot_at",
    )
    relation = RelationRef(catalog=database, namespace=schema_name, name="snapshot_records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="postgresql_snapshot",
            run_id="run-snapshot",
            token=1,
            authority_id="postgresql:test-state",
        ),
    )
    target = WriteTarget(
        relation=relation,
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
            WriteField(name="snapshot_at", data_type="TIMESTAMP"),
        ),
        publication_fence=publication,
    )
    first = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    second = datetime(2026, 8, 10, 13, 0, tzinfo=UTC)
    record = {"id": "one", "label": None, "snapshot_at": first}

    assert writer.write([record, record], target) == 1
    assert writer.write([record], target) == 0
    assert writer.write([{"id": "one", "label": None, "snapshot_at": second}], target) == 1

    with pool.connection() as connection:
        rows = connection.execute(
            sql.SQL("SELECT id, label, snapshot_at FROM {} ORDER BY snapshot_at").format(
                sql.Identifier(schema_name, relation.name)
            )
        ).fetchall()
    assert rows == [
        {"id": "one", "label": None, "snapshot_at": first},
        {"id": "one", "label": None, "snapshot_at": second},
    ]


def test_postgresql_scd2_closes_changed_rows_and_replays_without_new_versions(
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, pool, database, schema_name = postgresql_warehouse
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.STRICT,
        mode=WriteMode.SCD2,
    )
    relation = RelationRef(catalog=database, namespace=schema_name, name="scd2_records")
    first_fence = FencingToken(
        lease_table=None,
        pipeline_id="postgresql_scd2",
        run_id="run-scd2-one",
        token=1,
        authority_id="postgresql:test-state",
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=runtime.target_fence.claim(relation, first_fence),
    )
    assert writer.write([{"id": "one", "label": "first"}], target) == 1

    second_fence = FencingToken(
        lease_table=None,
        pipeline_id="postgresql_scd2",
        run_id="run-scd2-two",
        token=2,
        authority_id="postgresql:test-state",
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=target.schema,
        publication_fence=runtime.target_fence.claim(relation, second_fence),
    )
    changed = [{"id": "one", "label": "second"}]
    assert writer.write(changed, target) == 1
    assert writer.write(changed, target) == 0

    with pool.connection() as connection:
        rows = connection.execute(
            sql.SQL(
                "SELECT id, label, is_current, valid_to IS NOT NULL AS closed "
                "FROM {} ORDER BY valid_from"
            ).format(sql.Identifier(schema_name, relation.name))
        ).fetchall()
    assert rows == [
        {"id": "one", "label": "first", "is_current": False, "closed": True},
        {"id": "one", "label": "second", "is_current": True, "closed": False},
    ]


def _postgresql_graph_plan(
    database: str,
    schema_name: str,
    *,
    include_unsupported_target: bool = False,
) -> GraphExecutionPlan:
    source = SourceConfig(
        name="fixture",
        base_url="https://example.test",
        auth_strategy="none",
        endpoints=[
            Endpoint(
                name="records",
                path="/records",
                primary_key=["id"],
                raw_schema=[
                    RawField(name="id", data_type="STRING"),
                    RawField(name="label", data_type="STRING"),
                ],
            )
        ],
    )
    targets: list[dict[str, object]] = [
        {
            "id": "target",
            "type": "target",
            "name": "Target",
            "config": {
                "writer": {
                    "write_mode": "replace",
                    "destination": {
                        "dataset": schema_name,
                        "table": "graph_records",
                        "business_key": [],
                    },
                }
            },
            "fields": [
                {"name": "id", "type": "STRING"},
                {"name": "label", "type": "STRING"},
            ],
        }
    ]
    edges: list[dict[str, object]] = [
        {
            "from": "records",
            "to": "target",
            "mappings": [
                {"source": "id", "target": "id"},
                {"source": "label", "target": "label"},
            ],
        }
    ]
    if include_unsupported_target:
        targets.append(
            {
                "id": "unsupported",
                "type": "target",
                "name": "Unsupported",
                "config": {
                    "writer": {
                        "write_mode": "replace",
                        "destination": {
                            "dataset": schema_name,
                            "table": "unsupported_records",
                            "business_key": [],
                        },
                    }
                },
                "fields": [
                    {"name": "id", "type": "STRING", "cast_to": "STRING"},
                    {"name": "label", "type": "STRING"},
                ],
            }
        )
        edges.append(
            {
                "from": "records",
                "to": "unsupported",
                "mappings": [
                    {"source": "id", "target": "id"},
                    {"source": "label", "target": "label"},
                ],
            }
        )
    graph = PipelineGraph.model_validate(
        {
            "name": "postgresql_graph",
            "nodes": [
                {
                    "id": "records",
                    "type": "source",
                    "name": "Records",
                    "config": {"connector": "fixture", "endpoint": "records"},
                    "fields": [
                        {"name": "id", "type": "STRING"},
                        {"name": "label", "type": "STRING"},
                    ],
                },
                *targets,
            ],
            "edges": edges,
        }
    )
    return plan_graph_execution(
        graph,
        source,
        endpoint_relations={
            "records": RelationRef(
                catalog=database,
                namespace=schema_name,
                name="fixture_records",
            )
        },
    )


def _graph_fence(run_id: str, token: int) -> FencingToken:
    return FencingToken(
        lease_table=None,
        pipeline_id="postgresql_graph",
        run_id=run_id,
        token=token,
        authority_id="postgresql:test-state",
    )


def _create_graph_source(pool: PostgreSQLPool, schema_name: str) -> None:
    with pool.connection() as connection:
        connection.execute(
            sql.SQL("CREATE TABLE {} (id TEXT, label TEXT)").format(
                sql.Identifier(schema_name, "fixture_records")
            )
        )
        connection.execute(
            sql.SQL("INSERT INTO {} (id, label) VALUES (%s, %s), (%s, %s)").format(
                sql.Identifier(schema_name, "fixture_records")
            ),
            ("one", "first", "two", "second"),
        )


def test_postgresql_graph_replaces_and_replays_canonical_plan(
    tmp_path: Path,
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, pool, database, schema_name = postgresql_warehouse
    _create_graph_source(pool, schema_name)
    runner = runtime.transforms.build_transform_runner(
        graph_plan=_postgresql_graph_plan(database, schema_name),
        build_models=True,
    )
    assert isinstance(runner, PostgreSQLGraphRunner)
    ownership = _Ownership(_graph_fence("run-graph-one", 1))

    result = runner.build(tmp_path, ownership=ownership)

    assert result.models == ("target",)
    assert result.assertions == 0
    assert ownership.verifications == 2
    with pool.connection() as connection:
        first = connection.execute(
            sql.SQL("SELECT id, label FROM {} ORDER BY id").format(
                sql.Identifier(schema_name, "graph_records")
            )
        ).fetchall()
        connection.execute(
            sql.SQL("DELETE FROM {}").format(sql.Identifier(schema_name, "fixture_records"))
        )
        connection.execute(
            sql.SQL("INSERT INTO {} (id, label) VALUES (%s, %s)").format(
                sql.Identifier(schema_name, "fixture_records")
            ),
            ("two", "replayed"),
        )
    assert first == [
        {"id": "one", "label": "first"},
        {"id": "two", "label": "second"},
    ]

    replay = _Ownership(_graph_fence("run-graph-two", 2))
    assert runner.build(tmp_path, ownership=replay).models == ("target",)
    with pool.connection() as connection:
        rows = connection.execute(
            sql.SQL("SELECT id, label FROM {} ORDER BY id").format(
                sql.Identifier(schema_name, "graph_records")
            )
        ).fetchall()
    assert rows == [{"id": "two", "label": "replayed"}]


def test_postgresql_graph_preflights_every_target_and_source_catalog_before_mutation(
    tmp_path: Path,
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, pool, database, schema_name = postgresql_warehouse
    unsupported = runtime.transforms.build_transform_runner(
        graph_plan=_postgresql_graph_plan(
            database,
            schema_name,
            include_unsupported_target=True,
        ),
        build_models=True,
    )
    assert isinstance(unsupported, PostgreSQLGraphRunner)
    with pytest.raises(GraphRuntimeError, match="safe-cast semantics"):
        unsupported.build(tmp_path, ownership=_Ownership(_graph_fence("run-preflight", 3)))

    plan = _postgresql_graph_plan(database, schema_name)
    compiled = plan.targets[0]
    precision_schema = RelationSchema(
        fields=(
            compiled.target.canonical_schema.fields[0].model_copy(
                update={
                    "data_type": CanonicalType(
                        kind=LogicalTypeKind.TIMESTAMP,
                        with_timezone=True,
                        fractional_second_precision=7,
                    )
                }
            ),
            *compiled.target.canonical_schema.fields[1:],
        )
    )
    precision_target = WriteTarget(
        relation=compiled.target.relation_ref,
        business_key=compiled.target.business_key,
        schema=compiled.target.schema,
        declared_schema=precision_schema,
    )
    precision_plan = replace(
        plan,
        targets=(replace(compiled, target=precision_target),),
    )
    precision_runner = runtime.transforms.build_transform_runner(
        graph_plan=precision_plan,
        build_models=True,
    )
    assert isinstance(precision_runner, PostgreSQLGraphRunner)
    with pytest.raises(GraphRuntimeError, match="temporal precision up to 6"):
        precision_runner.build(
            tmp_path,
            ownership=_Ownership(_graph_fence("run-precision", 4)),
        )

    foreign = replace(
        plan,
        bindings=replace(
            plan.bindings,
            source_relations={
                "records": RelationRef(
                    catalog="other_database",
                    namespace=schema_name,
                    name="fixture_records",
                )
            },
        ),
    )
    runner = runtime.transforms.build_transform_runner(graph_plan=foreign, build_models=True)
    assert isinstance(runner, PostgreSQLGraphRunner)
    with pytest.raises(GraphRuntimeError, match="source belongs to another database"):
        runner.build(tmp_path, ownership=_Ownership(_graph_fence("run-foreign", 5)))

    with pool.connection() as connection:
        rows = connection.execute(
            "SELECT relname FROM pg_catalog.pg_class AS relation "
            "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = %s AND relation.relname IN "
            "('graph_records', 'unsupported_records', 'dander_target_commits')",
            (schema_name,),
        ).fetchall()
    assert rows == []


def test_postgresql_graph_selection_ownership_and_factory_fail_closed(
    tmp_path: Path,
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, _pool, database, schema_name = postgresql_warehouse
    with pytest.raises(TypeError, match="graph plan has the wrong type"):
        runtime.transforms.build_transform_runner(graph_plan=object(), build_models=False)
    assert runtime.transforms.build_transform_runner(graph_plan=None, build_models=False) is None
    runner = runtime.transforms.build_transform_runner(
        graph_plan=_postgresql_graph_plan(database, schema_name),
        build_models=False,
    )
    assert isinstance(runner, PostgreSQLGraphRunner)
    ownership = _Ownership(_graph_fence("run-selection", 5))
    with pytest.raises(GraphRuntimeError, match="Unknown graph target"):
        runner.build(tmp_path, selected=["missing"], ownership=ownership)
    with pytest.raises(GraphRuntimeError, match="selected no targets"):
        runner.build(tmp_path, selected=[], ownership=ownership)
    with pytest.raises(GraphRuntimeError, match="requires active lease ownership"):
        runner.build(tmp_path)


def test_postgresql_graph_stale_fence_rolls_back_target_publication(
    tmp_path: Path,
    postgresql_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, pool, database, schema_name = postgresql_warehouse
    _create_graph_source(pool, schema_name)
    plan = _postgresql_graph_plan(database, schema_name)
    runner = runtime.transforms.build_transform_runner(graph_plan=plan, build_models=True)
    assert isinstance(runner, PostgreSQLGraphRunner)

    class _SupersededOwnership:
        def __init__(self) -> None:
            self.fence = _graph_fence("run-stale", 6)
            self.verifications = 0

        def verify(self) -> None:
            self.verifications += 1
            if self.verifications == 2:
                runtime.target_fence.claim(
                    plan.targets[0].target.relation_ref,
                    _graph_fence("run-newer", 7),
                )

    with pytest.raises(TargetFenceLostError, match="lost before publication"):
        runner.build(tmp_path, ownership=_SupersededOwnership())

    with pool.connection() as connection:
        target = connection.execute(
            "SELECT to_regclass(%s) AS relation",
            (f"{schema_name}.graph_records",),
        ).fetchone()
        temporary = connection.execute(
            "SELECT count(*) AS count FROM pg_catalog.pg_class WHERE relname LIKE 'dander_graph_%'",
        ).fetchone()
    assert target == {"relation": None}
    assert temporary == {"count": 0}
