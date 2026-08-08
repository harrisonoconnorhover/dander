"""PostgreSQL destination-side target fencing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from psycopg import Connection, Cursor, sql
from psycopg_pool import ConnectionPool

from dander.concurrency import FencingToken, TargetFence, TargetFenceLostError
from dander.warehouse.runtime import PreparedWarehouseStatement

if TYPE_CHECKING:
    from dander.warehouse.contracts import RelationRef

PostgreSQLPool = ConnectionPool[Connection[dict[str, object]]]


@dataclass(frozen=True, slots=True)
class PostgreSQLFencePlan:
    """Provider-native statements surrounding one publication transaction."""

    touch_sql: sql.Composed
    commit_sql: sql.Composed
    parameters: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class PostgreSQLTargetFence:
    """Claim and verify publication ownership in the destination database."""

    pool: PostgreSQLPool
    catalog: str

    def claim(self, target: RelationRef, fence: FencingToken) -> TargetFence:
        """Atomically accept a newer token or an exact idempotent retry."""
        if target.catalog != self.catalog:
            raise ValueError("PostgreSQL target fence must use the runtime database")
        table = sql.Identifier(target.namespace, "dander_target_commits")
        target_id = ".".join(target.coordinates)
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(_postgresql_target_fence_table_sql(table))
            row = connection.execute(
                _postgresql_target_claim_sql(table),
                (
                    target_id,
                    fence.pipeline_id,
                    fence.resolved_authority_id,
                    fence.authority_epoch,
                    fence.run_id,
                    fence.token,
                ),
            ).fetchone()
        if row is None:
            raise TargetFenceLostError(
                f"Destination target {target_id!r} rejected stale publication ownership"
            )
        return TargetFence(
            fence_table=f"{target.namespace}.dander_target_commits",
            target_id=target_id,
            authority_id=fence.resolved_authority_id,
            authority_epoch=fence.authority_epoch,
            pipeline_id=fence.pipeline_id,
            run_id=fence.run_id,
            token=fence.token,
        )

    def prepare_dml(self, statement: str, fence: TargetFence) -> PreparedWarehouseStatement:
        """Return DML plus the exact transactional fence operations it requires."""
        finalizer = statement.strip().removesuffix(";")
        table = _postgresql_fence_identifier(fence.fence_table)
        return PreparedWarehouseStatement(
            sql=finalizer,
            options=PostgreSQLFencePlan(
                touch_sql=_postgresql_target_touch_sql(table),
                commit_sql=_postgresql_target_commit_sql(table),
                parameters=_postgresql_claim_parameters(fence),
            ),
        )

    def execute_dml(
        self,
        connection: Connection[dict[str, object]],
        statement: str,
        fence: TargetFence,
        parameters: tuple[object, ...] = (),
    ) -> Cursor[dict[str, object]]:
        """Execute exact ownership checks and publication in one transaction."""
        prepared = self.prepare_dml(statement, fence)
        plan = prepared.options
        assert isinstance(plan, PostgreSQLFencePlan)
        with connection.transaction():
            if connection.execute(plan.touch_sql, plan.parameters).fetchone() is None:
                raise TargetFenceLostError("Dander destination fence lost before publication")
            result = connection.execute(prepared.sql, parameters)
            if connection.execute(plan.commit_sql, plan.parameters).fetchone() is None:
                raise TargetFenceLostError("Dander destination fence lost during publication")
        return result


def _postgresql_target_fence_table_sql(table: sql.Identifier) -> sql.Composed:
    return sql.SQL(
        "CREATE TABLE IF NOT EXISTS {} ("
        "target_id TEXT NOT NULL, pipeline_id TEXT NOT NULL, "
        "authority_id TEXT NOT NULL, authority_epoch BIGINT NOT NULL CHECK (authority_epoch > 0), "
        "run_id TEXT NOT NULL, fencing_token BIGINT NOT NULL CHECK (fencing_token > 0), "
        "status TEXT NOT NULL CHECK (status IN ('claimed', 'committed')), "
        "claimed_at TIMESTAMPTZ NOT NULL, committed_at TIMESTAMPTZ, "
        "PRIMARY KEY (target_id, pipeline_id))"
    ).format(table)


def _postgresql_target_claim_sql(table: sql.Identifier) -> sql.Composed:
    return sql.SQL(
        "INSERT INTO {} AS current "
        "(target_id, pipeline_id, authority_id, authority_epoch, run_id, fencing_token, "
        "status, claimed_at, committed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'claimed', clock_timestamp(), NULL) "
        "ON CONFLICT (target_id, pipeline_id) DO UPDATE SET "
        "run_id = EXCLUDED.run_id, fencing_token = EXCLUDED.fencing_token, "
        "status = 'claimed', claimed_at = clock_timestamp(), committed_at = NULL "
        "WHERE current.authority_id = EXCLUDED.authority_id "
        "AND current.authority_epoch = EXCLUDED.authority_epoch "
        "AND (EXCLUDED.fencing_token > current.fencing_token OR "
        "(EXCLUDED.fencing_token = current.fencing_token "
        "AND EXCLUDED.run_id = current.run_id)) RETURNING fencing_token"
    ).format(table)


def _postgresql_target_touch_sql(table: sql.Identifier) -> sql.Composed:
    return sql.SQL("UPDATE {} SET claimed_at = claimed_at WHERE {} RETURNING fencing_token").format(
        table, sql.SQL(_postgresql_target_match_sql())
    )


def _postgresql_target_commit_sql(table: sql.Identifier) -> sql.Composed:
    return sql.SQL(
        "UPDATE {} SET status = 'committed', committed_at = clock_timestamp() "
        "WHERE {} RETURNING fencing_token"
    ).format(table, sql.SQL(_postgresql_target_match_sql()))


def _postgresql_target_match_sql() -> str:
    return (
        "target_id = %s AND pipeline_id = %s AND authority_id = %s "
        "AND authority_epoch = %s AND run_id = %s AND fencing_token = %s "
        "AND status IN ('claimed', 'committed')"
    )


def _postgresql_claim_parameters(fence: TargetFence) -> tuple[object, ...]:
    return (
        fence.target_id,
        fence.pipeline_id,
        fence.authority_id,
        fence.authority_epoch,
        fence.run_id,
        fence.token,
    )


def _postgresql_fence_identifier(value: str) -> sql.Identifier:
    coordinates = value.split(".")
    if len(coordinates) != 2 or any(not coordinate for coordinate in coordinates):
        raise ValueError("Invalid PostgreSQL target-fence table")
    return sql.Identifier(*coordinates)


__all__ = ["PostgreSQLFencePlan", "PostgreSQLTargetFence"]
