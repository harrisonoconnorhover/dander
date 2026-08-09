"""BigQuery materialization and generic data-test execution."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Protocol, cast

import sqlglot
from google.cloud import bigquery

from dander._bigquery_retry import run_mutation_with_retry
from dander.concurrency import OwnershipGuard, fenced_dml, fencing_job_config
from dander.transform.model import Materialization, SqlDialect
from dander.transform.project import (
    TransformModel,
    TransformProject,
    TransformProjectError,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from dander.transform.config import GenericTestMetadata


class TransformRunError(RuntimeError):
    """Raised when model execution or a generic assertion fails."""


class _QueryRow(Protocol):
    def __getitem__(self, key: str) -> object:
        """Return a projected result value by column name."""


class _QueryJob(Protocol):
    def result(self) -> Iterable[_QueryRow]:
        """Wait for completion and return query rows."""


class _BigQueryClient(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _QueryJob:
        """Submit BigQuery Standard SQL."""


@dataclass(frozen=True)
class TransformRunResult:
    """Summary of models materialized and assertions evaluated."""

    models: tuple[str, ...]
    assertions: int


@dataclass(frozen=True)
class _Assertion:
    name: str
    sql: str


@dataclass(frozen=True)
class _MaterializationStatement:
    sql: str
    dml_finalizer: bool = False


class BigQueryTransformRunner:
    """Build and test selected transform models with an injected BigQuery client."""

    target_dialect = SqlDialect.BIGQUERY

    def __init__(self, *, project: str, client: _BigQueryClient | None = None) -> None:
        self._project = project
        self._client = client or cast("_BigQueryClient", bigquery.Client(project=project))

    def build(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Materialize selected models in dependency order, then run their assertions."""
        project = TransformProject.load(models_dir, project_id=self._project)
        models = project.ordered(selected)
        compiled = [(model, project.compile(model)) for model in models]
        statements = [
            statement
            for model, query in compiled
            for statement in _materialization_statements(project, model, query)
        ]
        assertions = [
            assertion for model in models for assertion in _compile_assertions(project, model)
        ]
        for statement in statements:
            if ownership is not None:
                ownership.verify()
            if statement.dml_finalizer and ownership is not None and ownership.fence is not None:
                script = fenced_dml(statement.sql, ownership.fence)
                config = fencing_job_config(ownership.fence)
                run_mutation_with_retry(partial(self._client.query, script, job_config=config))
            else:
                self._client.query(statement.sql).result()
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
        """Run assertions against already-materialized selected model relations."""
        project = TransformProject.load(models_dir, project_id=self._project)
        models = project.ordered(selected)
        assertions = [
            assertion for model in models for assertion in _compile_assertions(project, model)
        ]
        self._run_assertions(assertions)
        return TransformRunResult(
            models=tuple(model.name for model in models),
            assertions=len(assertions),
        )

    def _run_assertions(
        self,
        assertions: Iterable[_Assertion],
        *,
        ownership: OwnershipGuard | None = None,
    ) -> None:
        failures: list[str] = []
        for assertion in assertions:
            if ownership is not None:
                ownership.verify()
            rows = list(self._client.query(assertion.sql).result())
            if len(rows) != 1:
                raise TransformRunError(f"Assertion returned an invalid result: {assertion.name}")
            try:
                raw_count = rows[0]["failures"]
            except (KeyError, TypeError, ValueError) as error:
                raise TransformRunError(
                    f"Assertion returned an invalid result: {assertion.name}"
                ) from error
            if isinstance(raw_count, bool) or not isinstance(raw_count, (int, str)):
                raise TransformRunError(f"Assertion returned an invalid result: {assertion.name}")
            try:
                failure_count = int(raw_count)
            except ValueError as error:
                raise TransformRunError(
                    f"Assertion returned an invalid result: {assertion.name}"
                ) from error
            if failure_count > 0:
                failures.append(assertion.name)
        if failures:
            raise TransformRunError(f"Data tests failed: {', '.join(failures)}")


def _materialization_sql(project: TransformProject, model: TransformModel, query: str) -> str:
    relation = project.relation_for_model(model)
    match model.metadata.materialization:
        case Materialization.VIEW:
            return f"CREATE OR REPLACE VIEW {relation} AS\n{query}"
        case Materialization.TABLE:
            return f"CREATE OR REPLACE TABLE {relation} AS\n{query}"
        case Materialization.INCREMENTAL:
            return _incremental_materialization_sql(
                relation=relation,
                query=query,
                columns=tuple(column.name for column in model.metadata.columns),
                unique_key=tuple(model.metadata.unique_key),
                cursor=model.metadata.incremental_cursor,
            )


