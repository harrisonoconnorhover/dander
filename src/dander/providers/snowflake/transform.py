"""Fenced Snowflake table and incremental model execution."""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dander.concurrency import TargetFenceLostError
from dander.providers.snowflake.session import (
    SnowflakeConnection,
    SnowflakeConnectionFactory,
    execute,
    open_connection,
)
from dander.providers.snowflake.writer import (
    SnowflakeWriteError,
    _ensure_target,
    _qualified,
    _quote,
    _set_query_tag,
    _snowflake_type,
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
    from dander.providers.snowflake.fence import SnowflakeTargetFence
    from dander.transform.config import GenericTestMetadata
    from dander.warehouse import RelationRef


@dataclass(frozen=True, slots=True)
class _SnowflakeModelPlan:
    model: TransformModel
    target: WriteTarget
    query: str


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
    ) -> None:
        self._database = database
        self._connection_factory = connection_factory
        self._target_fence = target_fence
        self._raw_namespace = raw_namespace

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
        project, models, plans, assertions = self._preflight(models_dir, selected=selected)
        del project
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
        """Run assertions against already materialized Snowflake relations."""
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

    def _publish(self, plan: _SnowflakeModelPlan, publication: TargetFence) -> None:
        target = WriteTarget(
            relation=plan.target.relation_ref,
            business_key=plan.target.business_key,
            schema=plan.target.schema,
            publication_fence=publication,
        )
        temporary = _temporary_relation(target.relation_ref, publication)
        cleanup_started = False
        with open_connection(self._connection_factory) as connection:
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
                execute(connection, _create_temporary_sql(plan, temporary))
                cleanup_started = True
                self._target_fence.execute_statements(
                    connection,
                    _publication_statements(plan, temporary),
                    publication,
                )
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

    def _run_assertions(
        self,
        assertions: Sequence[_SnowflakeAssertion],
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
                        f"Snowflake assertion execution failed: {assertion.name}"
                    ) from error
                if _failure_count(row, assertion.name) > 0:
                    failures.append(assertion.name)
        if failures:
            raise TransformRunError(f"Data tests failed: {', '.join(failures)}")


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
        )
        for column in model.metadata.columns
    )
    target = WriteTarget(
        relation=project.relation_ref_for_model(model),
        business_key=tuple(model.metadata.unique_key),
        schema=fields,
    )
    for field in target.canonical_schema.fields:
        _snowflake_type(field.data_type)
    return _SnowflakeModelPlan(
        model=model,
        target=target,
        query=project.compile(model),
    )


def _create_temporary_sql(plan: _SnowflakeModelPlan, temporary: str) -> str:
    names = tuple(field.name for field in plan.target.canonical_schema.fields)
    selected = ", ".join(f"source.{_quote(name)}" for name in names)
    if plan.model.metadata.materialization is Materialization.TABLE:
        return (
            f"CREATE OR REPLACE TEMPORARY TABLE {temporary} AS SELECT {selected} "
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
    if plan.model.metadata.materialization is Materialization.TABLE:
        return (
            (f"DELETE FROM {target}", ()),
            (f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {temporary}", ()),
        )
    keys = tuple(plan.model.metadata.unique_key)
    cursor = plan.model.metadata.incremental_cursor
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


__all__ = ["SnowflakeTransformRunner"]
