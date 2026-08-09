"""PostgreSQL materialization and generic assertion execution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from psycopg import Connection, sql
from psycopg_pool import ConnectionPool

from dander.providers.postgresql.writer import PostgreSQLTimeouts, _set_timeouts
from dander.transform import (
    SqlDialect,
    TransformModel,
    TransformProject,
    TransformProjectError,
    TransformRunError,
    TransformRunResult,
)
from dander.transform.model import Materialization

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from dander.concurrency import OwnershipGuard, TargetFence
    from dander.providers.postgresql.fence import PostgreSQLTargetFence
    from dander.transform.config import GenericTestMetadata
    from dander.warehouse import RelationRef

PostgreSQLRow = dict[str, object]
PostgreSQLPool = ConnectionPool[Connection[PostgreSQLRow]]


@dataclass(frozen=True, slots=True)
class _PostgreSQLAssertion:
    name: str
    statement: sql.SQL | sql.Composed
    parameters: tuple[object, ...] = ()


class PostgreSQLTransformRunner:
    """Build provider-exact or portable models in PostgreSQL 15 or newer."""

    target_dialect = SqlDialect.POSTGRES

    def __init__(
        self,
        *,
        database: str,
        pool: PostgreSQLPool,
        target_fence: PostgreSQLTargetFence,
        timeouts: PostgreSQLTimeouts,
        raw_namespace: str = "raw",
    ) -> None:
        self._database = database
        self._pool = pool
        self._target_fence = target_fence
        self._timeouts = timeouts
        self._raw_namespace = raw_namespace

    def build(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Materialize selected models behind per-target destination fencing."""
        if ownership is None or ownership.fence is None:
            raise TransformRunError("PostgreSQL hosted transforms require active lease ownership")
        project = TransformProject.load(
            models_dir,
            catalog=self._database,
            raw_namespace=self._raw_namespace,
            target_dialect=SqlDialect.POSTGRES,
        )
        models = project.ordered(selected)
        compiled = [
            (model, _materialization_statements(model, project.compile(model))) for model in models
        ]
        assertions = [
            assertion for model in models for assertion in _compile_assertions(project, model)
        ]
        for model, statements in compiled:
            ownership.verify()
            relation = _relation_ref(project, model)
            publication = self._target_fence.claim(relation, ownership.fence)
            self._publish(statements, publication)
        self._run_assertions(assertions, ownership=ownership)
        return TransformRunResult(
            models=tuple(model.name for model in models),
            assertions=len(assertions),
        )

    def test(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
    ) -> TransformRunResult:
        """Evaluate assertions against already materialized PostgreSQL relations."""
        project = TransformProject.load(
            models_dir,
            catalog=self._database,
            raw_namespace=self._raw_namespace,
            target_dialect=SqlDialect.POSTGRES,
        )
        models = project.ordered(selected)
        assertions = [
            assertion for model in models for assertion in _compile_assertions(project, model)
        ]
        self._run_assertions(assertions)
        return TransformRunResult(
            models=tuple(model.name for model in models),
            assertions=len(assertions),
        )

    def _publish(
        self,
        statements: Sequence[sql.SQL | sql.Composed],
        publication: TargetFence,
    ) -> None:
        with self._pool.connection() as connection, connection.transaction():
            _set_timeouts(connection, self._timeouts)
            self._target_fence.execute_statements(connection, statements, publication)

    def _run_assertions(
        self,
        assertions: Iterable[_PostgreSQLAssertion],
        *,
        ownership: OwnershipGuard | None = None,
    ) -> None:
        failures: list[str] = []
        with self._pool.connection() as connection, connection.transaction():
            _set_timeouts(connection, self._timeouts)
            for assertion in assertions:
                if ownership is not None:
                    ownership.verify()
                row = connection.execute(assertion.statement, assertion.parameters).fetchone()
                if row is None:
                    raise TransformRunError(
                        f"Assertion returned an invalid result: {assertion.name}"
                    )
                raw_count = row.get("failures")
                if isinstance(raw_count, bool) or not isinstance(raw_count, int):
                    raise TransformRunError(
                        f"Assertion returned an invalid result: {assertion.name}"
                    )
                if raw_count > 0:
                    failures.append(assertion.name)
        if failures:
            raise TransformRunError(f"Data tests failed: {', '.join(failures)}")