def _materialization_statements(
    project: TransformProject,
    model: TransformModel,
    query: str,
) -> tuple[_MaterializationStatement, ...]:
    if model.metadata.materialization is not Materialization.INCREMENTAL:
        return (_MaterializationStatement(_materialization_sql(project, model, query)),)
    relation = project.relation_for_model(model)
    create, merge = _incremental_materialization_statements(
        relation=relation,
        query=query,
        columns=tuple(column.name for column in model.metadata.columns),
        unique_key=tuple(model.metadata.unique_key),
        cursor=model.metadata.incremental_cursor,
    )
    return (
        _MaterializationStatement(create),
        _MaterializationStatement(merge, dml_finalizer=True),
    )


def _incremental_materialization_sql(
    *,
    relation: str,
    query: str,
    columns: tuple[str, ...],
    unique_key: tuple[str, ...],
    cursor: str | None,
) -> str:
    create, merge = _incremental_materialization_statements(
        relation=relation,
        query=query,
        columns=columns,
        unique_key=unique_key,
        cursor=cursor,
    )
    return f"{create};\n{merge}"


def _incremental_materialization_statements(
    *,
    relation: str,
    query: str,
    columns: tuple[str, ...],
    unique_key: tuple[str, ...],
    cursor: str | None,
) -> tuple[str, str]:
    if not unique_key or cursor is None:
        raise TransformProjectError("Incremental materialization metadata is incomplete")
    selected = ", ".join(f"`{column}`" for column in columns)
    source_selected = ", ".join(f"source.`{column}`" for column in columns)
    match = " AND ".join(f"target.`{key}` = source.`{key}`" for key in unique_key)
    mutable = [column for column in columns if column not in unique_key]
    matched = ""
    if mutable:
        assignments = ", ".join(f"target.`{column}` = source.`{column}`" for column in mutable)
        matched = f"\nWHEN MATCHED THEN UPDATE SET {assignments}"
    source_query = (
        f"SELECT {source_selected}\n"
        f"FROM (\n{query}\n) AS source\n"
        f"WHERE NOT EXISTS (SELECT 1 FROM {relation})\n"
        f"   OR source.`{cursor}` >= (SELECT MAX(`{cursor}`) FROM {relation})\n"
        f"QUALIFY ROW_NUMBER() OVER (\n"
        f"  PARTITION BY {', '.join(f'source.`{key}`' for key in unique_key)}\n"
        f"  ORDER BY source.`{cursor}` DESC, TO_JSON_STRING(source) DESC\n"
        f") = 1"
    )
    create = (
        f"CREATE TABLE IF NOT EXISTS {relation} AS\n"
        f"SELECT {selected} FROM (\n{query}\n) AS source WHERE FALSE"
    )
    merge = (
        f"MERGE {relation} AS target\n"
        f"USING (\n{source_query}\n) AS source\n"
        f"ON {match}"
        f"{matched}\n"
        f"WHEN NOT MATCHED THEN INSERT ({selected}) VALUES ({source_selected})"
    )
    return create, merge


def _compile_assertions(
    project: TransformProject,
    model: TransformModel,
) -> tuple[_Assertion, ...]:
    relation = project.relation_for_model(model)
    assertions: list[_Assertion] = []
    for test in model.metadata.tests:
        assertions.extend(_assertions_for_test(project, model.name, relation, test))
    return tuple(assertions)


def _assertions_for_test(
    project: TransformProject,
    model_name: str,
    relation: str,
    test: GenericTestMetadata,
) -> list[_Assertion]:
    column = f"`{test.column}`"
    assertions: list[_Assertion] = []
    if test.not_null:
        assertions.append(
            _Assertion(
                name=f"{model_name}.{test.column}.not_null",
                sql=f"SELECT COUNTIF({column} IS NULL) AS failures FROM {relation}",
            )
        )
    if test.unique:
        assertions.append(
            _Assertion(
                name=f"{model_name}.{test.column}.unique",
                sql=(
                    "SELECT COUNT(*) AS failures FROM (\n"
                    f"  SELECT {column}\n"
                    f"  FROM {relation}\n"
                    f"  WHERE {column} IS NOT NULL\n"
                    f"  GROUP BY {column}\n"
                    "  HAVING COUNT(*) > 1\n"
                    ")"
                ),
            )
        )
    if test.accepted_values is not None:
        rendered = ", ".join(
            sqlglot.exp.convert(value).sql(dialect="bigquery") for value in test.accepted_values
        )
        assertions.append(
            _Assertion(
                name=f"{model_name}.{test.column}.accepted_values",
                sql=(
                    f"SELECT COUNTIF({column} IS NOT NULL AND {column} NOT IN ({rendered})) "
                    f"AS failures FROM {relation}"
                ),
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
        parent = project.relation_for_ref(test.relationships.to)
        parent_field = f"`{test.relationships.field}`"
        assertions.append(
            _Assertion(
                name=f"{model_name}.{test.column}.relationships",
                sql=(
                    "SELECT COUNT(*) AS failures\n"
                    f"FROM {relation} AS child\n"
                    f"LEFT JOIN {parent} AS parent\n"
                    f"  ON child.{column} = parent.{parent_field}\n"
                    f"WHERE child.{column} IS NOT NULL AND parent.{parent_field} IS NULL"
                ),
            )
        )
    return assertions
