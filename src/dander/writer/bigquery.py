"""Explicit, idempotent BigQuery write patterns."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import islice
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from dander._bigquery_retry import run_mutation_with_retry
from dander.concurrency import fenced_dml, fencing_job_config, fencing_touch_sql
from dander.identity import google_client_options
from dander.schema import BIGQUERY_FIELD_MODES, BIGQUERY_FIELD_TYPES, normalize_bigquery_type
from dander.writer.base import SchemaEvolution, WriteField, WriteMode, WritePattern, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from dander.concurrency import FencingToken

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STAGING_TTL = timedelta(days=1)


class BigQueryWriteError(ValueError):
    """Raised when a batch cannot satisfy its explicit write contract."""


class _Job(Protocol):
    num_dml_affected_rows: int | None

    def result(self) -> object:
        """Wait for job completion."""


class _BigQueryClient(Protocol):
    def create_table(self, table: bigquery.Table, *, exists_ok: bool = False) -> bigquery.Table:
        """Create a table from an explicit schema."""

    def get_table(self, table: str) -> bigquery.Table:
        """Read a deployed table schema."""

    def update_table(
        self,
        table: bigquery.Table,
        fields: Sequence[str],
    ) -> bigquery.Table:
        """Update selected table metadata fields."""

    def load_table_from_json(
        self,
        json_rows: Sequence[Mapping[str, Any]],
        destination: str,
        *,
        job_config: bigquery.LoadJobConfig,
    ) -> _Job:
        """Load JSON-compatible rows."""

    def copy_table(
        self,
        sources: str,
        destination: str,
        *,
        job_config: bigquery.CopyJobConfig,
    ) -> _Job:
        """Atomically copy one staging table over a target."""

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _Job:
        """Run a SQL query."""

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        """Delete a table."""


class BigQueryScd1Writer(WritePattern):
    """Load a batch through a unique staging table and merge on its business key."""

    mode = WriteMode.SCD1
    supports_batched_writes = True

    def __init__(
        self,
        *,
        project: str,
        client: _BigQueryClient | None = None,
        max_batch_rows: int = 10_000,
        schema_evolution: SchemaEvolution = SchemaEvolution.STRICT,
    ) -> None:
        self._project = _validated_identifier(project, "project", allow_dash=True)
        self._client = client or cast(
            "_BigQueryClient",
            bigquery.Client(project=project, **google_client_options()),
        )
        self._max_batch_rows = _validated_batch_size(max_batch_rows)
        self._schema_evolution = schema_evolution

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        """Write a consistently-shaped batch idempotently.

        Duplicate business keys within the incoming batch use last-record-wins semantics before
        loading. This prevents BigQuery `MERGE` from matching a target row more than once.
        """
        target_id = _target_id(target)
        if target.project != self._project:
            raise BigQueryWriteError(
                f"Writer project {self._project!r} does not match target project {target.project!r}"
            )
        if not target.business_key:
            raise BigQueryWriteError("SCD1 writes require at least one business-key column")

        rows = [dict(record) for record in records]
        declared = _validate_declared_schema(target, None)
        _require_declared_columns(declared, target.business_key, label="business-key")
        if not rows:
            _reconcile_declared_target(
                self._client,
                target_id,
                declared,
                self._schema_evolution,
            )
            return 0

        columns = tuple(rows[0])
        if not columns:
            raise BigQueryWriteError("Cannot write records with no columns")
        _require_batch_matches_declared(declared, columns)
        for column in columns:
            _validated_identifier(column, "column")
        for key in target.business_key:
            _validated_identifier(key, "business-key column")
            if key not in columns:
                raise BigQueryWriteError(f"Business-key column {key!r} is absent from the batch")

        expected_columns = set(columns)
        deduplicated: dict[tuple[object, ...], dict[str, Any]] = {}
        for index, row in enumerate(rows):
            if set(row) != expected_columns:
                raise BigQueryWriteError(
                    f"Record {index} has a different column set from the first record"
                )
            key_values = tuple(row[key] for key in target.business_key)
            if any(value is None for value in key_values):
                raise BigQueryWriteError(f"Record {index} has a null business-key value")
            try:
                deduplicated[key_values] = row
            except TypeError as error:
                raise BigQueryWriteError(
                    f"Record {index} has a non-scalar business-key value"
                ) from error

        staged_rows = list(deduplicated.values())
        _reconcile_declared_target(
            self._client,
            target_id,
            declared,
            self._schema_evolution,
        )
        staging_id = f"{target.project}.{target.dataset}._dander_stage_{target.table}_{uuid4().hex}"
        try:
            _load_rows_in_chunks(
                self._client,
                staged_rows,
                staging_id,
                max_batch_rows=self._max_batch_rows,
                expire=True,
                schema=declared,
            )
            self._client.query(_create_target_sql(target_id, staging_id, columns)).result()
            merge_sql = _merge_sql(target_id, staging_id, columns, target.business_key)
            if target.fence is not None:
                merge_script = fenced_dml(merge_sql, target.fence)
                merge_config = fencing_job_config(target.fence)
                merge_job = run_mutation_with_retry(
                    lambda: self._client.query(merge_script, job_config=merge_config)
                )
            else:
                merge_job = self._client.query(merge_sql)
                merge_job.result()
            return (
                merge_job.num_dml_affected_rows
                if merge_job.num_dml_affected_rows is not None
                else len(staged_rows)
            )
        finally:
            self._client.delete_table(staging_id, not_found_ok=True)


class BigQueryIncrementalWriter(BigQueryScd1Writer):
    """Merge a watermark-bounded batch after validating its cursor column."""

    mode = WriteMode.INCREMENTAL
    supports_batched_writes = False

    def __init__(
        self,
        *,
        project: str,
        cursor_field: str,
        client: _BigQueryClient | None = None,
        max_batch_rows: int = 10_000,
        schema_evolution: SchemaEvolution = SchemaEvolution.STRICT,
    ) -> None:
        super().__init__(
            project=project,
            client=client,
            max_batch_rows=max_batch_rows,
            schema_evolution=schema_evolution,
        )
        self._cursor_field = _validated_identifier(cursor_field, "cursor column")

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        """Validate the cursor boundary and reuse the idempotent SCD1 merge."""
        rows = [dict(record) for record in records]
        for index, row in enumerate(rows):
            if self._cursor_field not in row:
                raise BigQueryWriteError(
                    f"Cursor column {self._cursor_field!r} is absent from record {index}"
                )
            if row[self._cursor_field] is None:
                raise BigQueryWriteError(f"Record {index} has a null cursor value")
        return super().write(rows, target)


class BigQuerySnapshotWriter(WritePattern):
    """Append immutable snapshots while suppressing exact rerun duplicates."""

    mode = WriteMode.SNAPSHOT

    def __init__(
        self,
        *,
        project: str,
        snapshot_field: str,
        client: _BigQueryClient | None = None,
        max_batch_rows: int = 10_000,
        schema_evolution: SchemaEvolution = SchemaEvolution.STRICT,
    ) -> None:
        self._project = _validated_identifier(project, "project", allow_dash=True)
        self._snapshot_field = _validated_identifier(snapshot_field, "snapshot column")
        self._client = client or cast(
            "_BigQueryClient",
            bigquery.Client(project=project, **google_client_options()),
        )
        self._max_batch_rows = _validated_batch_size(max_batch_rows)
        self._schema_evolution = schema_evolution

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        """Stage one or more snapshots and insert rows not already stored exactly."""
        target_id = _target_id(target)
        _require_project(target, self._project)
        rows, columns = _validated_batch(records)
        if not rows:
            return 0
        if self._snapshot_field not in columns:
            raise BigQueryWriteError(
                f"Snapshot column {self._snapshot_field!r} is absent from the batch"
            )
        for index, row in enumerate(rows):
            if row[self._snapshot_field] is None:
                raise BigQueryWriteError(f"Record {index} has a null snapshot value")

        declared = _validate_declared_schema(target, self._schema_evolution)
        staging_id = _staging_id(target)
        try:
            self._load(rows, staging_id, schema=declared)
            self._client.query(
                _create_snapshot_target_sql(
                    target_id,
                    staging_id,
                    columns,
                    self._snapshot_field,
                )
            ).result()
            _apply_schema_evolution(
                self._client,
                target_id,
                target,
                self._schema_evolution,
            )
            insert_sql = _snapshot_insert_sql(target_id, staging_id, columns)
            if target.fence is not None:
                insert_script = fenced_dml(insert_sql, target.fence)
                insert_config = fencing_job_config(target.fence)
                insert_job = run_mutation_with_retry(
                    lambda: self._client.query(insert_script, job_config=insert_config)
                )
            else:
                insert_job = self._client.query(insert_sql)
                insert_job.result()
            return (
                insert_job.num_dml_affected_rows
                if insert_job.num_dml_affected_rows is not None
                else len(rows)
            )
        finally:
            self._client.delete_table(staging_id, not_found_ok=True)

    def _load(
        self,
        rows: Sequence[Mapping[str, Any]],
        staging_id: str,
        *,
        schema: Sequence[WriteField],
    ) -> None:
        _load_rows_in_chunks(
            self._client,
            rows,
            staging_id,
            max_batch_rows=self._max_batch_rows,
            expire=True,
            schema=schema,
        )


class BigQueryScd2Writer(WritePattern):
    """Version changed business-key rows with one current record per key."""

    mode = WriteMode.SCD2
    _SYSTEM_COLUMNS = ("valid_from", "valid_to", "is_current")

    def __init__(
        self,
        *,
        project: str,
        client: _BigQueryClient | None = None,
        max_batch_rows: int = 10_000,
        schema_evolution: SchemaEvolution = SchemaEvolution.STRICT,
    ) -> None:
        self._project = _validated_identifier(project, "project", allow_dash=True)
        self._client = client or cast(
            "_BigQueryClient",
            bigquery.Client(project=project, **google_client_options()),
        )
        self._max_batch_rows = _validated_batch_size(max_batch_rows)
        self._schema_evolution = schema_evolution

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        """Close changed current versions and insert their replacements transactionally."""
        target_id = _target_id(target)
        _require_project(target, self._project)
        if not target.business_key:
            raise BigQueryWriteError("SCD2 writes require at least one business-key column")

        rows, columns = _validated_batch(records)
        if not rows:
            return 0
        collisions = sorted(set(columns).intersection(self._SYSTEM_COLUMNS))
        if collisions:
            raise BigQueryWriteError(f"SCD2 input uses reserved column {collisions[0]!r}")
        staged_rows = _deduplicate_keyed(rows, columns, target.business_key)
        declared = _validate_declared_schema(target, self._schema_evolution)
        staging_id = _staging_id(target)

        try:
            _load_rows_in_chunks(
                self._client,
                staged_rows,
                staging_id,
                max_batch_rows=self._max_batch_rows,
                expire=True,
                schema=declared,
            )
            self._client.query(_create_scd2_target_sql(target_id, staging_id, columns)).result()
            _apply_schema_evolution(
                self._client,
                target_id,
                target,
                self._schema_evolution,
            )
            history_sql = _scd2_sql(
                target_id,
                staging_id,
                columns,
                target.business_key,
                fence=target.fence,
            )
            if target.fence is not None:
                history_config = fencing_job_config(target.fence)
                history_job = run_mutation_with_retry(
                    lambda: self._client.query(history_sql, job_config=history_config)
                )
            else:
                history_job = self._client.query(history_sql)
                history_job.result()
            return (
                history_job.num_dml_affected_rows
                if history_job.num_dml_affected_rows is not None
                else len(staged_rows)
            )
        finally:
            self._client.delete_table(staging_id, not_found_ok=True)


class BigQueryReplaceWriter(WritePattern):
    """Stage bounded load batches, then replace through one atomic BigQuery DDL statement."""

    mode = WriteMode.REPLACE
    accepts_streaming_input = True

    def __init__(
        self,
        *,
        project: str,
        client: _BigQueryClient | None = None,
        max_batch_rows: int = 10_000,
    ) -> None:
        self._project = _validated_identifier(project, "project", allow_dash=True)
        self._client = client or cast(
            "_BigQueryClient",
            bigquery.Client(project=project, **google_client_options()),
        )
        self._max_batch_rows = _validated_batch_size(max_batch_rows)

    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        """Stage a bounded input stream, then atomically replace the target."""
        target_id = _target_id(target)
        if target.fence is not None:
            raise BigQueryWriteError(
                "Replace mode does not provide transactionally fenced cloud publication"
            )
        if target.project != self._project:
            raise BigQueryWriteError(
                f"Writer project {self._project!r} does not match target project {target.project!r}"
            )

        record_iterator = iter(records)
        declared = _validate_declared_schema(target, None)
        first_batch = [dict(record) for record in islice(record_iterator, self._max_batch_rows)]
        if not first_batch:
            if not declared:
                self._client.delete_table(target_id, not_found_ok=True)
                return 0
            staging_id = _staging_id(target)
            try:
                _create_declared_target(
                    self._client,
                    staging_id,
                    declared,
                    expires=_staging_expiration(),
                )
                self._client.copy_table(
                    staging_id,
                    target_id,
                    job_config=_copy_config(),
                ).result()
            finally:
                self._client.delete_table(staging_id, not_found_ok=True)
            return 0

        columns = tuple(first_batch[0])
        if not columns:
            raise BigQueryWriteError("Cannot write records with no columns")
        _require_batch_matches_declared(declared, columns)
        for column in columns:
            _validated_identifier(column, "column")
        expected_columns = set(columns)
        staging_id = _staging_id(target)
        written = 0
        try:
            if declared:
                _create_declared_target(
                    self._client,
                    staging_id,
                    declared,
                    expires=_staging_expiration(),
                )
            batch = first_batch
            first = True
            while batch:
                for index, row in enumerate(batch, start=written):
                    if set(row) != expected_columns:
                        raise BigQueryWriteError(
                            f"Record {index} has a different column set from the first record"
                        )
                disposition = (
                    bigquery.WriteDisposition.WRITE_TRUNCATE
                    if first
                    else bigquery.WriteDisposition.WRITE_APPEND
                )
                self._client.load_table_from_json(
                    batch,
                    staging_id,
                    job_config=_load_config(disposition, declared),
                ).result()
                if first:
                    if not declared:
                        self._client.query(_expire_staging_sql(staging_id)).result()
                    first = False
                written += len(batch)
                batch = [dict(record) for record in islice(record_iterator, self._max_batch_rows)]
            self._client.copy_table(
                staging_id,
                target_id,
                job_config=_copy_config(),
            ).result()
            return written
        finally:
            self._client.delete_table(staging_id, not_found_ok=True)


def _validated_identifier(value: str, label: str, *, allow_dash: bool = False) -> str:
    pattern = r"^[A-Za-z_][A-Za-z0-9_-]*$" if allow_dash else _IDENTIFIER.pattern
    if not re.fullmatch(pattern, value):
        raise BigQueryWriteError(f"Invalid {label}: {value!r}")
    return value


def _validated_batch_size(value: int) -> int:
    if isinstance(value, bool) or value <= 0:
        raise BigQueryWriteError("max_batch_rows must be a positive integer")
    return value


def _apply_schema_evolution(
    client: _BigQueryClient,
    target_id: str,
    target: WriteTarget,
    mode: SchemaEvolution,
) -> None:
    """Retain additive behavior for non-CLI writer modes without nested evolution."""
    if mode is SchemaEvolution.STRICT:
        return
    fields = _validate_declared_schema(target, mode)
    if not fields:
        return
    statements: list[str] = []
    for field in fields:
        if field.mode != "NULLABLE":
            raise BigQueryWriteError("Additive schema fields must be top-level NULLABLE fields")
        statements.append(
            f"ALTER TABLE `{target_id}` ADD COLUMN IF NOT EXISTS "
            f"`{field.name}` {_field_type_sql(field)}"
        )
    client.query(";\n".join(statements)).result()


def _validate_declared_schema(
    target: WriteTarget,
    mode: SchemaEvolution | None,
) -> tuple[WriteField, ...]:
    if not target.schema:
        if mode is SchemaEvolution.ADDITIVE:
            raise BigQueryWriteError("Additive schema evolution requires a declared target schema")
        return ()
    return _validate_fields(target.schema, path="schema")


def _validate_fields(fields: Sequence[WriteField], *, path: str) -> tuple[WriteField, ...]:
    seen: set[str] = set()
    validated: list[WriteField] = []
    for field in fields:
        name = _validated_identifier(field.name, f"{path} column")
        if name in seen:
            raise BigQueryWriteError(f"Duplicate declared schema column: {name!r}")
        seen.add(name)
        data_type = normalize_bigquery_type(field.data_type)
        if data_type not in BIGQUERY_FIELD_TYPES:
            raise BigQueryWriteError(f"Unsupported declared schema type: {field.data_type!r}")
        mode = field.mode.upper()
        if mode not in BIGQUERY_FIELD_MODES:
            raise BigQueryWriteError(f"Unsupported declared schema mode: {field.mode!r}")
        children = _validate_fields(field.fields, path=f"{path}.{name}")
        if data_type == "RECORD" and not children:
            raise BigQueryWriteError(f"RECORD schema column {name!r} requires nested fields")
        if data_type != "RECORD" and children:
            raise BigQueryWriteError(
                f"Only RECORD schema columns can declare nested fields: {name!r}"
            )
        validated.append(WriteField(name=name, data_type=data_type, mode=mode, fields=children))
    return tuple(validated)


def _require_declared_columns(
    schema: Sequence[WriteField],
    columns: Sequence[str],
    *,
    label: str,
) -> None:
    if not schema:
        return
    declared = {field.name for field in schema}
    for column in columns:
        if column not in declared:
            raise BigQueryWriteError(f"Declared schema is missing {label} column {column!r}")


def _require_batch_matches_declared(
    schema: Sequence[WriteField],
    columns: Sequence[str],
) -> None:
    if not schema:
        return
    declared = {field.name for field in schema}
    incoming = set(columns)
    if unknown := sorted(incoming - declared):
        raise BigQueryWriteError(f"Batch column {unknown[0]!r} is undeclared")
    if missing := sorted(declared - incoming):
        raise BigQueryWriteError(f"Batch is missing declared column {missing[0]!r}")


def _reconcile_declared_target(
    client: _BigQueryClient,
    target_id: str,
    declared: Sequence[WriteField],
    mode: SchemaEvolution,
) -> None:
    if not declared:
        return
    try:
        deployed = client.get_table(target_id)
    except NotFound:
        _create_declared_target(client, target_id, declared)
        return

    deployed_by_name = {field.name: field for field in deployed.schema}
    declared_by_name = {field.name: field for field in declared}
    if extra := sorted(set(deployed_by_name) - set(declared_by_name)):
        raise BigQueryWriteError(f"Undeclared deployed field: {extra[0]}")

    additions: list[WriteField] = []
    for name, field in declared_by_name.items():
        actual = deployed_by_name.get(name)
        if actual is None:
            additions.append(field)
        else:
            _assert_deployed_field(field, actual, path=name)
    if not additions:
        return
    if mode is SchemaEvolution.STRICT:
        raise BigQueryWriteError(f"Deployed schema is missing declared field: {additions[0].name}")
    for field in additions:
        if field.mode != "NULLABLE":
            raise BigQueryWriteError(f"Additive field must be top-level NULLABLE: {field.name}")
    deployed.schema = [*deployed.schema, *_bigquery_schema(additions)]
    updated = client.update_table(deployed, ["schema"])
    _assert_deployed_schema(declared, updated.schema)


def _assert_deployed_field(
    declared: WriteField,
    deployed: bigquery.SchemaField,
    *,
    path: str,
) -> None:
    if normalize_bigquery_type(deployed.field_type) != declared.data_type:
        raise BigQueryWriteError(f"Deployed schema type mismatch at {path}")
    if deployed.mode.upper() != declared.mode:
        raise BigQueryWriteError(f"Deployed schema mode mismatch at {path}")
    deployed_children = {field.name: field for field in deployed.fields}
    declared_children = {field.name: field for field in declared.fields}
    if set(deployed_children) != set(declared_children):
        raise BigQueryWriteError(f"Nested schema change is not supported at {path}")
    for name, child in declared_children.items():
        _assert_deployed_field(child, deployed_children[name], path=f"{path}.{name}")


def _create_declared_target(
    client: _BigQueryClient,
    target_id: str,
    schema: Sequence[WriteField],
    *,
    expires: datetime | None = None,
) -> None:
    if not schema:
        return
    table = bigquery.Table(target_id, schema=_bigquery_schema(schema))
    table.expires = expires
    created = client.create_table(table, exists_ok=True)
    _assert_deployed_schema(schema, created.schema)


def _assert_deployed_schema(
    declared: Sequence[WriteField],
    deployed: Sequence[bigquery.SchemaField],
) -> None:
    deployed_by_name = {field.name: field for field in deployed}
    declared_by_name = {field.name: field for field in declared}
    if extra := sorted(set(deployed_by_name) - set(declared_by_name)):
        raise BigQueryWriteError(f"Undeclared deployed field: {extra[0]}")
    if missing := sorted(set(declared_by_name) - set(deployed_by_name)):
        raise BigQueryWriteError(f"Deployed schema is missing declared field: {missing[0]}")
    for name, field in declared_by_name.items():
        _assert_deployed_field(field, deployed_by_name[name], path=name)


def _bigquery_schema(fields: Sequence[WriteField]) -> list[bigquery.SchemaField]:
    return [
        bigquery.SchemaField(
            field.name,
            field.data_type,
            mode=field.mode,
            fields=_bigquery_schema(field.fields),
        )
        for field in fields
    ]


def _field_type_sql(field: WriteField) -> str:
    if field.data_type == "RECORD":
        nested = ", ".join(f"`{child.name}` {_field_type_sql(child)}" for child in field.fields)
        data_type = f"STRUCT<{nested}>"
    else:
        data_type = field.data_type
    return f"ARRAY<{data_type}>" if field.mode == "REPEATED" else data_type


def _target_id(target: WriteTarget) -> str:
    project = _validated_identifier(target.project, "project", allow_dash=True)
    dataset = _validated_identifier(target.dataset, "dataset")
    table = _validated_identifier(target.table, "table")
    return f"{project}.{dataset}.{table}"


def _staging_id(target: WriteTarget) -> str:
    return f"{target.project}.{target.dataset}._dander_stage_{target.table}_{uuid4().hex}"


def _require_project(target: WriteTarget, project: str) -> None:
    if target.project != project:
        raise BigQueryWriteError(
            f"Writer project {project!r} does not match target project {target.project!r}"
        )


def _validated_batch(
    records: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    rows = [dict(record) for record in records]
    if not rows:
        return [], ()
    columns = tuple(rows[0])
    if not columns:
        raise BigQueryWriteError("Cannot write records with no columns")
    for column in columns:
        _validated_identifier(column, "column")
    expected_columns = set(columns)
    for index, row in enumerate(rows):
        if set(row) != expected_columns:
            raise BigQueryWriteError(
                f"Record {index} has a different column set from the first record"
            )
    return rows, columns


def _deduplicate_keyed(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[str],
    business_key: Sequence[str],
) -> list[dict[str, Any]]:
    for key in business_key:
        _validated_identifier(key, "business-key column")
        if key not in columns:
            raise BigQueryWriteError(f"Business-key column {key!r} is absent from the batch")
    deduplicated: dict[tuple[object, ...], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key_values = tuple(row[key] for key in business_key)
        if any(value is None for value in key_values):
            raise BigQueryWriteError(f"Record {index} has a null business-key value")
        try:
            deduplicated[key_values] = row
        except TypeError as error:
            raise BigQueryWriteError(
                f"Record {index} has a non-scalar business-key value"
            ) from error
    return list(deduplicated.values())


def _load_config(
    write_disposition: str,
    schema: Sequence[WriteField] = (),
) -> bigquery.LoadJobConfig:
    config = bigquery.LoadJobConfig(
        autodetect=not schema,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=write_disposition,
    )
    if schema:
        config.schema = _bigquery_schema(schema)
    return config


def _copy_config() -> bigquery.CopyJobConfig:
    return bigquery.CopyJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )


def _load_rows_in_chunks(
    client: _BigQueryClient,
    rows: Sequence[Mapping[str, Any]],
    destination: str,
    *,
    max_batch_rows: int,
    expire: bool = False,
    schema: Sequence[WriteField] = (),
) -> None:
    """Bound each load request while preserving one logical truncate-then-append batch."""
    if expire and schema:
        _create_declared_target(
            client,
            destination,
            schema,
            expires=_staging_expiration(),
        )
    for offset in range(0, len(rows), max_batch_rows):
        disposition = (
            bigquery.WriteDisposition.WRITE_TRUNCATE
            if offset == 0
            else bigquery.WriteDisposition.WRITE_APPEND
        )
        client.load_table_from_json(
            [_json_load_row(row) for row in rows[offset : offset + max_batch_rows]],
            destination,
            job_config=_load_config(
                disposition,
                schema,
            ),
        ).result()
        if offset == 0 and expire and not schema:
            # Legacy schema-inferred writes cannot be precreated safely. Expire them
            # immediately after the first successful load without using unsupported
            # load-job properties.
            client.query(_expire_staging_sql(destination)).result()


def _json_load_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _json_load_value(value) for key, value in row.items()}


def _json_load_value(value: object) -> object:
    """Encode validated Python scalars for BigQuery's JSON load client."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _json_load_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_load_value(item) for item in value]
    return value


