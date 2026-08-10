"""Fenced Amazon Redshift table and incremental model execution."""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, replace
from time import perf_counter_ns
from typing import TYPE_CHECKING

from dander.concurrency import TargetFenceLostError
from dander.pipeline.compiler import CompiledTarget, PipelineCompileError
from dander.pipeline.runtime import GraphExecutionPlan, GraphRuntimeError
from dander.providers.redshift.config import validate_redshift_relation
from dander.providers.redshift.session import (
    RedshiftConnection,
    RedshiftConnectionFactory,
    RedshiftStatementResult,
    capture_last_query_id,
    enrich_operation_telemetry,
    execute,
    open_connection,
)
from dander.providers.redshift.writer import (
    RedshiftWriteError,
    _create_target_sql,
    _qualified,
    _quote,
    _redshift_field_type,
    _schema_changes,
    _set_query_group,
    _validate_super_roles,
    validate_redshift_schema,
)
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.transform import (
    SqlDialect,
    TransformModel,
    TransformProject,
    TransformProjectError,
    TransformRunError,
    TransformRunResult,
)
from dander.transform.model import Materialization
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

    from dander.concurrency import OwnershipGuard, TargetFence
    from dander.providers.redshift.fence import RedshiftTargetFence
    from dander.transform.config import GenericTestMetadata
    from dander.warehouse import RelationRef


