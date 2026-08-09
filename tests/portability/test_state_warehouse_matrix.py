"""Cross-backend publication proof for the executable BigQuery/PostgreSQL pair."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any, cast

import pytest
from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.concurrency import FencingToken, TargetFence, TargetFenceLostError
from dander.providers import ProviderKind, default_provider_registry
from dander.warehouse import RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterator

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


@pytest.fixture
def postgresql_matrix_warehouse() -> Iterator[tuple[WarehouseRuntime, PostgreSQLPool, str, str]]:
    dsn = os.environ.get("DANDER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("DANDER_TEST_POSTGRES_DSN is not configured")
    pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=3,
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
    schema = f"dander_matrix_{uuid.uuid4().hex}"
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.WAREHOUSE,
        {
            "provider": "postgresql",
            "database": database,
            "dsn_env": "DANDER_TEST_POSTGRES_DSN",
        },
    )
    runtime = registry.build(ProviderKind.WAREHOUSE, config, context={"pool": pool})
    assert isinstance(runtime, WarehouseRuntime)
    try:
        yield runtime, pool, database, schema
    finally:
        with pool.connection() as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )
        pool.close()


def test_bigquery_state_token_fences_postgresql_publication(
    postgresql_matrix_warehouse: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    warehouse, pool, database, schema = postgresql_matrix_warehouse
    relation = RelationRef(catalog=database, namespace=schema, name="records")
    legacy_bigquery_lease_table = "unit-project.dander_meta._dander_lease_0123456789abcdef"
    first_token = FencingToken(
        lease_table=legacy_bigquery_lease_table,
        pipeline_id="cross_backend",
        run_id="run-one",
        token=1,
    )
    first_publication = warehouse.target_fence.claim(relation, first_token)
    writer = warehouse.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.STRICT,
    )
    target = _target(database, schema, first_publication)

    assert writer.write([{"id": "one", "label": "first"}], target) == 1

    second_token = FencingToken(
        lease_table=legacy_bigquery_lease_table,
        pipeline_id="cross_backend",
        run_id="run-two",
        token=2,
    )
    second_publication = warehouse.target_fence.claim(relation, second_token)
    with pytest.raises(TargetFenceLostError, match="destination fence lost"):
        writer.write([{"id": "one", "label": "stale"}], target)
    assert (
        writer.write(
            [{"id": "one", "label": "second"}],
            _target(database, schema, second_publication),
        )
        == 1
    )

    with pool.connection() as connection:
        row = connection.execute(
            sql.SQL("SELECT label FROM {}.records WHERE id = 'one'").format(sql.Identifier(schema))
        ).fetchone()
        fence = connection.execute(
            sql.SQL(
                "SELECT authority_id, run_id, fencing_token, status "
                "FROM {}.dander_target_commits WHERE pipeline_id = 'cross_backend'"
            ).format(sql.Identifier(schema))
        ).fetchone()
    assert row == {"label": "second"}
    assert fence == {
        "authority_id": "bigquery:unit-project.dander_meta",
        "run_id": "run-two",
        "fencing_token": 2,
        "status": "committed",
    }


def _target(database: str, schema: str, fence: TargetFence) -> WriteTarget:
    return WriteTarget(
        project=database,
        dataset=schema,
        table="records",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=fence,
    )