def _staging_expiration() -> datetime:
    return datetime.now(UTC) + _STAGING_TTL


def _expire_staging_sql(staging_id: str) -> str:
    return (
        f"ALTER TABLE `{staging_id}` SET OPTIONS "
        "(expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 1 DAY))"
    )


def _quoted_columns(columns: Sequence[str], *, alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(f"{prefix}`{column}`" for column in columns)


def _create_target_sql(target_id: str, staging_id: str, columns: Sequence[str]) -> str:
    selected = _quoted_columns(columns)
    return (
        f"CREATE TABLE IF NOT EXISTS `{target_id}` AS "
        f"SELECT {selected} FROM `{staging_id}` WHERE FALSE"
    )


def _create_snapshot_target_sql(
    target_id: str,
    staging_id: str,
    columns: Sequence[str],
    snapshot_field: str,
) -> str:
    selected = _quoted_columns(columns)
    return (
        f"CREATE TABLE IF NOT EXISTS `{target_id}`\n"
        f"PARTITION BY DATE(`{snapshot_field}`)\n"
        f"AS SELECT {selected} FROM `{staging_id}` WHERE FALSE"
    )


def _snapshot_insert_sql(
    target_id: str,
    staging_id: str,
    columns: Sequence[str],
) -> str:
    equality = " AND ".join(
        f"TO_JSON_STRING(target.`{column}`) IS NOT DISTINCT FROM TO_JSON_STRING(source.`{column}`)"
        for column in columns
    )
    selected = _quoted_columns(columns, alias="source")
    return (
        f"INSERT INTO `{target_id}` ({_quoted_columns(columns)})\n"
        f"SELECT {selected} FROM `{staging_id}` AS source\n"
        f"WHERE NOT EXISTS (\n"
        f"  SELECT 1 FROM `{target_id}` AS target WHERE {equality}\n"
        f")\n"
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY TO_JSON_STRING(source)) = 1"
    )


