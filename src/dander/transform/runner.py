"""BigQuery materialization and generic data-test execution."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Protocol, cast

import sqlglot

from dander.concurrency import OwnershipGuard, fenced_dml, fencing_job_config
from dander.identity import google_client_options
from dander.providers.bigquery.telemetry import BigQueryJobTelemetry
from dander.telemetry import TelemetryOperation
from dander.transform.assertions import GenericAssertion, plan_assertions
from dander.transform.model import Materialization, SqlDialect
from dander.transform.project import (
    TransformModel,
    TransformProject,
    TransformProjectError,
)
from dander.transform.result import TransformRunError, TransformRunResult

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from google.cloud import bigquery


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

    def __init__(
        self,
        *,
        project: str,
        raw_namespace: str = "raw",
        client: _BigQueryClient | None = None,
    ) -> None:
        self._project = project
        self._raw_namespace = raw_namespace
        if client is None:
            from google.cloud import bigquery

            client = cast(
                "_BigQueryClient",
                bigquery.Client(project=project, **google_client_options()),
            )
        self._client = client

    def build(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Materialize selected models in dependency order, then run their assertions."""
        project = TransformProject.load(
            models_dir,
            catalog=self._project,
            raw_namespace=self._raw_namespace,
        )
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
        telemetry = BigQueryJobTelemetry()
        for statement in statements:
            if ownership is not None:
                ownership.verify()
            if statement.dml_finalizer and ownership is not None and ownership.fence is not None:
                script = fenced_dml(statement.sql, ownership.fence)
                config = fencing_job_config(ownership.fence)
                telemetry.run(
                    partial(self._client.query, script, job_config=config),
                    operation=TelemetryOperation.TRANSFORM,
                    retry_mutation=True,
                )
            else:
                telemetry.run(
                    partial(self._client.query, statement.sql),
                    operation=TelemetryOperation.TRANSFORM,
                )
        self._run_assertions(assertions, telemetry, ownership=ownership)
        return TransformRunResult(
            models=tuple(model.name for model in models),
            assertions=len(assertions),
            telemetry=telemetry.drain(),
        )

    def test(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
    ) -> TransformRunResult:
        """Run assertions against already-materialized selected model relations."""
        project = TransformProject.load(
            models_dir,
            catalog=self._project,
            raw_namespace=self._raw_namespace,
        )
        models = project.ordered(selected)
        assertions = [
            assertion for model in models for assertion in _compile_assertions(project, model)
        ]
        telemetry = BigQueryJobTelemetry()
        self._run_assertions(assertions, telemetry)
        return TransformRunResult(
            models=tuple(model.name for model in models),
            assertions=len(assertions),
            telemetry=telemetry.drain(),
        )

    def _run_assertions(
        self,
        assertions: Iterable[_Assertion],
        telemetry: BigQueryJobTelemetry,
        *,
        ownership: OwnershipGuard | None = None,
    ) -> None:
        failures: list[str] = []
        for assertion in assertions:
            if ownership is not None:
                ownership.verify()
            rows = list(
                telemetry.run_result(
                    partial(self._client.query, assertion.sql), operation=TelemetryOperation.TEST
                )
            )
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


def _compile_assertions(project: TransformProject, model: TransformModel) -> tuple[_Assertion, ...]:
    relation = project.relation_for_model(model)
    return tuple(
        _render_assertion(assertion, relation) for assertion in plan_assertions(project, model)
    )


def _render_assertion(assertion: GenericAssertion, relation: str) -> _Assertion:
    column = f"`{assertion.column}`"
    match assertion.kind:
        case "not_null":
            return _Assertion(
                name=assertion.name,
                sql=f"SELECT COUNTIF({column} IS NULL) AS failures FROM {relation}",
            )
        case "unique":
            return _Assertion(
                name=assertion.name,
                sql=(
                    f"SELECT COUNT(*) AS failures FROM (\n  SELECT {column}\n  FROM "
                    f"{relation}\n  WHERE {column} IS NOT NULL\n  GROUP BY {column}\n  "
                    f"HAVING COUNT(*) > 1\n)"
                ),
            )
        case "accepted_values":
            rendered = ", ".join(
                sqlglot.exp.convert(value).sql(dialect="bigquery") for value in assertion.values
            )
            return _Assertion(
                name=assertion.name,
                sql=(
                    f"SELECT COUNTIF({column} IS NOT NULL AND {column} NOT IN "
                    f"({rendered})) AS failures FROM {relation}"
                ),
            )
        case "relationships":
            assert assertion.parent is not None and assertion.parent_field is not None
            parent = f"`{'.'.join(assertion.parent.coordinates)}`"
            parent_field = f"`{assertion.parent_field}`"
            return _Assertion(
                name=assertion.name,
                sql=(
                    f"SELECT COUNT(*) AS failures\nFROM {relation} AS child\nLEFT JOIN "
                    f"{parent} AS parent\n  ON child.{column} = "
                    f"parent.{parent_field}\nWHERE child.{column} IS NOT NULL AND "
                    f"parent.{parent_field} IS NULL"
                ),
            )