@dataclass(frozen=True, slots=True)
class _RedshiftModelPlan:
    name: str
    target: WriteTarget
    query: str
    materialization: Materialization
    unique_key: tuple[str, ...] = ()
    incremental_cursor: str | None = None


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
        telemetry: list[OperationTelemetry] = []
        for plan in plans:
            ownership.verify()
            publication = self._target_fence.claim(plan.target.relation_ref, ownership.fence)
            ownership.verify()
            telemetry.extend(
                _publish_plan(
                    plan,
                    publication,
                    connection_factory=self._connection_factory,
                    target_fence=self._target_fence,
                    statement_timeout_ms=self._statement_timeout_ms,
                )
            )
        telemetry.extend(self._run_assertions(assertions, ownership=ownership))
        return TransformRunResult(
            models=tuple(model.name for model in models),
            assertions=len(assertions),
            telemetry=tuple(telemetry),
        )

    def test(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
    ) -> TransformRunResult:
        """Run assertions against already materialized Redshift relations."""
        _project, models, _plans, assertions = self._preflight(models_dir, selected=selected)
        telemetry = self._run_assertions(assertions)
        return TransformRunResult(
            models=tuple(model.name for model in models),
            assertions=len(assertions),
            telemetry=telemetry,
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

    def _run_assertions(
        self,
        assertions: Sequence[_RedshiftAssertion],
        *,
        ownership: OwnershipGuard | None = None,
    ) -> tuple[OperationTelemetry, ...]:
        failures: list[str] = []
        telemetry: list[OperationTelemetry] = []
        with open_connection(self._connection_factory) as connection:
            for assertion in assertions:
                if ownership is not None:
                    ownership.verify()
                try:
                    result, duration_ms = _timed_call(
                        execute,
                        connection,
                        assertion.statement,
                        assertion.parameters,
                        fetch="one",
                    )
                except Exception as error:
                    raise TransformRunError(
                        f"Redshift assertion execution failed: {assertion.name}"
                    ) from error
                telemetry.append(
                    _operation_telemetry(
                        result,
                        operation=TelemetryOperation.TEST,
                        duration_ms=duration_ms,
                    )
                )
                if _failure_count(result.row, assertion.name) > 0:
                    failures.append(assertion.name)
            connection.commit()
        if failures:
            raise TransformRunError(f"Data tests failed: {', '.join(failures)}")
        return tuple(telemetry)


class RedshiftGraphRunner:
    """Publish provider-neutral graph targets through Redshift's fenced DML path."""

    target_dialect = SqlDialect.REDSHIFT

    def __init__(
        self,
        *,
        plan: GraphExecutionPlan,
        database: str,
        connection_factory: RedshiftConnectionFactory,
        target_fence: RedshiftTargetFence,
        statement_timeout_ms: int,
    ) -> None:
        self._plan = plan
        self._database = database
        self._connection_factory = connection_factory
        self._target_fence = target_fence
        self._statement_timeout_ms = statement_timeout_ms

    def build(
        self,
        _models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Preflight every selected target, then publish each target exactly once."""
        if ownership is None or ownership.fence is None:
            raise GraphRuntimeError("Redshift graph execution requires active lease ownership")
        selected_ids = set(selected) if selected is not None else None
        known = {target.node_id for target in self._plan.targets}
        if selected_ids is not None and (unknown := sorted(selected_ids - known)):
            raise GraphRuntimeError(f"Unknown graph target: {unknown[0]!r}")
        targets = tuple(
            target
            for target in self._plan.targets
            if selected_ids is None or target.node_id in selected_ids
        )
        if not targets:
            raise GraphRuntimeError("Graph execution selected no targets")

        plans = tuple(_graph_plan(target, database=self._database) for target in targets)
        telemetry: list[OperationTelemetry] = []
        for plan in plans:
            ownership.verify()
            publication = self._target_fence.claim(plan.target.relation_ref, ownership.fence)
            ownership.verify()
            telemetry.extend(
                _publish_plan(
                    plan,
                    publication,
                    connection_factory=self._connection_factory,
                    target_fence=self._target_fence,
                    statement_timeout_ms=self._statement_timeout_ms,
                )
            )
        return TransformRunResult(
            models=tuple(plan.name for plan in plans),
            assertions=0,
            telemetry=tuple(telemetry),
        )


def _publish_plan(
    plan: _RedshiftModelPlan,
    publication: TargetFence,
    *,
    connection_factory: RedshiftConnectionFactory,
    target_fence: RedshiftTargetFence,
    statement_timeout_ms: int,
) -> tuple[OperationTelemetry, ...]:
    target = WriteTarget(
        relation=plan.target.relation_ref,
        business_key=plan.target.business_key,
        schema=plan.target.schema,
        declared_schema=plan.target.canonical_schema,
        publication_fence=publication,
    )
    temporary = _temporary_name(target.relation_ref, publication)
    cleanup_started = False
    telemetry: list[OperationTelemetry] = []
    with open_connection(connection_factory) as connection:
        try:
            _set_query_group(
                connection,
                publication,
                statement_timeout_ms=statement_timeout_ms,
            )
            cleanup_started = True
            staged, duration_ms = _timed_call(
                execute,
                connection,
                _create_temporary_sql(plan, temporary),
            )
            # Redshift temp tables survive commit. End CTAS and schema-inspection snapshots
            # before the single destination-fenced publication transaction begins.
            connection.commit()
            staged = replace(staged, query_id=capture_last_query_id(connection))
            telemetry.append(
                _operation_telemetry(
                    staged,
                    operation=TelemetryOperation.TRANSFORM,
                    duration_ms=duration_ms,
                )
            )
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
            started = perf_counter_ns()
            results = target_fence.execute_statements(connection, statements, publication)
            publication_duration_ms = _elapsed_milliseconds(started)
            telemetry.append(
                _operation_telemetry(
                    results[-1],
                    operation=TelemetryOperation.TRANSFORM,
                    duration_ms=publication_duration_ms,
                )
            )
            completed = enrich_operation_telemetry(connection, telemetry)
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
    return completed


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
            extensions=column.extensions,
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
        _redshift_field_type(field)
    _validate_super_roles(
        target,
        cursor_field=model.metadata.incremental_cursor,
        snapshot_field=None,
    )
    return _RedshiftModelPlan(
        name=model.name,
        target=target,
        query=project.compile(model),
        materialization=model.metadata.materialization,
        unique_key=tuple(model.metadata.unique_key),
        incremental_cursor=model.metadata.incremental_cursor,
    )


def _graph_plan(compiled: CompiledTarget, *, database: str) -> _RedshiftModelPlan:
    if compiled.write_mode is not WriteMode.REPLACE:
        raise GraphRuntimeError(f"Redshift graph target {compiled.node_id!r} requires replace mode")
    if compiled.target.relation_ref.catalog != database:
        raise GraphRuntimeError(
            f"Redshift graph target {compiled.node_id!r} belongs to another database"
        )
    try:
        validate_redshift_relation(compiled.target.relation_ref)
        validate_redshift_schema(compiled.target.canonical_schema)
        _validate_super_roles(compiled.target, cursor_field=None, snapshot_field=None)
        query = compiled.render(SqlDialect.REDSHIFT)
    except (PipelineCompileError, RedshiftWriteError, ValueError) as error:
        raise GraphRuntimeError(str(error)) from error
    return _RedshiftModelPlan(
        name=compiled.node_id,
        target=compiled.target,
        query=query,
        materialization=Materialization.TABLE,
    )


def _create_temporary_sql(plan: _RedshiftModelPlan, temporary: str) -> str:
    names = tuple(field.name for field in plan.target.canonical_schema.fields)
    selected = ", ".join(f"source.{_quote(name)}" for name in names)
    if plan.materialization is Materialization.TABLE:
        return (
            f"CREATE TEMP TABLE {_quote(temporary)} AS SELECT {selected} "
            f"FROM ({plan.query}) AS source"
        )
    keys = plan.unique_key
    cursor = plan.incremental_cursor
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
    if plan.materialization is Materialization.TABLE:
        return (
            (f"DELETE FROM {target}", ()),
            (f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {staged}", ()),
        )
    keys = plan.unique_key
    cursor = plan.incremental_cursor
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


def _timed_call(
    function: Callable[..., RedshiftStatementResult],
    *arguments: object,
    **keywords: object,
) -> tuple[RedshiftStatementResult, int]:
    started = perf_counter_ns()
    result = function(*arguments, **keywords)
    return result, _elapsed_milliseconds(started)


def _elapsed_milliseconds(started: int) -> int:
    return max((perf_counter_ns() - started) // 1_000_000, 0)


def _operation_telemetry(
    result: RedshiftStatementResult,
    *,
    operation: TelemetryOperation,
    duration_ms: int,
) -> OperationTelemetry:
    return OperationTelemetry(
        provider="redshift",
        operation=operation,
        duration_ms=duration_ms,
        rows_affected=max(result.rowcount, 0),
        query_id=result.query_id,
    )


__all__ = ["RedshiftGraphRunner", "RedshiftTransformRunner"]
