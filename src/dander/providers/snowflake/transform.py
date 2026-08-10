"""Fenced Snowflake table and incremental model execution."""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from time import perf_counter_ns
from typing import TYPE_CHECKING

from dander.concurrency import TargetFenceLostError
from dander.pipeline.compiler import CompiledTarget, PipelineCompileError
from dander.pipeline.runtime import GraphExecutionPlan, GraphRuntimeError
from dander.providers.snowflake.session import (
    SnowflakeConnection,
    SnowflakeConnectionFactory,
    SnowflakeStatementResult,
    enrich_operation_telemetry,
    execute,
    open_connection,
)
from dander.providers.snowflake.writer import (
    SnowflakeWriteError,
    _ensure_target,
    _qualified,
    _quote,
    _set_query_tag,
    validate_snowflake_schema,
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
    from dander.providers.snowflake.fence import SnowflakeTargetFence
    from dander.transform.config import GenericTestMetadata
    from dander.warehouse import RelationRef


@dataclass(frozen=True, slots=True)
class _SnowflakeModelPlan:
    name: str
    target: WriteTarget
    query: str
    materialization: Materialization
    unique_key: tuple[str, ...] = ()
    incremental_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class _SnowflakeAssertion:
    name: str
    statement: str
    parameters: tuple[object, ...] = ()


class SnowflakeTransformRunner:
    """Build portable table/incremental models behind destination fencing."""

    target_dialect = SqlDialect.SNOWFLAKE

    def __init__(
        self,
        *,
        database: str,
        connection_factory: SnowflakeConnectionFactory,
        target_fence: SnowflakeTargetFence,
        raw_namespace: str = "raw",
        warehouse: str | None = None,
    ) -> None:
        self._database = database
        self._connection_factory = connection_factory
        self._target_fence = target_fence
        self._raw_namespace = raw_namespace
        self._warehouse = warehouse

    def build(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Preflight the selected DAG, then publish each model through fenced DML."""
        if ownership is None or ownership.fence is None:
            raise TransformRunError("Snowflake hosted transforms require active lease ownership")
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
                    warehouse=self._warehouse,
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
        """Run assertions against already materialized Snowflake relations."""
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
        tuple[_SnowflakeModelPlan, ...],
        tuple[_SnowflakeAssertion, ...],
    ]:
        """Compile every selected model and assertion before opening a provider session."""
        project = TransformProject.load(
            models_dir,
            catalog=self._database,
            raw_namespace=self._raw_namespace,
            target_dialect=SqlDialect.SNOWFLAKE,
        )
        models = project.ordered(selected)
        plans = tuple(_model_plan(project, model) for model in models)
        assertions = tuple(
            assertion for model in models for assertion in _compile_assertions(project, model)
        )
        return project, models, plans, assertions

    def _run_assertions(
        self,
        assertions: Sequence[_SnowflakeAssertion],
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
                        f"Snowflake assertion execution failed: {assertion.name}"
                    ) from error
                telemetry.append(
                    _operation_telemetry(
                        result,
                        operation=TelemetryOperation.TEST,
                        duration_ms=duration_ms,
                        warehouse=self._warehouse,
                    )
                )
                if _failure_count(result.row, assertion.name) > 0:
                    failures.append(assertion.name)
            completed = enrich_operation_telemetry(connection, telemetry)
        if failures:
            raise TransformRunError(f"Data tests failed: {', '.join(failures)}")
        return completed


class SnowflakeGraphRunner:
    """Publish provider-neutral graph targets through Snowflake's fenced DML path."""

    target_dialect = SqlDialect.SNOWFLAKE

    def __init__(
        self,
        *,
        plan: GraphExecutionPlan,
        database: str,
        connection_factory: SnowflakeConnectionFactory,
        target_fence: SnowflakeTargetFence,
        warehouse: str | None = None,
    ) -> None:
        self._plan = plan
        self._database = database
        self._connection_factory = connection_factory
        self._target_fence = target_fence
        self._warehouse = warehouse

    def build(
        self,
        _models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Preflight every selected target, then publish each target exactly once."""
        if ownership is None or ownership.fence is None:
            raise GraphRuntimeError("Snowflake graph execution requires active lease ownership")
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

        # Rendering and schema validation may fail for a portable construct whose semantics are
        # not exact in Snowflake. Complete that preflight for every selected target before the
        # first claim or provider session so a graph can never partially publish for that reason.
        plans = tuple(_graph_plan(target, database=self._database) for target in targets)
        telemetry: list[OperationTelemetry] = []
        for plan in plans:
            ownership.verify()
            publication = self._target_fence.claim(
                plan.target.relation_ref,
                ownership.fence,
            )
            ownership.verify()
            telemetry.extend(
                _publish_plan(
                    plan,
                    publication,
                    connection_factory=self._connection_factory,
                    target_fence=self._target_fence,
                    warehouse=self._warehouse,
                )
            )
        return TransformRunResult(
            models=tuple(plan.name for plan in plans),
            assertions=0,
            telemetry=tuple(telemetry),
        )


def _publish_plan(
    plan: _SnowflakeModelPlan,
    publication: TargetFence,
    *,
    connection_factory: SnowflakeConnectionFactory,
    target_fence: SnowflakeTargetFence,
    warehouse: str | None,
) -> tuple[OperationTelemetry, ...]:
    target = WriteTarget(
        relation=plan.target.relation_ref,
        business_key=plan.target.business_key,
        schema=plan.target.schema,
        declared_schema=plan.target.canonical_schema,
        publication_fence=publication,
    )
    temporary = _temporary_relation(target.relation_ref, publication)
    cleanup_started = False
    telemetry: list[OperationTelemetry] = []
    with open_connection(connection_factory) as connection:
        try:
            _set_query_tag(connection, publication)
            # Transform targets never evolve automatically: the only permanent DDL permitted
            # here is create-if-absent followed by exact declared-schema validation.
            _ensure_target(
                connection,
                target,
                target.canonical_schema,
                evolution=SchemaEvolution.STRICT,
            )
            cleanup_started = True
            staged, duration_ms = _timed_call(
                execute,
                connection,
                _create_temporary_sql(plan, temporary),
            )
            telemetry.append(
                _operation_telemetry(
                    staged,
                    operation=TelemetryOperation.TRANSFORM,
                    duration_ms=duration_ms,
                    warehouse=warehouse,
                )
            )
            published, duration_ms = _timed_call(
                target_fence.execute_statements,
                connection,
                _publication_statements(plan, temporary),
                publication,
            )
            telemetry.append(
                _operation_telemetry(
                    published,
                    operation=TelemetryOperation.TRANSFORM,
                    duration_ms=duration_ms,
                    warehouse=warehouse,
                )
            )
            completed = enrich_operation_telemetry(connection, telemetry)
        except (TargetFenceLostError, SnowflakeWriteError, TransformRunError):
            raise
        except Exception as error:
            raise TransformRunError("Snowflake transform publication failed") from error
        finally:
            if cleanup_started:
                _drop_temporary(
                    connection,
                    temporary,
                    suppress_failure=sys.exc_info()[0] is not None,
                )
    return completed


def _model_plan(project: TransformProject, model: TransformModel) -> _SnowflakeModelPlan:
    if model.metadata.materialization is Materialization.VIEW:
        raise TransformProjectError(
            "Snowflake view materialization is unavailable because permanent DDL cannot satisfy "
            "transactional destination fencing"
        )
    if model.metadata.materialization not in {Materialization.TABLE, Materialization.INCREMENTAL}:
        raise TransformProjectError(
            f"Snowflake materialization is unavailable: {model.metadata.materialization.value}"
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
    validate_snowflake_schema(target.canonical_schema)
    return _SnowflakeModelPlan(
        name=model.name,
        target=target,
        query=project.compile(model),
        materialization=model.metadata.materialization,
        unique_key=tuple(model.metadata.unique_key),
        incremental_cursor=model.metadata.incremental_cursor,
    )


def _graph_plan(compiled: CompiledTarget, *, database: str) -> _SnowflakeModelPlan:
    if compiled.write_mode is not WriteMode.REPLACE:
        raise GraphRuntimeError(
            f"Snowflake graph target {compiled.node_id!r} requires replace mode"
        )
    if compiled.target.relation_ref.catalog != database:
        raise GraphRuntimeError(
            f"Snowflake graph target {compiled.node_id!r} belongs to another database"
        )
    try:
        validate_snowflake_schema(compiled.target.canonical_schema)
        query = compiled.render(SqlDialect.SNOWFLAKE)
    except (PipelineCompileError, SnowflakeWriteError) as error:
        raise GraphRuntimeError(str(error)) from error
    return _SnowflakeModelPlan(
        name=compiled.node_id,
        target=compiled.target,
        query=query,
        materialization=Materialization.TABLE,
    )


def _create_temporary_sql(plan: _SnowflakeModelPlan, temporary: str) -> str:
    names = tuple(field.name for field in plan.target.canonical_schema.fields)
    selected = ", ".join(f"source.{_quote(name)}" for name in names)
    if plan.materialization is Materialization.TABLE:
        return (
            f"CREATE OR REPLACE TEMPORARY TABLE {temporary} AS SELECT {selected} "
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
        f"CREATE OR REPLACE TEMPORARY TABLE {temporary} AS SELECT {projected} FROM ("
        f"SELECT {selected}, ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY "
        f"{', '.join(ordering)}) AS {_quote('_dander_rank')} FROM ({plan.query}) AS source"
        f") AS ranked WHERE ranked.{_quote('_dander_rank')} = 1"
    )


def _publication_statements(
    plan: _SnowflakeModelPlan,
    temporary: str,
) -> tuple[tuple[str, Sequence[object]], ...]:
    target = _qualified(*plan.target.relation_ref.coordinates)
    names = tuple(field.name for field in plan.target.canonical_schema.fields)
    columns = ", ".join(_quote(name) for name in names)
    incoming = ", ".join(f"incoming.{_quote(name)}" for name in names)
    if plan.materialization is Materialization.TABLE:
        return (
            (f"DELETE FROM {target}", ()),
            (f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {temporary}", ()),
        )
    keys = plan.unique_key
    cursor = plan.incremental_cursor
    assert keys and cursor is not None
    match = " AND ".join(f"target.{_quote(key)} = incoming.{_quote(key)}" for key in keys)
    mutable = tuple(name for name in names if name not in keys)
    update = ", ".join(f"target.{_quote(name)} = incoming.{_quote(name)}" for name in mutable)
    matched = (
        f" WHEN MATCHED AND incoming.{_quote(cursor)} >= target.{_quote(cursor)} "
        f"THEN UPDATE SET {update}"
        if mutable
        else ""
    )
    merge = (
        f"MERGE INTO {target} AS target USING {temporary} AS incoming ON {match}{matched} "
        f"WHEN NOT MATCHED THEN INSERT ({columns}) VALUES ({incoming})"
    )
    return ((merge, ()),)


def _compile_assertions(
    project: TransformProject,
    model: TransformModel,
) -> tuple[_SnowflakeAssertion, ...]:
    relation = _qualified(*project.relation_ref_for_model(model).coordinates)
    assertions: list[_SnowflakeAssertion] = []
    for test in model.metadata.tests:
        assertions.extend(_assertions_for_test(project, model.name, relation, test))
    return tuple(assertions)


def _assertions_for_test(
    project: TransformProject,
    model_name: str,
    relation: str,
    test: GenericTestMetadata,
) -> list[_SnowflakeAssertion]:
    column = _quote(test.column)
    assertions: list[_SnowflakeAssertion] = []
    if test.not_null:
        assertions.append(
            _SnowflakeAssertion(
                name=f"{model_name}.{test.column}.not_null",
                statement=f"SELECT COUNT_IF({column} IS NULL) AS failures FROM {relation}",
            )
        )
    if test.unique:
        assertions.append(
            _SnowflakeAssertion(
                name=f"{model_name}.{test.column}.unique",
                statement=(
                    "SELECT COUNT(*) AS failures FROM ("
                    f"SELECT {column} FROM {relation} WHERE {column} IS NOT NULL "
                    f"GROUP BY {column} HAVING COUNT(*) > 1) AS duplicates"
                ),
            )
        )
    if test.accepted_values is not None:
        placeholders = ", ".join("?" for _ in test.accepted_values)
        assertions.append(
            _SnowflakeAssertion(
                name=f"{model_name}.{test.column}.accepted_values",
                statement=(
                    f"SELECT COUNT_IF({column} IS NOT NULL AND {column} NOT IN "
                    f"({placeholders})) AS failures FROM {relation}"
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
        parent = _qualified(*project.relation_ref_for_ref(test.relationships.to).coordinates)
        parent_field = _quote(test.relationships.field)
        assertions.append(
            _SnowflakeAssertion(
                name=f"{model_name}.{test.column}.relationships",
                statement=(
                    f"SELECT COUNT(*) AS failures FROM {relation} AS child "
                    f"LEFT JOIN {parent} AS parent ON child.{column} = parent.{parent_field} "
                    f"WHERE child.{column} IS NOT NULL AND parent.{parent_field} IS NULL"
                ),
            )
        )
    return assertions


def _temporary_relation(relation: RelationRef, publication: TargetFence) -> str:
    digest = hashlib.sha256(
        f"{'.'.join(relation.coordinates)}:{publication.run_id}:{publication.token}".encode()
    ).hexdigest()[:20]
    return _qualified(relation.catalog, relation.namespace, f"dander_model_{digest}")


def _drop_temporary(
    connection: SnowflakeConnection,
    temporary: str,
    *,
    suppress_failure: bool,
) -> None:
    try:
        execute(connection, f"DROP TABLE IF EXISTS {temporary}")
    except Exception as error:
        if suppress_failure:
            return
        raise TransformRunError("Snowflake transform staging cleanup failed") from error


def _failure_count(row: object | None, assertion: str) -> int:
    if isinstance(row, dict):
        value = row.get("FAILURES", row.get("failures"))
    elif isinstance(row, (tuple, list)) and row:
        value = row[0]
    else:
        value = None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TransformRunError(f"Assertion returned an invalid result: {assertion}")
    return value


def _timed_call(
    function: Callable[..., SnowflakeStatementResult],
    *arguments: object,
    **keywords: object,
) -> tuple[SnowflakeStatementResult, int]:
    started = perf_counter_ns()
    result = function(*arguments, **keywords)
    duration_ms = max((perf_counter_ns() - started) // 1_000_000, 0)
    return result, duration_ms


def _operation_telemetry(
    result: SnowflakeStatementResult,
    *,
    operation: TelemetryOperation,
    duration_ms: int,
    warehouse: str | None,
) -> OperationTelemetry:
    return OperationTelemetry(
        provider="snowflake",
        operation=operation,
        duration_ms=duration_ms,
        rows_affected=result.rowcount,
        query_id=result.query_id,
        resource_name=warehouse,
    )


__all__ = ["SnowflakeGraphRunner", "SnowflakeTransformRunner"]
