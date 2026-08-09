"""Serialized Redshift destination claims and transactional publication fencing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.concurrency import FencingToken, TargetFence, TargetFenceLostError
from dander.providers.redshift.config import validate_redshift_relation
from dander.providers.redshift.session import (
    RedshiftConnection,
    RedshiftConnectionFactory,
    RedshiftStatementResult,
    execute,
    open_connection,
)
from dander.warehouse.runtime import PreparedWarehouseStatement

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dander.warehouse import RelationRef


@dataclass(frozen=True, slots=True)
class RedshiftFencePlan:
    touch_sql: str
    commit_sql: str
    parameters: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class RedshiftTargetFence:
    connection_factory: RedshiftConnectionFactory
    database: str

    def claim(self, target: RelationRef, fence: FencingToken) -> TargetFence:
        validate_redshift_relation(target)
        if target.catalog != self.database:
            raise ValueError("Redshift target fence must use the configured database")
        table = _qualified(target.namespace, "dander_target_commits")
        target_id = ".".join(target.coordinates)
        with open_connection(self.connection_factory) as connection:
            try:
                execute(connection, f"CREATE SCHEMA IF NOT EXISTS {_quote(target.namespace)}")
                execute(connection, _fence_table_sql(table))
                connection.commit()
                execute(connection, "BEGIN")
                execute(connection, f"LOCK {table}")
                current = execute(
                    connection,
                    _select_claim_sql(table),
                    (target_id, fence.pipeline_id),
                    fetch="one",
                ).row
                if current is None:
                    execute(connection, _insert_claim_sql(table), _claim_values(target_id, fence))
                elif _claim_is_allowed(current, fence):
                    execute(connection, _update_claim_sql(table), _update_values(target_id, fence))
                else:
                    raise TargetFenceLostError(
                        f"Destination target {target_id!r} rejected stale publication ownership"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
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
        return PreparedWarehouseStatement(
            sql=statement.strip().removesuffix(";"),
            options=self._plan(fence),
        )

    def execute_dml(
        self,
        connection: RedshiftConnection,
        statement: str,
        fence: TargetFence,
        parameters: Sequence[object] = (),
    ) -> RedshiftStatementResult:
        return self.execute_statements(connection, ((statement, parameters),), fence)[0]

    def execute_statements(
        self,
        connection: RedshiftConnection,
        statements: Sequence[tuple[str, Sequence[object]]],
        fence: TargetFence,
    ) -> tuple[RedshiftStatementResult, ...]:
        if not statements:
            raise ValueError("Redshift fenced publication requires at least one statement")
        plan = self._plan(fence)
        table = _fence_table(fence.fence_table)
        execute(connection, "BEGIN")
        try:
            execute(connection, f"LOCK {table}")
            touched = execute(connection, plan.touch_sql, plan.parameters)
            if touched.rowcount != 1:
                raise TargetFenceLostError("Dander destination fence lost before publication")
            results: list[RedshiftStatementResult] = []
            for statement, parameters in statements:
                results.append(execute(connection, statement, parameters))
            committed = execute(connection, plan.commit_sql, plan.parameters)
            if committed.rowcount != 1:
                raise TargetFenceLostError("Dander destination fence lost during publication")
            connection.commit()
            return tuple(results)
        except Exception:
            connection.rollback()
            raise

    def _plan(self, fence: TargetFence) -> RedshiftFencePlan:
        table = _fence_table(fence.fence_table)
        parameters = (
            fence.target_id,
            fence.pipeline_id,
            fence.authority_id,
            fence.authority_epoch,
            fence.run_id,
            fence.token,
        )
        match = (
            '"target_id" = %s AND "pipeline_id" = %s AND "authority_id" = %s '
            'AND "authority_epoch" = %s AND "run_id" = %s AND "fencing_token" = %s'
        )
        return RedshiftFencePlan(
            touch_sql=f'UPDATE {table} SET "claimed_at" = "claimed_at" WHERE {match} '
            "AND \"status\" IN ('claimed', 'committed')",
            commit_sql=f"UPDATE {table} SET \"status\" = 'committed', "
            f'"committed_at" = GETDATE() WHERE {match} '
            "AND \"status\" IN ('claimed', 'committed')",
            parameters=parameters,
        )


def _claim_is_allowed(current: object, fence: FencingToken) -> bool:
    if not isinstance(current, (tuple, list)) or len(current) != 4:
        raise ValueError("Redshift fence claim returned an invalid row")
    authority_id, authority_epoch, run_id, token = current
    if (
        not isinstance(authority_id, str)
        or not isinstance(authority_epoch, int)
        or not isinstance(run_id, str)
        or not isinstance(token, int)
    ):
        raise ValueError("Redshift fence claim returned invalid values")
    exact_authority = (
        authority_id == fence.resolved_authority_id and authority_epoch == fence.authority_epoch
    )
    return exact_authority and (
        token < fence.token or (token == fence.token and run_id == fence.run_id)
    )


def _claim_values(target_id: str, fence: FencingToken) -> tuple[object, ...]:
    return (
        target_id,
        fence.pipeline_id,
        fence.resolved_authority_id,
        fence.authority_epoch,
        fence.run_id,
        fence.token,
    )


def _update_values(target_id: str, fence: FencingToken) -> tuple[object, ...]:
    return (
        fence.resolved_authority_id,
        fence.authority_epoch,
        fence.run_id,
        fence.token,
        target_id,
        fence.pipeline_id,
    )


def _fence_table_sql(table: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ("
        '"target_id" VARCHAR(1024) NOT NULL, "pipeline_id" VARCHAR(127) NOT NULL, '
        '"authority_id" VARCHAR(256) NOT NULL, "authority_epoch" BIGINT NOT NULL, '
        '"run_id" VARCHAR(127) NOT NULL, "fencing_token" BIGINT NOT NULL, '
        '"status" VARCHAR(16) NOT NULL, "claimed_at" TIMESTAMPTZ NOT NULL, '
        '"committed_at" TIMESTAMPTZ)'
    )


def _select_claim_sql(table: str) -> str:
    return (
        f'SELECT "authority_id", "authority_epoch", "run_id", "fencing_token" '
        f'FROM {table} WHERE "target_id" = %s AND "pipeline_id" = %s'
    )


def _insert_claim_sql(table: str) -> str:
    return (
        f'INSERT INTO {table} ("target_id", "pipeline_id", "authority_id", '
        '"authority_epoch", "run_id", "fencing_token", "status", "claimed_at", '
        '"committed_at") VALUES (%s, %s, %s, %s, %s, %s, '
        "'claimed', GETDATE(), NULL)"
    )


def _update_claim_sql(table: str) -> str:
    return (
        f'UPDATE {table} SET "authority_id" = %s, "authority_epoch" = %s, '
        '"run_id" = %s, "fencing_token" = %s, "status" = \'claimed\', '
        '"claimed_at" = GETDATE(), "committed_at" = NULL '
        'WHERE "target_id" = %s AND "pipeline_id" = %s'
    )


def _fence_table(value: str) -> str:
    parts = value.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid Redshift target-fence table")
    return _qualified(parts[1], parts[2])


def _qualified(*parts: str) -> str:
    return ".".join(_quote(part) for part in parts)


def _quote(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


__all__ = ["RedshiftFencePlan", "RedshiftTargetFence"]
