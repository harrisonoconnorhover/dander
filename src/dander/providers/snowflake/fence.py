"""Snowflake destination-side fencing around publication DML."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.concurrency import FencingToken, TargetFence, TargetFenceLostError
from dander.providers.snowflake.session import (
    SnowflakeConnection,
    SnowflakeConnectionFactory,
    SnowflakeStatementResult,
    execute,
    open_connection,
)
from dander.warehouse.runtime import PreparedWarehouseStatement

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dander.warehouse import RelationRef


@dataclass(frozen=True, slots=True)
class SnowflakeFencePlan:
    """Parameterized ownership statements surrounding one publication transaction."""

    touch_sql: str
    commit_sql: str
    parameters: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class SnowflakeTargetFence:
    """Claim and DML-touch exact destination ownership before publishing."""

    connection_factory: SnowflakeConnectionFactory
    database: str

    def claim(self, target: RelationRef, fence: FencingToken) -> TargetFence:
        """Accept a newer token or an exact idempotent retry in the destination."""
        if target.catalog != self.database:
            raise ValueError("Snowflake target fence must use the configured database")
        table = _qualified(target.catalog, target.namespace, "dander_target_commits")
        target_id = ".".join(target.coordinates)
        with open_connection(self.connection_factory) as connection:
            execute(
                connection,
                f"CREATE SCHEMA IF NOT EXISTS {_qualified(target.catalog, target.namespace)}",
            )
            execute(connection, _target_fence_table_sql(table))
            claim = execute(
                connection,
                _target_claim_sql(table),
                (
                    target_id,
                    fence.pipeline_id,
                    fence.resolved_authority_id,
                    fence.authority_epoch,
                    fence.run_id,
                    fence.token,
                ),
            )
            if claim.rowcount == 0:
                connection.rollback()
                raise TargetFenceLostError(
                    f"Destination target {target_id!r} rejected stale publication ownership"
                )
            # The Python connector inherits the account/user AUTOCOMMIT setting when its
            # ``autocommit`` argument is omitted. Persist the accepted claim explicitly so
            # accounts configured with AUTOCOMMIT=FALSE cannot lose it on connection close.
            connection.commit()
        return TargetFence(
            fence_table=f"{target.catalog}.{target.namespace}.dander_target_commits",
            target_id=target_id,
            authority_id=fence.resolved_authority_id,
            authority_epoch=fence.authority_epoch,
            pipeline_id=fence.pipeline_id,
            run_id=fence.run_id,
            token=fence.token,
        )

    def prepare_dml(self, statement: str, fence: TargetFence) -> PreparedWarehouseStatement:
        """Return publication DML plus its exact transactional ownership operations."""
        finalizer = statement.strip().removesuffix(";")
        return PreparedWarehouseStatement(sql=finalizer, options=self._plan(fence))

    def execute_dml(
        self,
        connection: SnowflakeConnection,
        statement: str,
        fence: TargetFence,
        parameters: Sequence[object] = (),
    ) -> SnowflakeStatementResult:
        """Touch ownership, publish, and commit the fence in one explicit transaction."""
        prepared = self.prepare_dml(statement, fence)
        plan = prepared.options
        assert isinstance(plan, SnowflakeFencePlan)
        execute(connection, "BEGIN TRANSACTION")
        try:
            touched = execute(connection, plan.touch_sql, plan.parameters)
            if touched.rowcount == 0:
                raise TargetFenceLostError("Dander destination fence lost before publication")
            result = execute(connection, prepared.sql, parameters)
            committed = execute(connection, plan.commit_sql, plan.parameters)
            if committed.rowcount == 0:
                raise TargetFenceLostError("Dander destination fence lost during publication")
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    def execute_statements(
        self,
        connection: SnowflakeConnection,
        statements: Sequence[tuple[str, Sequence[object]]],
        fence: TargetFence,
    ) -> SnowflakeStatementResult:
        """Publish an ordered DML group behind one exact target fence."""
        if not statements:
            raise ValueError("Snowflake fenced publication requires at least one statement")
        plan = self._plan(fence)
        execute(connection, "BEGIN TRANSACTION")
        try:
            touched = execute(connection, plan.touch_sql, plan.parameters)
            if touched.rowcount == 0:
                raise TargetFenceLostError("Dander destination fence lost before publication")
            result: SnowflakeStatementResult | None = None
            for statement, parameters in statements:
                result = execute(connection, statement, parameters)
            committed = execute(connection, plan.commit_sql, plan.parameters)
            if committed.rowcount == 0:
                raise TargetFenceLostError("Dander destination fence lost during publication")
            connection.commit()
            assert result is not None
            return result
        except Exception:
            connection.rollback()
            raise

    def _plan(self, fence: TargetFence) -> SnowflakeFencePlan:
        table = _fence_table(fence.fence_table)
        return SnowflakeFencePlan(
            touch_sql=_target_touch_sql(table),
            commit_sql=_target_commit_sql(table),
            parameters=_claim_parameters(fence),
        )


def _target_fence_table_sql(table: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ("
        '"target_id" VARCHAR NOT NULL, "pipeline_id" VARCHAR NOT NULL, '
        '"authority_id" VARCHAR NOT NULL, "authority_epoch" NUMBER(38,0) NOT NULL, '
        '"run_id" VARCHAR NOT NULL, "fencing_token" NUMBER(38,0) NOT NULL, '
        '"status" VARCHAR NOT NULL, "claimed_at" TIMESTAMP_TZ NOT NULL, '
        '"committed_at" TIMESTAMP_TZ)'
    )


def _target_claim_sql(table: str) -> str:
    return (
        f"MERGE INTO {table} AS current USING (SELECT ? AS target_id, ? AS pipeline_id, "
        "? AS authority_id, ? AS authority_epoch, ? AS run_id, ? AS fencing_token) incoming "
        'ON current."target_id" = incoming.target_id '
        'AND current."pipeline_id" = incoming.pipeline_id '
        'WHEN MATCHED AND current."authority_id" = incoming.authority_id '
        'AND current."authority_epoch" = incoming.authority_epoch '
        'AND (incoming.fencing_token > current."fencing_token" OR '
        '(incoming.fencing_token = current."fencing_token" '
        'AND incoming.run_id = current."run_id")) THEN UPDATE SET '
        'current."run_id" = incoming.run_id, '
        'current."fencing_token" = incoming.fencing_token, '
        'current."status" = \'claimed\', current."claimed_at" = CURRENT_TIMESTAMP(), '
        'current."committed_at" = NULL WHEN NOT MATCHED THEN INSERT '
        '("target_id", "pipeline_id", "authority_id", "authority_epoch", "run_id", '
        '"fencing_token", "status", "claimed_at", "committed_at") VALUES '
        "(incoming.target_id, incoming.pipeline_id, incoming.authority_id, "
        "incoming.authority_epoch, incoming.run_id, incoming.fencing_token, "
        "'claimed', CURRENT_TIMESTAMP(), NULL)"
    )


def _target_touch_sql(table: str) -> str:
    return (
        f'UPDATE {table} SET "claimed_at" = "claimed_at" WHERE '
        f"{_target_match_sql()} AND \"status\" IN ('claimed', 'committed')"
    )


def _target_commit_sql(table: str) -> str:
    return (
        f"UPDATE {table} SET \"status\" = 'committed', "
        f'"committed_at" = CURRENT_TIMESTAMP() WHERE {_target_match_sql()} '
        "AND \"status\" IN ('claimed', 'committed')"
    )


def _target_match_sql() -> str:
    return (
        '"target_id" = ? AND "pipeline_id" = ? AND "authority_id" = ? '
        'AND "authority_epoch" = ? AND "run_id" = ? AND "fencing_token" = ?'
    )


def _claim_parameters(fence: TargetFence) -> tuple[object, ...]:
    return (
        fence.target_id,
        fence.pipeline_id,
        fence.authority_id,
        fence.authority_epoch,
        fence.run_id,
        fence.token,
    )


def _fence_table(value: str) -> str:
    coordinates = value.split(".")
    if len(coordinates) != 3 or any(not coordinate for coordinate in coordinates):
        raise ValueError("Invalid Snowflake target-fence table")
    return _qualified(*coordinates)


def _qualified(*coordinates: str) -> str:
    return ".".join(_quote(coordinate) for coordinate in coordinates)


def _quote(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


__all__ = ["SnowflakeFencePlan", "SnowflakeTargetFence"]