def _materialization_statements(
    model: TransformModel,
    query: str,
) -> tuple[sql.SQL | sql.Composed, ...]:
    relation = _relation_identifier(model)
    query_sql = sql.SQL(query)
    columns = tuple(column.name for column in model.metadata.columns)
    selected = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    source_selected = sql.SQL(", ").join(
        sql.SQL("source.{}").format(sql.Identifier(column)) for column in columns
    )
    match model.metadata.materialization:
        case Materialization.VIEW:
            return (sql.SQL("CREATE OR REPLACE VIEW {} AS {}").format(relation, query_sql),)
        case Materialization.TABLE:
            create = sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} AS SELECT {} FROM ({}) AS source WITH NO DATA"
            ).format(relation, source_selected, query_sql)
            replace = sql.SQL("TRUNCATE TABLE {}").format(relation)
            insert = sql.SQL("INSERT INTO {} ({}) SELECT {} FROM ({}) AS source").format(
                relation,
                selected,
                source_selected,
                query_sql,
            )
            return create, replace, insert
        case Materialization.INCREMENTAL:
            return _incremental_statements(model, query)


def _incremental_statements(
    model: TransformModel,
    query: str,
) -> tuple[sql.SQL | sql.Composed, ...]:
    unique_key = tuple(model.metadata.unique_key)
    cursor = model.metadata.incremental_cursor
    if not unique_key or cursor is None:
        raise TransformProjectError("Incremental materialization metadata is incomplete")
    relation = _relation_identifier(model)
    query_sql = sql.SQL(query)
    columns = tuple(column.name for column in model.metadata.columns)
    selected = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    source_selected = sql.SQL(", ").join(
        sql.SQL("source.{}").format(sql.Identifier(column)) for column in columns
    )
    incoming_selected = sql.SQL(", ").join(
        sql.SQL("incoming.{}").format(sql.Identifier(column)) for column in columns
    )
    keys = sql.SQL(", ").join(sql.Identifier(key) for key in unique_key)
    source_keys = sql.SQL(", ").join(
        sql.SQL("source.{}").format(sql.Identifier(key)) for key in unique_key
    )
    ordering = sql.SQL(", ").join(
        [
            *(sql.SQL("source.{}").format(sql.Identifier(key)) for key in unique_key),
            sql.SQL("source.{} DESC").format(sql.Identifier(cursor)),
            sql.SQL("to_jsonb(source)::text DESC"),
        ]
    )
    mutable = tuple(column for column in columns if column not in unique_key)
    conflict = (
        sql.SQL("DO UPDATE SET {}").format(
            sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(
                    sql.Identifier(column),
                    sql.Identifier(column),
                )
                for column in mutable
            )
        )
        if mutable
        else sql.SQL("DO NOTHING")
    )
    create = sql.SQL(
        "CREATE TABLE IF NOT EXISTS {} AS SELECT {} FROM ({}) AS source WITH NO DATA"
    ).format(relation, source_selected, query_sql)
    index = sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} ({})").format(
        sql.Identifier(_unique_index_name(model)),
        relation,
        keys,
    )
    insert = sql.SQL(
        "INSERT INTO {} ({}) SELECT {} FROM ("
        "SELECT DISTINCT ON ({}) {} FROM ({}) AS source "
        "WHERE NOT EXISTS (SELECT 1 FROM {}) OR source.{} >= (SELECT MAX({}) FROM {}) "
        "ORDER BY {}) AS incoming ON CONFLICT ({}) {}"
    ).format(
        relation,
        selected,
        incoming_selected,
        source_keys,
        source_selected,
        query_sql,
        relation,
        sql.Identifier(cursor),
        sql.Identifier(cursor),
        relation,
        ordering,
        keys,
        conflict,
    )
    return create, index, insert