def _create_scd2_target_sql(
    target_id: str,
    staging_id: str,
    columns: Sequence[str],
) -> str:
    selected = _quoted_columns(columns)
    return (
        f"CREATE TABLE IF NOT EXISTS `{target_id}` AS\n"
        f"SELECT {selected}, CURRENT_TIMESTAMP() AS `valid_from`,\n"
        f"  CAST(NULL AS TIMESTAMP) AS `valid_to`, TRUE AS `is_current`\n"
        f"FROM `{staging_id}` WHERE FALSE"
    )


def _scd2_sql(
    target_id: str,
    staging_id: str,
    columns: Sequence[str],
    business_key: Sequence[str],
    *,
    fence: FencingToken | None = None,
) -> str:
    match = " AND ".join(f"target.`{key}` = source.`{key}`" for key in business_key)
    mutable = [column for column in columns if column not in business_key]
    changed = (
        " OR ".join(
            f"TO_JSON_STRING(target.`{column}`) IS DISTINCT FROM TO_JSON_STRING(source.`{column}`)"
            for column in mutable
        )
        or "FALSE"
    )
    missing = f"target.`{business_key[0]}` IS NULL"
    key_match_changed = " AND ".join(f"target.`{key}` = changed.`{key}`" for key in business_key)
    insert_columns = (*columns, "valid_from", "valid_to", "is_current")
    source_columns = _quoted_columns(columns, alias="changed")
    lease_touch = f"{fencing_touch_sql(fence)}\n" if fence is not None else ""
    return (
        "DECLARE effective_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP();\n"
        "CREATE TEMP TABLE changed AS\n"
        f"SELECT {_quoted_columns(columns, alias='source')} FROM `{staging_id}` AS source\n"
        f"LEFT JOIN `{target_id}` AS target ON {match} AND target.`is_current` = TRUE\n"
        f"WHERE {missing} OR {changed};\n"
        "BEGIN TRANSACTION;\n"
        f"{lease_touch}"
        f"UPDATE `{target_id}` AS target\n"
        "SET `valid_to` = effective_at, `is_current` = FALSE\n"
        "WHERE target.`is_current` = TRUE AND EXISTS (\n"
        f"  SELECT 1 FROM changed WHERE {key_match_changed}\n"
        ");\n"
        f"INSERT INTO `{target_id}` ({_quoted_columns(insert_columns)})\n"
        f"SELECT {source_columns}, effective_at, NULL, TRUE FROM changed;\n"
        "COMMIT TRANSACTION;"
    )


def _merge_sql(
    target_id: str,
    staging_id: str,
    columns: Sequence[str],
    business_key: Sequence[str],
) -> str:
    match = " AND ".join(f"target.`{key}` = source.`{key}`" for key in business_key)
    mutable_columns = [column for column in columns if column not in business_key]
    matched = ""
    if mutable_columns:
        assignments = ", ".join(
            f"target.`{column}` = source.`{column}`" for column in mutable_columns
        )
        matched = f"\nWHEN MATCHED THEN UPDATE SET {assignments}"
    insert_columns = _quoted_columns(columns)
    insert_values = _quoted_columns(columns, alias="source")
    return (
        f"MERGE `{target_id}` AS target\n"
        f"USING `{staging_id}` AS source\n"
        f"ON {match}"
        f"{matched}\n"
        f"WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({insert_values})"
    )
