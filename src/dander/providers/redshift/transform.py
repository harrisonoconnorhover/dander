"""Fenced Amazon Redshift table and incremental model execution."""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.concurrency import TargetFenceLostError
from dander.providers.redshift.config import validate_redshift_relation
from dander.providers.redshift.session import (
    RedshiftConnection,
    RedshiftConnectionFactory,
    execute,
    open_connection,
)
from dander.providers.redshift.writer import (
    RedshiftWriteError,
    _create_target_sql,
    _qualified,
    _quote,
    _redshift_type,
    _schema_changes,
    _set_query_group,
)
from dander.transform import (
    SqlDialect,
    TransformModel,
    TransformProject,
    TransformProjectError,
    TransformRunError,
    TransformRunResult,
)
from dander.transform.model import Materialization
from dander.writer import SchemaEvolution, WriteField, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from dander.concurrency import OwnershipGuard, TargetFence
    from dander.providers.redshift.fence import RedshiftTargetFence
    from dander.transform.config import GenericTestMetadata
    from dander.warehouse import RelationRef


@dataclass(frozen=True, slots=True)
class _RedshiftModelPlan:
    model: TransformModel
    target: WriteTarget
    query: str


@dataclass(frozen=True, slots=True)
class _RedshiftAssertion:
    name: str
    statement: str
    parameters: tuple[object, ...] = ()


