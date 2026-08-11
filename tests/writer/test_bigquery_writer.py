"""BigQuery SCD1 writer tests for DANDER-20."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from google.api_core.exceptions import BadRequest, NotFound
from google.cloud import bigquery

from dander.concurrency import FencingToken
from dander.writer import (
    BigQueryIncrementalWriter,
    BigQueryReplaceWriter,
    BigQueryScd1Writer,
    BigQueryScd2Writer,
    BigQuerySnapshotWriter,
    BigQueryWriteError,
    SchemaEvolution,
    WriteField,
    WriteMode,
    WritePattern,
    WriteTarget,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


class _Job:
    def __init__(self, *, affected: int | None = None, error: Exception | None = None) -> None:
        self.num_dml_affected_rows = affected
        self._error = error

    def result(self) -> object:
        if self._error is not None:
            raise self._error
        return self


class _Client:
    def __init__(
        self,
        *,
        load_error: Exception | None = None,
        deployed_schema: Sequence[bigquery.SchemaField] | None = None,
        fenced_errors: Sequence[Exception] = (),
    ) -> None:
        self.load_error = load_error
        self.loaded_rows: list[dict[str, Any]] = []
        self.loaded_batches: list[list[dict[str, Any]]] = []
        self.destination = ""
        self.queries: list[str] = []
        self.query_configs: list[bigquery.QueryJobConfig | None] = []
        self.deleted: list[str] = []
        self.write_disposition: str | None = None
        self.write_dispositions: list[str] = []
        self.loaded_schemas: list[list[bigquery.SchemaField]] = []
        self.load_configs: list[bigquery.LoadJobConfig] = []
        self.created: list[str] = []
        self.created_expirations: dict[str, datetime | None] = {}
        self.updated: list[tuple[str, list[str]]] = []
        self.copies: list[tuple[str, str, str]] = []
        self.tables: dict[str, bigquery.Table] = {}
        self.fenced_errors = list(fenced_errors)
        if deployed_schema is not None:
            table = bigquery.Table(
                "unit-project.raw.example_widgets",
                schema=deployed_schema,
            )
            self.tables[str(table.reference)] = table

    def create_table(
        self,
        table: bigquery.Table,
        *,
        exists_ok: bool = False,
    ) -> bigquery.Table:
        table_id = str(table.reference)
        if table_id in self.tables:
            assert exists_ok
            return self.tables[table_id]
        self.tables[table_id] = table
        self.created.append(table_id)
        self.created_expirations[table_id] = table.expires
        return table

    def get_table(self, table: str) -> bigquery.Table:
        try:
            return self.tables[table]
        except KeyError as error:
            raise NotFound("synthetic missing table") from error  # type: ignore[no-untyped-call]

    def update_table(
        self,
        table: bigquery.Table,
        fields: Sequence[str],
    ) -> bigquery.Table:
        table_id = str(table.reference)
        self.tables[table_id] = table
        self.updated.append((table_id, list(fields)))
        return table

    def load_table_from_json(
        self,
        json_rows: Sequence[Mapping[str, Any]],
        destination: str,
        *,
        job_config: bigquery.LoadJobConfig,
    ) -> _Job:
        if job_config.schema:
            assert not job_config.autodetect
            self.loaded_schemas.append(list(job_config.schema))
            self.tables[destination] = bigquery.Table(destination, schema=job_config.schema)
        else:
            assert job_config.autodetect
        self.write_disposition = job_config.write_disposition
        self.write_dispositions.append(job_config.write_disposition)
        self.load_configs.append(job_config)
        self.loaded_rows = [dict(row) for row in json_rows]
        self.loaded_batches.append(self.loaded_rows)
        self.destination = destination
        return _Job(error=self.load_error)

    def copy_table(
        self,
        sources: str,
        destination: str,
        *,
        job_config: bigquery.CopyJobConfig,
    ) -> _Job:
        self.copies.append((sources, destination, job_config.write_disposition))
        source = self.tables.get(sources)
        if source is not None:
            self.tables[destination] = bigquery.Table(destination, schema=source.schema)
        return _Job()

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _Job:
        self.queries.append(query)
        self.query_configs.append(job_config)
        if query.startswith("BEGIN TRANSACTION") and self.fenced_errors:
            return _Job(error=self.fenced_errors.pop(0))
        return _Job(affected=2 if query.startswith("MERGE") else None)

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        assert not_found_ok
        self.deleted.append(table)
        self.tables.pop(table, None)


def _target() -> WriteTarget:
    return WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
    )


def _fenced_target() -> WriteTarget:
    return WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        fence=FencingToken(
            lease_table="unit-project.meta._dander_leases",
            pipeline_id="example_pipeline",
            run_id="run-one",
            token=11,
        ),
    )


def _assert_supported_load_config(config: bigquery.LoadJobConfig) -> None:
    assert "destinationExpirationTime" not in config.to_api_repr()["load"]


def _assert_staging_precreated_with_expiration(client: _Client) -> None:
    staging = next(table for table in client.created if "._dander_stage_" in table)
    expires = client.created_expirations[staging]
    assert expires is not None
    remaining = expires - datetime.now(UTC)
    assert timedelta(hours=23, minutes=59) < remaining <= timedelta(days=1)


def _assert_inferred_staging_expired_after_load(client: _Client) -> None:
    _assert_supported_load_config(client.load_configs[0])
    assert client.queries[0].startswith(f"ALTER TABLE `{client.destination}` SET OPTIONS")


def test_scd1_deduplicates_last_record_and_builds_explicit_merge() -> None:
    client = _Client()
    writer = BigQueryScd1Writer(project="unit-project", client=client)

    affected = writer.write(
        [
            {"id": "one", "label": "old"},
            {"id": "one", "label": "new"},
            {"id": "two", "label": "other"},
        ],
        _target(),
    )

    assert affected == 2
    assert client.loaded_rows == [
        {"id": "one", "label": "new"},
        {"id": "two", "label": "other"},
    ]
    _assert_inferred_staging_expired_after_load(client)
    assert client.queries[1].startswith(
        "CREATE TABLE IF NOT EXISTS `unit-project.raw.example_widgets`"
    )
    merge = client.queries[2]
    assert "ON target.`id` = source.`id`" in merge
    assert "target.`label` = source.`label`" in merge
    assert "SELECT *" not in merge
    assert client.deleted == [client.destination]


def test_scd1_encodes_typed_scalars_for_bigquery_json_load() -> None:
    client = _Client()
    writer = BigQueryScd1Writer(project="unit-project", client=client)
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="amount", data_type="NUMERIC"),
            WriteField(name="payload", data_type="BYTES"),
            WriteField(name="observed_at", data_type="TIMESTAMP"),
            WriteField(
                name="details",
                data_type="RECORD",
                fields=(
                    WriteField(name="amount", data_type="NUMERIC"),
                    WriteField(name="dates", data_type="DATE", mode="REPEATED"),
                    WriteField(name="cutoff", data_type="TIME"),
                ),
            ),
        ),
    )

    writer.write(
        [
            {
                "id": "one",
                "amount": Decimal("125.50"),
                "payload": b"\x00\xff",
                "observed_at": datetime(2026, 8, 3, 2, 30, tzinfo=UTC),
                "details": {
                    "amount": Decimal("0.000000001"),
                    "dates": [date(2026, 8, 2), date(2026, 8, 3)],
                    "cutoff": time(9, 15),
                },
            }
        ],
        target,
    )

    assert client.loaded_rows == [
        {
            "id": "one",
            "amount": "125.50",
            "payload": "AP8=",
            "observed_at": "2026-08-03T02:30:00+00:00",
            "details": {
                "amount": "0.000000001",
                "dates": ["2026-08-02", "2026-08-03"],
                "cutoff": "09:15:00",
            },
        }
    ]
    _assert_supported_load_config(client.load_configs[0])
    _assert_staging_precreated_with_expiration(client)


def test_scd1_finalizer_dml_touches_matching_lease_inside_transaction() -> None:
    client = _Client()
    writer = BigQueryScd1Writer(project="unit-project", client=client)
    fence = FencingToken(
        lease_table="unit-project.meta._dander_leases",
        pipeline_id="greenhouse_jobs",
        run_id="run-one",
        token=11,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        fence=fence,
    )

    writer.write([{"id": "one", "label": "active"}], target)

    script = client.queries[-1]
    assert script.startswith("BEGIN TRANSACTION;\nUPDATE `unit-project.meta._dander_leases`")
    assert "pipeline_id = @dander_pipeline_id" in script
    assert "run_id = @dander_run_id" in script
    assert "fencing_token = @dander_fencing_token" in script
    assert "ASSERT @@row_count = 1 AS 'Dander pipeline lease lost'" in script
    assert "MERGE `unit-project.raw.example_widgets`" in script
    assert script.index("UPDATE `unit-project.meta._dander_leases`") < script.index(
        "MERGE `unit-project.raw.example_widgets`"
    )
    assert "SELECT" not in script.split("ASSERT @@row_count", 1)[0]
    config = client.query_configs[-1]
    assert config is not None
    assert {parameter.name: parameter.value for parameter in config.query_parameters} == {
        "dander_pipeline_id": "greenhouse_jobs",
        "dander_run_id": "run-one",
        "dander_fencing_token": 11,
    }


def test_scd1_fenced_finalizer_retries_bigquery_concurrent_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(
        fenced_errors=[
            BadRequest(
                "Transaction is aborted due to concurrent update against table "
                "unit-project.meta._dander_leases"
            )  # type: ignore[no-untyped-call]
        ]
    )
    monkeypatch.setattr("dander._bigquery_retry.sleep", lambda _delay: None)
    writer = BigQueryScd1Writer(project="unit-project", client=client)

    writer.write([{"id": "one", "label": "active"}], _fenced_target())

    fenced_scripts = [query for query in client.queries if query.startswith("BEGIN TRANSACTION")]
    assert len(fenced_scripts) == 2
    assert fenced_scripts[0] == fenced_scripts[1]


def test_writer_rejects_inconsistent_shape_before_network() -> None:
    client = _Client()
    writer = BigQueryScd1Writer(project="unit-project", client=client)

    with pytest.raises(BigQueryWriteError, match="different column set"):
        writer.write([{"id": "one"}, {"id": "two", "label": "extra"}], _target())

    assert not client.loaded_rows


def test_staging_table_is_cleaned_after_load_failure() -> None:
    client = _Client(load_error=RuntimeError("synthetic load failure"))
    writer = BigQueryScd1Writer(project="unit-project", client=client)
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(WriteField(name="id", data_type="STRING", mode="REQUIRED"),),
    )

    with pytest.raises(RuntimeError, match="synthetic load failure"):
        writer.write([{"id": "one"}], target)

    _assert_supported_load_config(client.load_configs[0])
    _assert_staging_precreated_with_expiration(client)
    assert client.deleted == [client.destination]


def test_replace_writer_stages_then_atomically_replaces_target() -> None:
    client = _Client()
    writer = BigQueryReplaceWriter(project="unit-project", client=client)

    affected = writer.write([{"id": "one"}, {"id": "two"}], _target())

    assert affected == 2
    assert client.destination.startswith("unit-project.raw._dander_stage_example_widgets_")
    assert client.write_disposition == "WRITE_TRUNCATE"
    _assert_inferred_staging_expired_after_load(client)
    assert client.copies == [
        (
            client.destination,
            "unit-project.raw.example_widgets",
            "WRITE_TRUNCATE",
        )
    ]
    assert client.deleted == [client.destination]


def test_replace_writer_rejects_cloud_fence_instead_of_claiming_transactional_safety() -> None:
    client = _Client()
    writer = BigQueryReplaceWriter(project="unit-project", client=client)

    with pytest.raises(BigQueryWriteError, match="does not provide transactionally fenced"):
        writer.write([{"id": "one"}], _fenced_target())

    assert client.loaded_batches == []
    assert client.queries == []


def test_replace_writer_deletes_stale_table_for_empty_snapshot() -> None:
    client = _Client()
    writer = BigQueryReplaceWriter(project="unit-project", client=client)

    assert writer.write([], _target()) == 0

    assert client.deleted == ["unit-project.raw.example_widgets"]
    assert client.queries == []


def test_replace_writer_bootstraps_declared_schema_for_empty_snapshot() -> None:
    client = _Client()
    writer = BigQueryReplaceWriter(project="unit-project", client=client)
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        schema=(WriteField(name="id", data_type="INT64", mode="REQUIRED"),),
    )

    assert writer.write([], target) == 0

    assert len(client.created) == 1
    assert client.created[0].startswith("unit-project.raw._dander_stage_example_widgets_")
    expires = client.created_expirations[client.created[0]]
    assert expires is not None
    assert timedelta(hours=23, minutes=59) < expires - datetime.now(UTC)
    assert client.tables["unit-project.raw.example_widgets"].schema == [
        bigquery.SchemaField("id", "INT64", mode="REQUIRED")
    ]


def test_replace_writer_precreates_declared_staging_with_expiration() -> None:
    client = _Client()
    writer = BigQueryReplaceWriter(project="unit-project", client=client)
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        schema=(WriteField(name="id", data_type="INT64", mode="REQUIRED"),),
    )

    assert writer.write([{"id": 1}], target) == 1

    _assert_supported_load_config(client.load_configs[0])
    _assert_staging_precreated_with_expiration(client)
    assert client.queries == []


def test_replace_writer_bounds_load_requests_and_appends_after_first_chunk() -> None:
    client = _Client()
    writer = BigQueryReplaceWriter(
        project="unit-project",
        client=client,
        max_batch_rows=2,
    )

    affected = writer.write(
        [{"id": "one"}, {"id": "two"}, {"id": "three"}, {"id": "four"}, {"id": "five"}],
        _target(),
    )

    assert affected == 5
    assert [len(batch) for batch in client.loaded_batches] == [2, 2, 1]
    assert client.write_dispositions == ["WRITE_TRUNCATE", "WRITE_APPEND", "WRITE_APPEND"]
    _assert_inferred_staging_expired_after_load(client)
    for config in client.load_configs:
        _assert_supported_load_config(config)
    assert client.copies == [
        (
            client.destination,
            "unit-project.raw.example_widgets",
            "WRITE_TRUNCATE",
        )
    ]
    assert client.deleted == [client.destination]


def test_replace_writer_starts_loading_before_input_is_exhausted() -> None:
    observed_at_load: list[int] = []
    yielded = 0

    class _ObservingClient(_Client):
        def load_table_from_json(
            self,
            json_rows: Sequence[Mapping[str, Any]],
            destination: str,
            *,
            job_config: bigquery.LoadJobConfig,
        ) -> _Job:
            observed_at_load.append(yielded)
            return super().load_table_from_json(
                json_rows,
                destination,
                job_config=job_config,
            )

    def records() -> Iterable[Mapping[str, Any]]:
        nonlocal yielded
        for index in range(5):
            yielded += 1
            yield {"id": str(index)}

    client = _ObservingClient()
    writer = BigQueryReplaceWriter(
        project="unit-project",
        client=client,
        max_batch_rows=2,
    )

    assert writer.write(records(), _target()) == 5
    assert observed_at_load == [2, 4, 5]


def test_replace_writer_does_not_publish_partial_stage_after_source_failure() -> None:
    client = _Client()
    writer = BigQueryReplaceWriter(
        project="unit-project",
        client=client,
        max_batch_rows=2,
    )

    def records() -> Iterable[Mapping[str, Any]]:
        yield {"id": "one"}
        yield {"id": "two"}
        raise RuntimeError("synthetic extraction failure")

    with pytest.raises(RuntimeError, match="synthetic extraction failure"):
        writer.write(records(), _target())

    assert len(client.loaded_batches) == 1
    assert client.copies == []
    assert client.deleted == [client.destination]


def test_writer_rejects_invalid_batch_bound() -> None:
    with pytest.raises(BigQueryWriteError, match="positive integer"):
        BigQueryScd1Writer(project="unit-project", client=_Client(), max_batch_rows=0)


def test_additive_schema_evolution_adds_only_declared_scalar_columns() -> None:
    client = _Client(deployed_schema=[bigquery.SchemaField("id", "STRING")])
    writer = BigQueryScd1Writer(
        project="unit-project",
        client=client,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
    )

    writer.write([{"id": "one", "label": "new"}], target)

    assert client.updated == [("unit-project.raw.example_widgets", ["schema"])]
    assert [field.name for field in client.tables["unit-project.raw.example_widgets"].schema] == [
        "id",
        "label",
    ]
    assert client.queries[-1].startswith("MERGE")


def test_additive_schema_rejects_unsupported_type_before_load() -> None:
    client = _Client()
    writer = BigQueryScd1Writer(
        project="unit-project",
        client=client,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(WriteField(name="id", data_type="STRUCT<value STRING>"),),
    )

    with pytest.raises(BigQueryWriteError, match="Unsupported declared schema type"):
        writer.write([{"id": "one"}], target)

    assert client.loaded_batches == []


def test_declared_schema_rejects_batch_drift_before_target_mutation() -> None:
    client = _Client()
    writer = BigQueryScd1Writer(
        project="unit-project",
        client=client,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(WriteField(name="id", data_type="STRING"),),
    )

    with pytest.raises(BigQueryWriteError, match="Batch column 'label' is undeclared"):
        writer.write([{"id": "one", "label": "unexpected"}], target)

    assert client.created == []
    assert client.updated == []
    assert client.loaded_batches == []


def test_scd1_bootstraps_empty_target_from_nested_declaration() -> None:
    client = _Client()
    writer = BigQueryScd1Writer(
        project="unit-project",
        client=client,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="INT64", mode="REQUIRED"),
            WriteField(
                name="properties",
                data_type="RECORD",
                fields=(
                    WriteField(name="name", data_type="STRING"),
                    WriteField(name="tags", data_type="STRING", mode="REPEATED"),
                ),
            ),
        ),
    )

    assert writer.write([], target) == 0

    created = client.tables["unit-project.raw.example_widgets"]
    assert created.schema[0].mode == "REQUIRED"
    assert created.schema[1].field_type == "RECORD"
    assert created.schema[1].fields[1].mode == "REPEATED"
    assert client.loaded_batches == []


@pytest.mark.parametrize(
    ("deployed_schema", "match"),
    [
        ([bigquery.SchemaField("id", "STRING")], "type mismatch at id"),
        ([bigquery.SchemaField("id", "INT64")], "mode mismatch at id"),
        (
            [
                bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField(
                    "properties",
                    "RECORD",
                    fields=(bigquery.SchemaField("unexpected", "STRING"),),
                ),
            ],
            "Nested schema change is not supported at properties",
        ),
        (
            [
                bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField("legacy", "STRING"),
            ],
            "Undeclared deployed field: legacy",
        ),
    ],
)
def test_scd1_rejects_deployed_schema_drift_before_loading(
    deployed_schema: Sequence[bigquery.SchemaField],
    match: str,
) -> None:
    client = _Client(deployed_schema=deployed_schema)
    writer = BigQueryScd1Writer(
        project="unit-project",
        client=client,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="INT64", mode="REQUIRED"),
            WriteField(
                name="properties",
                data_type="RECORD",
                fields=(WriteField(name="name", data_type="STRING"),),
            ),
        ),
    )

    with pytest.raises(BigQueryWriteError, match=match):
        writer.write([], target)

    assert client.loaded_batches == []


def test_scd1_additive_rejects_missing_non_nullable_top_level_field() -> None:
    client = _Client(deployed_schema=[bigquery.SchemaField("id", "INT64", mode="REQUIRED")])
    writer = BigQueryScd1Writer(
        project="unit-project",
        client=client,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="INT64", mode="REQUIRED"),
            WriteField(name="tags", data_type="STRING", mode="REPEATED"),
        ),
    )

    with pytest.raises(BigQueryWriteError, match="top-level NULLABLE: tags"):
        writer.write([], target)

    assert client.updated == []


def test_scd1_additive_rejects_new_nested_field() -> None:
    client = _Client(
        deployed_schema=[
            bigquery.SchemaField("id", "INT64"),
            bigquery.SchemaField(
                "properties",
                "RECORD",
                fields=(bigquery.SchemaField("name", "STRING"),),
            ),
        ]
    )
    writer = BigQueryScd1Writer(
        project="unit-project",
        client=client,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="INT64"),
            WriteField(
                name="properties",
                data_type="RECORD",
                fields=(
                    WriteField(name="name", data_type="STRING"),
                    WriteField(name="active", data_type="BOOL"),
                ),
            ),
        ),
    )

    with pytest.raises(
        BigQueryWriteError,
        match="Nested schema change is not supported at properties",
    ):
        writer.write([], target)

    assert client.updated == []


def test_scd1_strict_rejects_missing_declared_top_level_field() -> None:
    client = _Client(deployed_schema=[bigquery.SchemaField("id", "INT64")])
    writer = BigQueryScd1Writer(
        project="unit-project",
        client=client,
        schema_evolution=SchemaEvolution.STRICT,
    )
    target = WriteTarget(
        project="unit-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="INT64"),
            WriteField(name="label", data_type="STRING"),
        ),
    )

    with pytest.raises(BigQueryWriteError, match="missing declared field: label"):
        writer.write([], target)

    assert client.updated == []


def test_incremental_writer_requires_cursor_and_reuses_idempotent_merge() -> None:
    client = _Client()
    writer = BigQueryIncrementalWriter(
        project="unit-project",
        cursor_field="updated_at",
        client=client,
    )

    affected = writer.write(
        [{"id": "one", "updated_at": "2026-07-29T12:00:00Z"}],
        _target(),
    )

    assert writer.mode is WriteMode.INCREMENTAL
    assert affected == 2
    assert client.queries[2].startswith("MERGE")

    with pytest.raises(BigQueryWriteError, match="Cursor column"):
        writer.write([{"id": "two"}], _target())
    with pytest.raises(BigQueryWriteError, match="null cursor"):
        writer.write([{"id": "two", "updated_at": None}], _target())


def test_snapshot_writer_partitions_and_suppresses_exact_reruns() -> None:
    client = _Client()
    writer = BigQuerySnapshotWriter(
        project="unit-project",
        snapshot_field="snapshot_at",
        client=client,
    )

    affected = writer.write(
        [
            {
                "id": "one",
                "snapshot_at": "2026-07-29T12:00:00Z",
                "label": "active",
            }
        ],
        _target(),
    )

    assert affected == 1
    _assert_inferred_staging_expired_after_load(client)
    assert "PARTITION BY DATE(`snapshot_at`)" in client.queries[1]
    insert = client.queries[2]
    assert insert.startswith("INSERT INTO")
    assert "WHERE NOT EXISTS" in insert
    assert "IS NOT DISTINCT FROM" in insert
    assert "PARTITION BY TO_JSON_STRING(source)" in insert
    assert "SELECT *" not in insert
    assert client.deleted == [client.destination]


def test_snapshot_insert_honors_cloud_fence() -> None:
    client = _Client()
    writer = BigQuerySnapshotWriter(
        project="unit-project",
        snapshot_field="snapshot_at",
        client=client,
    )

    writer.write(
        [{"id": "one", "snapshot_at": "2026-07-29T12:00:00Z"}],
        _fenced_target(),
    )

    script = client.queries[-1]
    assert script.startswith("BEGIN TRANSACTION;\nUPDATE `unit-project.meta._dander_leases`")
    assert "INSERT INTO `unit-project.raw.example_widgets`" in script


def test_snapshot_writer_rejects_missing_or_null_snapshot_value() -> None:
    writer = BigQuerySnapshotWriter(
        project="unit-project",
        snapshot_field="snapshot_at",
        client=_Client(),
    )

    with pytest.raises(BigQueryWriteError, match="absent"):
        writer.write([{"id": "one"}], _target())
    with pytest.raises(BigQueryWriteError, match="null snapshot"):
        writer.write([{"id": "one", "snapshot_at": None}], _target())


def test_scd2_writer_builds_transactional_change_history() -> None:
    client = _Client()
    writer = BigQueryScd2Writer(project="unit-project", client=client)

    affected = writer.write(
        [
            {"id": "one", "label": "old"},
            {"id": "one", "label": "new"},
            {"id": "two", "label": "other"},
        ],
        _target(),
    )

    assert affected == 2
    _assert_inferred_staging_expired_after_load(client)
    assert client.loaded_rows == [
        {"id": "one", "label": "new"},
        {"id": "two", "label": "other"},
    ]
    create = client.queries[1]
    assert "valid_from" in create
    assert "valid_to" in create
    assert "is_current" in create
    history = client.queries[2]
    assert "CREATE TEMP TABLE changed" in history
    assert "IS DISTINCT FROM" in history
    assert "BEGIN TRANSACTION" in history
    assert "SET `valid_to` = effective_at, `is_current` = FALSE" in history
    assert "COMMIT TRANSACTION" in history
    assert "SELECT *" not in history
    assert client.deleted == [client.destination]


def test_scd2_transaction_touches_matching_lease_before_history_mutation() -> None:
    client = _Client()
    writer = BigQueryScd2Writer(project="unit-project", client=client)

    writer.write([{"id": "one", "label": "new"}], _fenced_target())

    script = client.queries[-1]
    assert "BEGIN TRANSACTION;\nUPDATE `unit-project.meta._dander_leases`" in script
    assert script.index("UPDATE `unit-project.meta._dander_leases`") < script.index(
        "UPDATE `unit-project.raw.example_widgets` AS target"
    )
    assert "ASSERT @@row_count = 1 AS 'Dander pipeline lease lost'" in script


def test_scd2_writer_rejects_reserved_columns_and_missing_key() -> None:
    writer = BigQueryScd2Writer(project="unit-project", client=_Client())

    with pytest.raises(BigQueryWriteError, match="reserved column"):
        writer.write([{"id": "one", "valid_from": "user-data"}], _target())
    with pytest.raises(BigQueryWriteError, match="business-key"):
        writer.write(
            [{"id": "one"}],
            WriteTarget(project="unit-project", dataset="raw", table="snapshots"),
        )


@pytest.mark.parametrize(
    "writer",
    [
        BigQuerySnapshotWriter(
            project="unit-project",
            snapshot_field="snapshot_at",
            client=_Client(),
        ),
        BigQueryScd2Writer(project="unit-project", client=_Client()),
    ],
)
def test_new_writers_reject_project_mismatch(writer: WritePattern) -> None:
    target = WriteTarget(
        project="other-project",
        dataset="raw",
        table="example_widgets",
        business_key=("id",),
    )

    with pytest.raises(BigQueryWriteError, match="does not match"):
        writer.write(
            [{"id": "one", "snapshot_at": "2026-07-29T12:00:00Z"}],
            target,
        )
