"""Live PostgreSQL transform, assertion, and publication-fence conformance."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from textwrap import dedent, indent
from typing import TYPE_CHECKING, Any, cast

import pytest
from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dander.concurrency import FencingToken, TargetFenceLostError
from dander.providers import ProviderKind, default_provider_registry
from dander.providers.postgresql.transform import PostgreSQLTransformRunner
from dander.transform import TransformRunError
from dander.warehouse import WarehouseRuntime

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

PostgreSQLRow = dict[str, Any]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


@dataclass
class _Ownership:
    fence: FencingToken
    verifications: int = 0

    def verify(self) -> None:
        self.verifications += 1


@pytest.fixture
def postgresql_transform_runtime() -> Iterator[tuple[WarehouseRuntime, PostgreSQLPool, str, str]]:
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
    namespace = f"dander_transform_{suffix}"
    with pool.connection() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace)))
        connection.execute("CREATE SCHEMA IF NOT EXISTS raw")
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
    try:
        yield runtime, pool, database, namespace
    finally:
        with pool.connection() as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(namespace))
            )
            connection.execute(
                sql.SQL("DROP TABLE IF EXISTS raw.{}").format(sql.Identifier(f"fixture_{suffix}"))
            )
            connection.execute(
                sql.SQL("DROP TABLE IF EXISTS raw.{}").format(sql.Identifier(f"parent_{suffix}"))
            )
        pool.close()


def test_postgresql_builds_tables_views_incrementals_and_assertions(
    tmp_path: Path,
    postgresql_transform_runtime: tuple[WarehouseRuntime, PostgreSQLPool, str, str],
) -> None:
    runtime, pool, database, namespace = postgresql_transform_runtime
    suffix = namespace.removeprefix("dander_transform_")
    raw_table = f"fixture_{suffix}"
    parent_table = f"parent_{suffix}"
    base_model = f"base_{suffix}"
    view_model = f"view_{suffix}"
    incremental_model = f"incremental_{suffix}"
    with pool.connection() as connection:
        connection.execute(
            sql.SQL(
                "CREATE TABLE raw.{} (id TEXT, parent_id TEXT, status TEXT, updated_at BIGINT)"
            ).format(sql.Identifier(raw_table))
        )
        connection.execute(
            sql.SQL("CREATE TABLE raw.{} (id TEXT PRIMARY KEY)").format(
                sql.Identifier(parent_table)
            )
        )
        connection.execute(
            sql.SQL("INSERT INTO raw.{} (id) VALUES ('parent-one'), ('parent-two')").format(
                sql.Identifier(parent_table)
            )
        )
        connection.execute(
            sql.SQL(
                "INSERT INTO raw.{} (id, parent_id, status, updated_at) VALUES "
                "('one', 'parent-one', 'open', 1), "
                "('two', 'parent-two', 'closed', 2)"
            ).format(sql.Identifier(raw_table))
        )

    _write_models(
        tmp_path,
        namespace=namespace,
        raw_table=raw_table,
        parent_table=parent_table,
        base_model=base_model,
        view_model=view_model,
        incremental_model=incremental_model,
    )
    runner = runtime.transforms.build_transform_runner(graph_plan=None, build_models=True)
    assert isinstance(runner, PostgreSQLTransformRunner)
    ownership = _ownership(database, run_id="run-one", token=1)

    with pytest.raises(TransformRunError, match="require active lease ownership"):
        runner.build(tmp_path, selected=[view_model])

    result = runner.build(
        tmp_path,
        selected=[view_model, incremental_model],
        ownership=ownership,
    )

    assert result.models == (base_model, view_model, incremental_model)
    assert result.assertions == 4
    assert ownership.verifications == 7
    with pool.connection() as connection:
        base_rows = connection.execute(
            sql.SQL("SELECT id, status FROM {}.{} ORDER BY id").format(
                sql.Identifier(namespace),
                sql.Identifier(base_model),
            )
        ).fetchall()
        view_count = connection.execute(
            sql.SQL("SELECT COUNT(*) AS count FROM {}.{}").format(
                sql.Identifier(namespace),
                sql.Identifier(view_model),
            )
        ).fetchone()
        commits = connection.execute(
            sql.SQL(
                "SELECT COUNT(*) AS count FROM {}.dander_target_commits "
                "WHERE status = 'committed' AND run_id = 'run-one'"
            ).format(sql.Identifier(namespace))
        ).fetchone()
    assert base_rows == [
        {"id": "one", "status": "open"},
        {"id": "two", "status": "closed"},
    ]
    assert view_count == {"count": 2}
    assert commits == {"count": 3}

    with pool.connection() as connection:
        connection.execute(
            sql.SQL("UPDATE raw.{} SET status = 'closed', updated_at = 3 WHERE id = 'one'").format(
                sql.Identifier(raw_table)
            )
        )
        connection.execute(
            sql.SQL(
                "INSERT INTO raw.{} (id, parent_id, status, updated_at) "
                "VALUES ('three', 'parent-one', 'open', 4)"
            ).format(sql.Identifier(raw_table))
        )
    replay = runner.build(
        tmp_path,
        selected=[incremental_model],
        ownership=_ownership(database, run_id="run-two", token=2),
    )
    assert replay.models == (base_model, incremental_model)
    with pool.connection() as connection:
        incremental_rows = connection.execute(
            sql.SQL("SELECT id, status, updated_at FROM {}.{} ORDER BY id").format(
                sql.Identifier(namespace),
                sql.Identifier(incremental_model),
            )
        ).fetchall()
    assert incremental_rows == [
        {"id": "one", "status": "closed", "updated_at": 3},
        {"id": "three", "status": "open", "updated_at": 4},
        {"id": "two", "status": "closed", "updated_at": 2},
    ]
    with pytest.raises(TargetFenceLostError, match="rejected stale publication ownership"):
        runner.build(
            tmp_path,
            selected=[incremental_model],
            ownership=ownership,
        )
    with pool.connection() as connection:
        after_stale_attempt = connection.execute(
            sql.SQL("SELECT id, status, updated_at FROM {}.{} ORDER BY id").format(
                sql.Identifier(namespace),
                sql.Identifier(incremental_model),
            )
        ).fetchall()
    assert after_stale_attempt == incremental_rows
    assert runner.test(tmp_path, selected=[incremental_model]).assertions == 4

    with pool.connection() as connection:
        connection.execute(
            sql.SQL("UPDATE raw.{} SET status = 'record-value-must-not-leak'").format(
                sql.Identifier(raw_table)
            )
        )
    with pytest.raises(TransformRunError, match=rf"{base_model}\.status\.accepted_values") as error:
        runner.build(
            tmp_path,
            selected=[base_model],
            ownership=_ownership(database, run_id="run-three", token=3),
        )
    assert "record-value-must-not-leak" not in str(error.value)


def _ownership(database: str, *, run_id: str, token: int) -> _Ownership:
    return _Ownership(
        FencingToken(
            lease_table=None,
            pipeline_id="postgresql_transforms",
            run_id=run_id,
            token=token,
            authority_id=f"postgresql:{database}-state",
        )
    )


def _write_models(
    root: Path,
    *,
    namespace: str,
    raw_table: str,
    parent_table: str,
    base_model: str,
    view_model: str,
    incremental_model: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    columns = dedent(
        """
        - name: id
          type: STRING
          description: Stable fixture identifier.
        - name: parent_id
          type: STRING
          description: Parent fixture identifier.
        - name: status
          type: STRING
          description: Fixture status.
        - name: updated_at
          type: INT64
          description: Monotonic fixture cursor.
        """
    ).strip()
    (root / f"{base_model}.sql").write_text(
        f"SELECT id, parent_id, status, updated_at FROM {{{{ ref('raw_{raw_table}') }}}}"
    )
    (root / f"{base_model}.yml").write_text(
        f"model: {base_model}\n"
        "description: Portable PostgreSQL base fixture.\n"
        "owner: data-eng\n"
        "dialect: portable\n"
        "materialization: table\n"
        f"dataset: {namespace}\n"
        "source_system: fixture\n"
        "sensitivity: public\n"
        "columns:\n"
        f"{indent(columns, '  ')}\n"
        "tests:\n"
        "  - column: id\n"
        "    not_null: true\n"
        "    unique: true\n"
        "  - column: status\n"
        "    accepted_values: [open, closed]\n"
        "  - column: parent_id\n"
        "    relationships:\n"
        f"      to: raw_{parent_table}\n"
        "      field: id\n"
    )
    for model, materialization in (
        (view_model, "view"),
        (incremental_model, "incremental"),
    ):
        (root / f"{model}.sql").write_text(
            f"SELECT id, parent_id, status, updated_at FROM {{{{ ref('{base_model}') }}}}"
        )
        incremental = (
            "unique_key: [id]\nincremental_cursor: updated_at\n"
            if materialization == "incremental"
            else ""
        )
        (root / f"{model}.yml").write_text(
            f"model: {model}\n"
            f"description: Portable PostgreSQL {materialization} fixture.\n"
            "owner: data-eng\n"
            "dialect: portable\n"
            f"materialization: {materialization}\n"
            f"dataset: {namespace}\n"
            "source_system: fixture\n"
            "sensitivity: public\n"
            f"{incremental}"
            "columns:\n"
            f"{indent(columns, '  ')}\n"
            "tests: []\n"
        )