class RedshiftTransformRunner:
    """Build portable table/incremental models behind destination fencing."""

    target_dialect = SqlDialect.REDSHIFT

    def __init__(
        self,
        *,
        database: str,
        connection_factory: RedshiftConnectionFactory,
        target_fence: RedshiftTargetFence,
        statement_timeout_ms: int,
        raw_namespace: str = "raw",
    ) -> None:
        self._database = database
        self._connection_factory = connection_factory
        self._target_fence = target_fence
        self._statement_timeout_ms = statement_timeout_ms
        self._raw_namespace = raw_namespace

    def build(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Preflight the selected DAG, then publish every model through fenced DML."""
        if ownership is None or ownership.fence is None:
            raise TransformRunError("Redshift hosted transforms require active lease ownership")
        _project, models, plans, assertions = self._preflight(models_dir, selected=selected)
        for plan in plans:
            ownership.verify()
            publication = self._target_fence.claim(plan.target.relation_ref, ownership.fence)
            ownership.verify()
            self._publish(plan, publication)
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
        """Run assertions against already materialized Redshift relations."""
        _project, models, _plans, assertions = self._preflight(models_dir, selected=selected)
        self._run_assertions(assertions)
        return TransformRunResult(
            models=tuple(model.name for model in models),
            assertions=len(assertions),
        )

    def _preflight(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None,
    ) -> tuple[
        TransformProject,
        tuple[TransformModel, ...],
        tuple[_RedshiftModelPlan, ...],
        tuple[_RedshiftAssertion, ...],
    ]:
        """Compile every selected model and assertion before opening a provider session."""
        project = TransformProject.load(
            models_dir,
            catalog=self._database,
            raw_namespace=self._raw_namespace,
            target_dialect=SqlDialect.REDSHIFT,
        )
        models = project.ordered(selected)
        plans = tuple(_model_plan(project, model) for model in models)
        assertions = tuple(
            assertion for model in models for assertion in _compile_assertions(project, model)
        )
        return project, models, plans, assertions

    def _publish(self, plan: _RedshiftModelPlan, publication: TargetFence) -> None:
        target = WriteTarget(
            relation=plan.target.relation_ref,
            business_key=plan.target.business_key,
            schema=plan.target.schema,
            publication_fence=publication,
        )
        temporary = _temporary_name(target.relation_ref, publication)
        cleanup_started = False
        with open_connection(self._connection_factory) as connection:
            try:
                _set_query_group(
                    connection,
                    publication,
                    statement_timeout_ms=self._statement_timeout_ms,
                )
                cleanup_started = True
                execute(connection, _create_temporary_sql(plan, temporary))
                # Redshift temp tables survive commit. End CTAS and schema-inspection snapshots
                # before the single destination-fenced publication transaction begins.
                connection.commit()
                schema_changes = _schema_changes(
                    connection,
                    target,
                    target.canonical_schema,
                    evolution=SchemaEvolution.STRICT,
                )
                connection.commit()
                statements = (
                    (_create_target_sql(target, target.canonical_schema), ()),
                    *schema_changes,
                    *_publication_statements(plan, temporary),
                )
                self._target_fence.execute_statements(connection, statements, publication)
            except (TargetFenceLostError, RedshiftWriteError, TransformRunError):
                raise
            except Exception as error:
                raise TransformRunError("Redshift transform publication failed") from error
            finally:
                if cleanup_started:
                    _drop_temporary(
                        connection,
                        temporary,
                        suppress_failure=sys.exc_info()[0] is not None,
                    )

    def _run_assertions(
        self,
        assertions: Sequence[_RedshiftAssertion],
        *,
        ownership: OwnershipGuard | None = None,
    ) -> None:
        failures: list[str] = []
        with open_connection(self._connection_factory) as connection:
            for assertion in assertions:
                if ownership is not None:
                    ownership.verify()
                try:
                    row = execute(
                        connection,
                        assertion.statement,
                        assertion.parameters,
                        fetch="one",
                    ).row
                except Exception as error:
                    raise TransformRunError(
                        f"Redshift assertion execution failed: {assertion.name}"
                    ) from error
                if _failure_count(row, assertion.name) > 0:
                    failures.append(assertion.name)
            connection.commit()
        if failures:
            raise TransformRunError(f"Data tests failed: {', '.join(failures)}")


def _model_plan(project: TransformProject, model: TransformModel) -> _RedshiftModelPlan:
    if model.metadata.materialization is Materialization.VIEW:
        raise TransformProjectError(
            "Redshift view materialization is unavailable in the fenced transform slice"
        )
    if model.metadata.materialization not in {Materialization.TABLE, Materialization.INCREMENTAL}:
        raise TransformProjectError(
            f"Redshift materialization is unavailable: {model.metadata.materialization.value}"
        )
    required = set(model.metadata.unique_key)
    if model.metadata.incremental_cursor is not None:
        required.add(model.metadata.incremental_cursor)
    fields = tuple(
        WriteField(
            name=column.name,
            data_type=column.data_type,
            mode="REQUIRED" if column.name in required else "NULLABLE",
        )
        for column in model.metadata.columns
    )
    target = WriteTarget(
        relation=project.relation_ref_for_model(model),
        business_key=tuple(model.metadata.unique_key),
        schema=fields,
    )
    validate_redshift_relation(target.relation_ref)
    for field in target.canonical_schema.fields:
        _redshift_type(field.data_type)
    return _RedshiftModelPlan(model=model, target=target, query=project.compile(model))


def _create_temporary_sql(plan: _RedshiftModelPlan, temporary: str) -> str:
    names = tuple(field.name for field in plan.target.canonical_schema.fields)
    selected = ", ".join(f"source.{_quote(name)}" for name in names)
    if plan.model.metadata.materialization is Materialization.TABLE:
        return (
            f"CREATE TEMP TABLE {_quote(temporary)} AS SELECT {selected} "
            f"FROM ({plan.query}) AS source"
        )
    keys = tuple(plan.model.metadata.unique_key)
    cursor = plan.model.metadata.incremental_cursor
    assert keys and cursor is not None
    partition = ", ".join(f"source.{_quote(key)}" for key in keys)
    tie_breakers = tuple(name for name in sorted(names) if name not in {*keys, cursor})
    ordering = [f"source.{_quote(cursor)} DESC NULLS LAST"]
    ordering.extend(f"source.{_quote(name)} DESC NULLS LAST" for name in tie_breakers)
    projected = ", ".join(f"ranked.{_quote(name)}" for name in names)
    return (
        f"CREATE TEMP TABLE {_quote(temporary)} AS SELECT {projected} FROM ("
        f"SELECT {selected}, ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY "
        f"{', '.join(ordering)}) AS {_quote('_dander_rank')} FROM ({plan.query}) AS source"
        f") AS ranked WHERE ranked.{_quote('_dander_rank')} = 1"
    )


def _publication_statements(
    plan: _RedshiftModelPlan,
    temporary: str,
) -> tuple[tuple[str, Sequence[object]], ...]:
    target = _qualified(plan.target.relation_ref.namespace, plan.target.relation_ref.name)
    staged = _quote(temporary)
    names = tuple(field.name for field in plan.target.canonical_schema.fields)
    columns = ", ".join(_quote(name) for name in names)
    incoming = ", ".join(f"incoming.{_quote(name)}" for name in names)
    if plan.model.metadata.materialization is Materialization.TABLE:
        return (
            (f"DELETE FROM {target}", ()),
            (f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {staged}", ()),
        )
    keys = tuple(plan.model.metadata.unique_key)
    cursor = plan.model.metadata.incremental_cursor
    assert keys and cursor is not None
    match = " AND ".join(f"target.{_quote(key)} = incoming.{_quote(key)}" for key in keys)
    mutable = tuple(name for name in names if name not in keys)
    updates = ", ".join(f"{_quote(name)} = incoming.{_quote(name)}" for name in mutable)
    statements: list[tuple[str, Sequence[object]]] = []
    if mutable:
        statements.append(
            (
                f"UPDATE {target} AS target SET {updates} FROM {staged} AS incoming "
                f"WHERE {match} AND incoming.{_quote(cursor)} >= target.{_quote(cursor)}",
                (),
            )
        )
    missing = " AND ".join(f"existing.{_quote(key)} = incoming.{_quote(key)}" for key in keys)
    statements.append(
        (
            f"INSERT INTO {target} ({columns}) SELECT {incoming} FROM {staged} AS incoming "
            f"WHERE NOT EXISTS (SELECT 1 FROM {target} AS existing WHERE {missing})",
            (),
        )
    )
    return tuple(statements)


def _compile_assertions(
    project: TransformProject,
    model: TransformModel,
) -> tuple[_RedshiftAssertion, ...]:
    relation = _local_relation(project.relation_ref_for_model(model))
    assertions: list[_RedshiftAssertion] = []
    for test in model.metadata.tests:
        assertions.extend(_assertions_for_test(project, model.name, relation, test))
    return tuple(assertions)


def _assertions_for_test(
    project: TransformProject,
    model_name: str,
    relation: str,
    test: GenericTestMetadata,
) -> list[_RedshiftAssertion]:
    column = _quote(test.column)
    assertions: list[_RedshiftAssertion] = []
    if test.not_null:
        assertions.append(
            _RedshiftAssertion(
                name=f"{model_name}.{test.column}.not_null",
                statement=f"SELECT COUNT(*) AS failures FROM {relation} WHERE {column} IS NULL",
            )
        )
    if test.unique:
        assertions.append(
            _RedshiftAssertion(
                name=f"{model_name}.{test.column}.unique",
                statement=(
                    "SELECT COUNT(*) AS failures FROM ("
                    f"SELECT {column} FROM {relation} WHERE {column} IS NOT NULL "
                    f"GROUP BY {column} HAVING COUNT(*) > 1) AS duplicates"
                ),
            )
        )
    if test.accepted_values is not None:
        placeholders = ", ".join("%s" for _ in test.accepted_values)
        assertions.append(
            _RedshiftAssertion(
                name=f"{model_name}.{test.column}.accepted_values",
                statement=(
                    f"SELECT COUNT(*) AS failures FROM {relation} WHERE {column} IS NOT NULL "
                    f"AND {column} NOT IN ({placeholders})"
                ),
                parameters=tuple(test.accepted_values),
            )
        )
    if test.relationships is not None:
        if test.relationships.to in project.models:
            parent_model = project.models[test.relationships.to]
            parent_columns = {item.name for item in parent_model.metadata.columns}
            if test.relationships.field not in parent_columns:
                raise TransformProjectError(
                    "Relationship test references an undeclared parent column: "
                    f"{test.relationships.to}.{test.relationships.field}"
                )
        parent = _local_relation(project.relation_ref_for_ref(test.relationships.to))
        parent_field = _quote(test.relationships.field)
        assertions.append(
            _RedshiftAssertion(
                name=f"{model_name}.{test.column}.relationships",
                statement=(
                    f"SELECT COUNT(*) AS failures FROM {relation} AS child "
                    f"LEFT JOIN {parent} AS parent ON child.{column} = parent.{parent_field} "
                    f"WHERE child.{column} IS NOT NULL AND parent.{parent_field} IS NULL"
                ),
            )
        )
    return assertions


def _local_relation(relation: RelationRef) -> str:
    return _qualified(relation.namespace, relation.name)


def _temporary_name(relation: RelationRef, publication: TargetFence) -> str:
    digest = hashlib.sha256(
        f"{'.'.join(relation.coordinates)}:{publication.run_id}:{publication.token}".encode()
    ).hexdigest()[:20]
    return f"dander_model_{digest}"


def _drop_temporary(
    connection: RedshiftConnection,
    temporary: str,
    *,
    suppress_failure: bool,
) -> None:
    try:
        execute(connection, f"DROP TABLE IF EXISTS {_quote(temporary)}")
        connection.commit()
    except Exception as error:
        connection.rollback()
        if suppress_failure:
            return
        raise TransformRunError("Redshift transform staging cleanup failed") from error


def _failure_count(row: object | None, assertion: str) -> int:
    if isinstance(row, dict):
        value = row.get("failures")
    elif isinstance(row, (tuple, list)) and row:
        value = row[0]
    else:
        value = None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TransformRunError(f"Assertion returned an invalid result: {assertion}")
    return value


__all__ = ["RedshiftTransformRunner"]
