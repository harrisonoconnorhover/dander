"""PostgreSQL durable-state provider configuration and live conformance."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, cast

import pytest
from psycopg import Connection, OperationalError, connect, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from dander.concurrency import FencingToken, TargetFenceLostError
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.postgresql.fence import PostgreSQLTargetFence
from dander.providers.postgresql.state import (
    PostgreSQLLeaseStore,
    PostgreSQLMetadataStore,
    PostgreSQLRunHistoryStore,
    PostgreSQLStateMigrator,
    PostgreSQLWatermarkStore,
)
from dander.state import LeaseHandle, RunStage, RunStatus, StateRuntime
from dander.warehouse import RelationRef

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dander.catalog import MetadataStore

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


@pytest.fixture
def postgresql_runtime() -> Iterator[tuple[StateRuntime, PostgreSQLPool, str]]:
    dsn = os.environ.get("DANDER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("DANDER_TEST_POSTGRES_DSN is not configured")
    schema_name = f"dander_test_{uuid.uuid4().hex}"
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
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.STATE,
        {
            "provider": "postgresql",
            "authority_id": "postgresql:test-state",
            "dsn_env": "DANDER_TEST_POSTGRES_DSN",
            "schema_name": schema_name,
            "lease_seconds": 10,
            "terminal_history_retention_days": 30,
        },
    )
    runtime = registry.build(
        ProviderKind.STATE,
        config,
        context={"pool": pool, "metadata_enabled": True},
    )
    assert isinstance(runtime, StateRuntime)
    try:
        yield runtime, pool, schema_name
    finally:
        with pool.connection() as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
            )
        pool.close()


def test_postgresql_registration_requires_only_an_environment_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = default_provider_registry()

    config = registry.parse(
        ProviderKind.STATE,
        {
            "provider": "postgresql",
            "authority_id": "postgresql:test-state",
            "dsn_env": "DATABASE_URL",
            "schema_name": "dander_control",
            "pool_min_size": 2,
            "pool_max_size": 4,
        },
    )

    assert config.model_dump(mode="json") == {
        "provider": "postgresql",
        "authority_id": "postgresql:test-state",
        "authority_epoch": 1,
        "dsn_env": "DATABASE_URL",
        "schema_name": "dander_control",
        "pool_min_size": 2,
        "pool_max_size": 4,
        "pool_timeout_seconds": 10.0,
        "lease_seconds": 120,
        "terminal_history_retention_days": 90,
    }
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="connection string in DATABASE_URL"):
        registry.build(ProviderKind.STATE, config)


def test_postgresql_state_schema_and_stores_conform(
    postgresql_runtime: tuple[StateRuntime, PostgreSQLPool, str],
) -> None:
    runtime, pool, schema_name = postgresql_runtime

    assert runtime.migrator.current_version() == 0
    assert runtime.migrator.migrate() == 1
    assert runtime.migrator.migrate() == 1
    assert runtime.migrator.current_version() == 1
    assert isinstance(runtime.leases, PostgreSQLLeaseStore)
    assert isinstance(runtime.watermarks, PostgreSQLWatermarkStore)
    assert isinstance(runtime.history, PostgreSQLRunHistoryStore)
    assert isinstance(runtime.metadata, PostgreSQLMetadataStore)
    assert runtime.capabilities.server_time is True
    assert runtime.capabilities.atomic_leases is True
    assert runtime.capabilities.monotonic_fencing is True
    assert runtime.capabilities.atomic_watermark_cas is True
    assert runtime.capabilities.interrupted_run_reconciliation is True

    first = runtime.leases.acquire("salesforce", "run-1")
    assert first is not None
    assert first.fencing_token == 1
    assert first.fence is not None
    assert first.fence.lease_table is None
    assert first.fence.resolved_authority_id == "postgresql:test-state"
    assert first.fence.authority_epoch == 1
    assert runtime.leases.acquire("salesforce", "run-2") is None
    assert runtime.leases.heartbeat(first) is True
    stale = LeaseHandle("salesforce", "run-1", 999, 10)
    assert runtime.leases.heartbeat(stale) is False
    assert runtime.leases.release(stale) is False
    assert runtime.leases.release(first) is True
    second = runtime.leases.acquire("salesforce", "run-2")
    assert second is not None
    assert second.fencing_token == 2

    assert runtime.watermarks.get("salesforce", "accounts") is None
    assert runtime.watermarks.compare_and_set(
        "salesforce", "accounts", expected=None, cursor="2026-08-08T12:00:00Z"
    )
    assert not runtime.watermarks.compare_and_set(
        "salesforce", "accounts", expected=None, cursor="stale"
    )
    assert runtime.watermarks.compare_and_set(
        "salesforce",
        "accounts",
        expected="2026-08-08T12:00:00Z",
        cursor="2026-08-08T13:00:00Z",
    )
    assert not runtime.watermarks.compare_and_set(
        "salesforce", "accounts", expected="old", cursor="regressed"
    )
    assert runtime.watermarks.get("salesforce", "accounts") == "2026-08-08T13:00:00Z"

    runtime.history.start("abandoned", "salesforce", pipeline_id="salesforce")
    runtime.history.start("current", "salesforce", pipeline_id="salesforce")
    runtime.history.reconcile_interrupted("salesforce", current_run_id="current")
    runtime.history.checkpoint(
        "current",
        RunStage.TRANSFORM,
        endpoints=4,
        extracted=12,
        affected=10,
        models=2,
        assertions=3,
    )
    runtime.history.finish(
        "current",
        RunStatus.SUCCEEDED,
        endpoints=4,
        extracted=12,
        affected=10,
        models=5,
        assertions=8,
        assets=5,
    )
    records = {record.run_id: record for record in runtime.history.recent(limit=10)}
    assert records["abandoned"].status is RunStatus.FAILED
    assert records["abandoned"].failure_code == "interrupted_run"
    assert records["current"].status is RunStatus.SUCCEEDED
    assert records["current"].stage is RunStage.COMPLETE
    assert records["current"].models == 5

    metadata = cast("MetadataStore", runtime.metadata)
    metadata.publish(
        pipeline_id="salesforce",
        run_id="current",
        manifest={"z": 2, "a": {"columns": ["id"]}},
    )
    metadata.publish(
        pipeline_id="salesforce",
        run_id="replacement",
        manifest={"a": {"columns": ["id", "name"]}},
    )
    snapshots = metadata.snapshots(pipeline_id="salesforce")
    assert len(snapshots) == 1
    assert snapshots[0].run_id == "replacement"
    assert snapshots[0].manifest == {"a": {"columns": ["id", "name"]}}

    with pool.connection() as connection:
        lease_times = connection.execute(
            sql.SQL(
                "SELECT heartbeat_at, lease_expires_at, clock_timestamp() AS server_now "
                "FROM {} WHERE pipeline_id = 'salesforce'"
            ).format(sql.Identifier(schema_name, "dander_pipeline_leases"))
        ).fetchone()
    assert lease_times is not None
    assert lease_times["heartbeat_at"].tzinfo is not None
    assert lease_times["lease_expires_at"] > lease_times["server_now"]


def test_postgresql_lease_contention_has_one_owner(
    postgresql_runtime: tuple[StateRuntime, PostgreSQLPool, str],
) -> None:
    runtime, _pool, _schema_name = postgresql_runtime
    runtime.migrator.migrate()

    with ThreadPoolExecutor(max_workers=5) as executor:
        handles = list(
            executor.map(
                lambda run_id: runtime.leases.acquire("contended", run_id),
                (f"run-{index}" for index in range(10)),
            )
        )

    owners = [handle for handle in handles if handle is not None]
    assert len(owners) == 1
    assert owners[0].fencing_token == 1


def test_postgresql_destination_fence_rejects_stale_publication(
    postgresql_runtime: tuple[StateRuntime, PostgreSQLPool, str],
) -> None:
    runtime, pool, schema_name = postgresql_runtime
    runtime.migrator.migrate()
    with pool.connection() as connection:
        database_row = connection.execute("SELECT current_database() AS name").fetchone()
        assert database_row is not None
        catalog = cast("str", database_row["name"])
        connection.execute(
            sql.SQL("CREATE TABLE {} (id TEXT PRIMARY KEY, label TEXT NOT NULL)").format(
                sql.Identifier(schema_name, "destination_records")
            )
        )
        connection.execute(
            sql.SQL("INSERT INTO {} VALUES ('one', 'original')").format(
                sql.Identifier(schema_name, "destination_records")
            )
        )
    capability = PostgreSQLTargetFence(pool=pool, catalog=catalog)
    relation = RelationRef(
        catalog=catalog,
        namespace=schema_name,
        name="destination_records",
    )
    first = FencingToken(
        lease_table=None,
        pipeline_id="fenced-pipeline",
        run_id="run-one",
        token=1,
        authority_id="postgresql:test-state",
    )
    first_claim = capability.claim(relation, first)
    assert capability.claim(relation, first) == first_claim

    stale_retry = FencingToken(
        lease_table=None,
        pipeline_id="fenced-pipeline",
        run_id="different-run",
        token=1,
        authority_id="postgresql:test-state",
    )
    with pytest.raises(TargetFenceLostError, match="rejected stale"):
        capability.claim(relation, stale_retry)

    second = FencingToken(
        lease_table=None,
        pipeline_id="fenced-pipeline",
        run_id="run-two",
        token=2,
        authority_id="postgresql:test-state",
    )
    second_claim = capability.claim(relation, second)
    update = (
        sql.SQL("UPDATE {} SET label = %s WHERE id = %s")
        .format(sql.Identifier(schema_name, "destination_records"))
        .as_string()
    )
    with pool.connection() as connection:
        with pytest.raises(TargetFenceLostError, match="lost before publication"):
            capability.execute_dml(
                connection,
                update,
                first_claim,
                ("stale", "one"),
            )
        capability.execute_dml(
            connection,
            update,
            second_claim,
            ("published", "one"),
        )
        row = connection.execute(
            sql.SQL("SELECT label FROM {} WHERE id = 'one'").format(
                sql.Identifier(schema_name, "destination_records")
            )
        ).fetchone()
        commit = connection.execute(
            sql.SQL(
                "SELECT status, run_id, fencing_token FROM {} "
                "WHERE target_id = %s AND pipeline_id = %s"
            ).format(sql.Identifier(schema_name, "dander_target_commits")),
            (".".join(relation.coordinates), "fenced-pipeline"),
        ).fetchone()
    assert row == {"label": "published"}
    assert commit == {"status": "committed", "run_id": "run-two", "fencing_token": 2}


def test_postgresql_failed_migration_records_no_version(
    postgresql_runtime: tuple[StateRuntime, PostgreSQLPool, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _pool, _schema_name = postgresql_runtime
    migrator = cast("PostgreSQLStateMigrator", runtime.migrator)
    apply_initial_schema = migrator._apply_initial_schema

    def fail_after_ddl(connection: Connection[PostgreSQLRow]) -> None:
        apply_initial_schema(connection)
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(migrator, "_apply_initial_schema", fail_after_ddl)

    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        migrator.migrate()

    assert migrator.current_version() == 0


def test_postgresql_retention_preserves_interrupted_runs_and_rejects_newer_schema(
    postgresql_runtime: tuple[StateRuntime, PostgreSQLPool, str],
) -> None:
    runtime, pool, schema_name = postgresql_runtime
    runtime.migrator.migrate()
    runs = sql.Identifier(schema_name, "dander_runs")
    ledger = sql.Identifier(schema_name, "dander_schema_migrations")
    with pool.connection() as connection:
        connection.execute(
            sql.SQL(
                "INSERT INTO {} (run_id, pipeline_id, source_name, status, stage, started_at, "
                "finished_at, failure_code) VALUES "
                "('old-success', 'p', 's', 'succeeded', 'complete', now() - interval '60 days', "
                "now() - interval '60 days', NULL), "
                "('old-interrupted', 'p', 's', 'failed', 'ingest', now() - interval '60 days', "
                "now() - interval '60 days', 'interrupted_run')"
            ).format(runs)
        )
    runtime.migrator.migrate()
    remaining = {record.run_id for record in runtime.history.recent(limit=10)}
    assert "old-success" not in remaining
    assert "old-interrupted" in remaining

    with pool.connection() as connection:
        connection.execute(
            sql.SQL(
                "INSERT INTO {} (version, name, applied_at) VALUES (2, 'future', now())"
            ).format(ledger)
        )
    with pytest.raises(RuntimeError, match="newer than this Dander runtime"):
        runtime.migrator.current_version()


def test_postgresql_pool_exhaustion_fails_boundedly(
    postgresql_runtime: tuple[StateRuntime, PostgreSQLPool, str],
) -> None:
    _runtime, parent_pool, schema_name = postgresql_runtime
    dsn = os.environ["DANDER_TEST_POSTGRES_DSN"]
    pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=1,
            timeout=0.1,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=5)
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.STATE,
        {
            "provider": "postgresql",
            "authority_id": "postgresql:test-state",
            "dsn_env": "DANDER_TEST_POSTGRES_DSN",
            "schema_name": schema_name,
            "pool_min_size": 1,
            "pool_max_size": 1,
            "pool_timeout_seconds": 0.1,
        },
    )
    runtime = registry.build(
        ProviderKind.STATE,
        config,
        context={"pool": pool, "metadata_enabled": False},
    )
    assert isinstance(runtime, StateRuntime)
    runtime.migrator.migrate()
    try:
        with pool.connection(), pytest.raises(PoolTimeout):
            runtime.watermarks.get("source", "entity")
    finally:
        pool.close()
    with parent_pool.connection() as connection:
        assert connection.execute("SELECT 1 AS value").fetchone() == {"value": 1}


def test_postgresql_pool_replaces_a_lost_connection(
    postgresql_runtime: tuple[StateRuntime, PostgreSQLPool, str],
) -> None:
    _runtime, _parent_pool, schema_name = postgresql_runtime
    dsn = os.environ["DANDER_TEST_POSTGRES_DSN"]
    pool = cast(
        "PostgreSQLPool",
        ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=1,
            timeout=1,
            kwargs={"row_factory": dict_row},
            open=True,
        ),
    )
    pool.wait(timeout=5)
    registry = default_provider_registry()
    config = registry.parse(
        ProviderKind.STATE,
        {
            "provider": "postgresql",
            "authority_id": "postgresql:test-state",
            "dsn_env": "DANDER_TEST_POSTGRES_DSN",
            "schema_name": schema_name,
            "pool_min_size": 1,
            "pool_max_size": 1,
        },
    )
    runtime = registry.build(
        ProviderKind.STATE,
        config,
        context={"pool": pool, "metadata_enabled": False},
    )
    assert isinstance(runtime, StateRuntime)
    runtime.migrator.migrate()
    with pool.connection() as connection:
        row = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()
    assert row is not None
    with connect(dsn, autocommit=True) as killer:
        assert killer.execute("SELECT pg_terminate_backend(%s)", (row["pid"],)).fetchone() == (
            True,
        )

    try:
        with pytest.raises(OperationalError):
            runtime.watermarks.get("source", "lost-connection")
        pool.wait(timeout=5)
        runtime.watermarks.set("source", "lost-connection", "recovered")
        assert runtime.watermarks.get("source", "lost-connection") == "recovered"
    finally:
        pool.close()