def _compile_assertions(
    project: TransformProject,
    model: TransformModel,
) -> tuple[_PostgreSQLAssertion, ...]:
    relation = _relation_identifier(model)
    assertions: list[_PostgreSQLAssertion] = []
    for test in model.metadata.tests:
        assertions.extend(_assertions_for_test(project, model.name, relation, test))
    return tuple(assertions)


def _assertions_for_test(
    project: TransformProject,
    model_name: str,
    relation: sql.Identifier,
    test: GenericTestMetadata,
) -> list[_PostgreSQLAssertion]:
    column = sql.Identifier(test.column)
    assertions: list[_PostgreSQLAssertion] = []
    if test.not_null:
        assertions.append(
            _PostgreSQLAssertion(
                name=f"{model_name}.{test.column}.not_null",
                statement=sql.SQL(
                    "SELECT COUNT(*) FILTER (WHERE {} IS NULL) AS failures FROM {}"
                ).format(column, relation),
            )
        )
    if test.unique:
        assertions.append(
            _PostgreSQLAssertion(
                name=f"{model_name}.{test.column}.unique",
                statement=sql.SQL(
                    "SELECT COUNT(*) AS failures FROM ("
                    "SELECT {} FROM {} WHERE {} IS NOT NULL GROUP BY {} HAVING COUNT(*) > 1"
                    ") AS duplicates"
                ).format(column, relation, column, column),
            )
        )
    if test.accepted_values is not None:
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in test.accepted_values)
        assertions.append(
            _PostgreSQLAssertion(
                name=f"{model_name}.{test.column}.accepted_values",
                statement=sql.SQL(
                    "SELECT COUNT(*) FILTER (WHERE {} IS NOT NULL AND {} NOT IN ({})) "
                    "AS failures FROM {}"
                ).format(column, column, placeholders, relation),
                parameters=tuple(test.accepted_values),
            )
        )
    if test.relationships is not None:
        if test.relationships.to in project.models:
            parent_model = project.models[test.relationships.to]
            parent_columns = {column.name for column in parent_model.metadata.columns}
            if test.relationships.field not in parent_columns:
                raise TransformProjectError(
                    "Relationship test references an undeclared parent column: "
                    f"{test.relationships.to}.{test.relationships.field}"
                )
        parent = _relation_identifier_for_ref(project, test.relationships.to)
        parent_field = sql.Identifier(test.relationships.field)
        assertions.append(
            _PostgreSQLAssertion(
                name=f"{model_name}.{test.column}.relationships",
                statement=sql.SQL(
                    "SELECT COUNT(*) AS failures FROM {} AS child "
                    "LEFT JOIN {} AS parent ON child.{} = parent.{} "
                    "WHERE child.{} IS NOT NULL AND parent.{} IS NULL"
                ).format(
                    relation,
                    parent,
                    column,
                    parent_field,
                    column,
                    parent_field,
                ),
            )
        )
    return assertions


def _relation_ref(project: TransformProject, model: TransformModel) -> RelationRef:
    return project.relation_ref_for_model(model)


def _relation_identifier(model: TransformModel) -> sql.Identifier:
    return sql.Identifier(model.metadata.namespace, model.name)


def _relation_identifier_for_ref(
    project: TransformProject,
    reference: str,
) -> sql.Identifier:
    relation = project.relation_ref_for_ref(reference)
    return sql.Identifier(relation.namespace, relation.name)


def _unique_index_name(model: TransformModel) -> str:
    digest = hashlib.sha256(
        f"{model.metadata.namespace}.{model.name}:{','.join(model.metadata.unique_key)}".encode()
    ).hexdigest()[:10]
    return f"dander_uq_{model.name}"[:51] + f"_{digest}"


__all__ = ["PostgreSQLTransformRunner"]
