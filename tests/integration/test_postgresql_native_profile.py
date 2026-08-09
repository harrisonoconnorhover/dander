"""End-to-end PostgreSQL state, ingestion, transform, and metadata proof."""

from __future__ import annotations

import os
import uuid
from textwrap import dedent
from typing import TYPE_CHECKING, Any, cast

import pytest
from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.executor import PipelineExecutor
from dander.ingestion import Endpoint, RawField, Source, SourceConfig
from dander.providers import ProviderKind, ProviderRegistry, default_provider_registry
from dander.runtime import PipelineRunner
from dander.state import RunStatus, StateRuntime
from dander.warehouse import WarehouseRuntime
from dander.writer import SchemaEvolution

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


class _FixtureSource(Source):
    def __init__(self, source_name: str) -> None:
        super().__init__(
            SourceConfig(
                name=source_name,
                base_url="https://example.invalid",
                auth_strategy="none",
                endpoints=[
                    Endpoint(
                        name="widgets",
                        path="/widgets",
                        primary_key=["id"],
                        incremental_cursor="updated_at",
                        cursor_param="updated_after",
                        raw_schema=[
                            RawField(name="id", data_type="STRING", mode="REQUIRED"),
                            RawField(name="name", data_type="STRING"),
                            RawField(name="updated_at", data_type="TIMESTAMP"),
                        ],
                    )
                ],
            )
        )

    def discover(self) -> Mapping[str, Any]:
        return {}

    def extract(self, endpoint: str, *, since: str | None = None) -> Iterator[Mapping[str, Any]]:
        assert endpoint == "widgets"
        assert since in {None, "2026-08-08T12:01:00+00:00"}
        yield {"id": "one", "name": "First", "updated_at": "2026-08-08T12:00:00Z"}
        yield {"id": "two", "name": "Second", "updated_at": "2026-08-08T12:01:00Z"}


def test_postgresql_native_profile_runs_and_replays(
    tmp_path: Path,
) -> None:
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
    suffix = uuid.uuid4().hex[:12]
    raw_schema = "raw"
    source_name = f"fixture_{suffix}"
    raw_table = f"{source_name}_widgets"
    model_schema = f"models_{suffix}"
    state_schema = f"state_{suffix}"
    registry = default_provider_registry()
    state = _state_runtime(registry, pool, state_schema)
    warehouse = _warehouse_runtime(registry, pool, database)
    state.migrator.migrate()
    _write_model(
        tmp_path,
        source_name=source_name,
        raw_schema=raw_schema,
        model_schema=model_schema,
    )
    source = _FixtureSource(source_name)
    writer = warehouse.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    ingestion = PipelineRunner(
        source=source,
        writer=writer,
        watermarks=state.watermarks,
        project=database,
        dataset=raw_schema,
        batch_rows=1,
        target_fence=warehouse.target_fence,
    )
    transform = warehouse.transforms.build_transform_runner(
        graph_plan=None,
        build_models=True,
    )
    assert transform is not None
    executor = PipelineExecutor(
        pipeline_id="postgresql_fixture",
        source_config=source.config,
        ingestion=ingestion,
        history=state.history,
        project=database,
        models_dir=tmp_path,
        selected_models=("stg_fixture__widgets",),
        build_models=True,
        transform_runner=transform,
        metadata_store=state.metadata,
        leases=state.leases,
    )

    try:
        first = executor.execute(run_id="postgres-profile-one")
        replay = executor.execute(run_id="postgres-profile-two")

        assert first.ingestion.endpoints[0].extracted == 2
        assert replay.ingestion.endpoints[0].extracted == 2
        assert first.models == ("stg_fixture__widgets",)
        assert state.watermarks.get(source_name, "widgets") == "2026-08-08T12:01:00+00:00"
        recent = state.history.recent(limit=2, pipeline_id="postgresql_fixture")
        assert [record.status for record in recent] == [
            RunStatus.SUCCEEDED,
            RunStatus.SUCCEEDED,
        ]
        with pool.connection() as connection:
            raw_count = connection.execute(
                sql.SQL("SELECT COUNT(*) AS count FROM {}.{}").format(
                    sql.Identifier(raw_schema),
                    sql.Identifier(raw_table),
                )
            ).fetchone()
            model_count = connection.execute(
                sql.SQL("SELECT COUNT(*) AS count FROM {}.stg_fixture__widgets").format(
                    sql.Identifier(model_schema)
                )
            ).fetchone()
            commits = connection.execute(
                sql.SQL(
                    "SELECT COUNT(*) AS count FROM {}.dander_target_commits "
                    "WHERE status = 'committed' AND target_id = %s"
                ).format(sql.Identifier(raw_schema)),
                (f"{database}.{raw_schema}.{raw_table}",),
            ).fetchone()
            lease = connection.execute(
                sql.SQL(
                    "SELECT run_id FROM {}.dander_pipeline_leases "
                    "WHERE pipeline_id = 'postgresql_fixture'"
                ).format(sql.Identifier(state_schema))
            ).fetchone()
        assert raw_count == {"count": 2}
        assert model_count == {"count": 2}
        assert commits == {"count": 1}
        assert lease == {"run_id": None}
    finally:
        with pool.connection() as connection:
            connection.execute(
                sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                    sql.Identifier(raw_schema),
                    sql.Identifier(raw_table),
                )
            )
            for namespace in (model_schema, state_schema):
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(namespace))
                )
        pool.close()


def _state_runtime(
    registry: ProviderRegistry,
    pool: PostgreSQLPool,
    schema_name: str,
) -> StateRuntime:
    config = registry.parse(
        ProviderKind.STATE,
        {
            "provider": "postgresql",
            "authority_id": f"postgresql:{schema_name}",
            "dsn_env": "DANDER_TEST_POSTGRES_DSN",
            "schema_name": schema_name,
            "lease_seconds": 10,
        },
    )
    runtime = registry.build(
        ProviderKind.STATE,
        config,
        context={"pool": pool, "metadata_enabled": True},
    )
    assert isinstance(runtime, StateRuntime)
    return runtime


def _warehouse_runtime(
    registry: ProviderRegistry,
    pool: PostgreSQLPool,
    database: str,
) -> WarehouseRuntime:
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
    return runtime


def _write_model(
    root: Path,
    *,
    source_name: str,
    raw_schema: str,
    model_schema: str,
) -> None:
    (root / "stg_fixture__widgets.sql").write_text(
        f"SELECT id, name, updated_at FROM {{{{ ref('{raw_schema}_{source_name}_widgets') }}}}",
        encoding="utf-8",
    )
    (root / "stg_fixture__widgets.yml").write_text(
        dedent(
            f"""
            model: stg_fixture__widgets
            description: Portable PostgreSQL profile fixture.
            owner: data-eng
            dialect: portable
            materialization: table
            dataset: {model_schema}
            source_system: fixture
            sensitivity: public
            columns:
              - name: id
                type: STRING
                description: Stable fixture identifier.
              - name: name
                type: STRING
                description: Fixture name.
              - name: updated_at
                type: TIMESTAMP
                description: Source update time.
            tests:
              - column: id
                not_null: true
                unique: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
