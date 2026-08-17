"""Snowflake registration and stateful staged-SCD1 conformance."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

import pytest

from dander.concurrency import FencingToken, TargetFenceLostError
from dander.ingestion import Endpoint, RawField, SourceConfig
from dander.pipeline.graph import PipelineGraph
from dander.pipeline.runtime import GraphExecutionPlan, GraphRuntimeError, plan_graph_execution
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry
from dander.providers.snowflake import SnowflakeWarehouseConfig
from dander.providers.snowflake.session import enrich_operation_telemetry
from dander.providers.snowflake.transform import SnowflakeGraphRunner, SnowflakeTransformRunner
from dander.providers.snowflake.writer import SnowflakeWriteError
from dander.telemetry import OperationTelemetry, TelemetryOperation
from dander.transform import SqlDialect, TransformProject, TransformProjectError, TransformRunError
from dander.warehouse import (
    ProviderExtension,
    RelationRef,
    WarehouseRuntime,
    WarehouseSchemaSupportError,
)
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class _FakeSnowflake:
    """Minimal state shared by independently opened fake connector sessions."""

    statements: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    committed_checksums: set[str] = field(default_factory=set)
    describe_rows: list[object] = field(
        default_factory=lambda: [
            ("id", "VARCHAR(16777216)", "COLUMN", "N"),
            ("label", "VARCHAR(16777216)", "COLUMN", "Y"),
        ]
    )
    claim_rowcount: int = 1
    fence_touch_rowcount: int = 1
    merge_rowcount: int = 2
    fail_prefix: str | None = None
    commits: int = 0
    rollbacks: int = 0
    closes: int = 0
    committed_claims: int = 0
    discarded_claims: int = 0
    transform_source_rows: list[dict[str, object]] | None = None
    transform_rows: dict[str, dict[str, object]] = field(default_factory=dict)
    assertion_failure_count: int = 0
    query_history_template: tuple[object, ...] | None = None

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self)


class _FakeConnection:
    def __init__(self, backend: _FakeSnowflake) -> None:
        self.backend = backend
        self.pending_claim = False
        self.temporary_rows: list[dict[str, object]] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.backend.commits += 1
        if self.pending_claim:
            self.backend.committed_claims += 1
            self.pending_claim = False

    def rollback(self) -> None:
        self.backend.rollbacks += 1
        self.pending_claim = False

    def close(self) -> None:
        if self.pending_claim:
            self.backend.discarded_claims += 1
            self.pending_claim = False
        self.backend.closes += 1


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.backend = connection.backend
        self.rowcount = 0
        self.sfqid: str | None = None
        self._row: object | None = None
        self._rows: list[object] = []

    def execute(self, command: str, params: Sequence[object] | None = None) -> Self:
        parameters = tuple(params or ())
        compact = " ".join(command.split())
        self.backend.statements.append((compact, parameters))
        self.sfqid = f"query-{len(self.backend.statements)}"
        if self.backend.fail_prefix and compact.startswith(self.backend.fail_prefix):
            raise RuntimeError("private provider response")
        if compact.startswith("SELECT QUERY_ID, WAREHOUSE_SIZE, BYTES_SCANNED"):
            if self.backend.query_history_template is not None:
                self._rows = [
                    (str(query_id), *self.backend.query_history_template) for query_id in parameters
                ]
                self.rowcount = len(self._rows)
        elif compact == "SELECT CURRENT_DATABASE(), CURRENT_WAREHOUSE()":
            self._row = ("DANDER_TEST", "DANDER_WH")
        elif compact.startswith("DESCRIBE TABLE"):
            self._rows = list(self.backend.describe_rows)
        elif compact.startswith("MERGE INTO") and "dander_target_commits" in compact:
            self.rowcount = self.backend.claim_rowcount
            self.connection.pending_claim = self.rowcount > 0
        elif compact.startswith("UPDATE") and "dander_target_commits" in compact:
            self.rowcount = self.backend.fence_touch_rowcount
        elif compact.startswith("SELECT 1 FROM") and "dander_stage_loads" in compact:
            checksum = str(parameters[3])
            self._row = (1,) if checksum in self.backend.committed_checksums else None
        elif compact.startswith("INSERT INTO") and "dander_stage_loads" in compact:
            checksum = str(parameters[3])
            assert _CHECKSUM.fullmatch(checksum)
            assert len(parameters) == 10
            self.backend.committed_checksums.add(checksum)
            self.rowcount = 1
        elif compact.startswith("CREATE OR REPLACE TEMPORARY TABLE"):
            rows = self.backend.transform_source_rows
            if rows is not None:
                self.connection.temporary_rows = [dict(row) for row in rows]
                if "ROW_NUMBER() OVER" in compact:
                    selected: dict[str, dict[str, object]] = {}
                    for row in sorted(
                        self.connection.temporary_rows,
                        key=lambda item: (
                            _integer(item, "updated_at"),
                            str(item.get("label", "")),
                        ),
                        reverse=True,
                    ):
                        selected.setdefault(str(row["id"]), row)
                    self.connection.temporary_rows = list(selected.values())
        elif compact.startswith("DELETE FROM") and self.backend.transform_source_rows is not None:
            self.backend.transform_rows.clear()
        elif compact.startswith("INSERT INTO") and self.backend.transform_source_rows is not None:
            self.backend.transform_rows = {
                str(row["id"]): dict(row) for row in self.connection.temporary_rows
            }
        elif compact.startswith("MERGE INTO"):
            self.rowcount = self.backend.merge_rowcount
            if self.backend.transform_source_rows is not None:
                assert 'incoming."updated_at" >= target."updated_at"' in compact
                for row in self.connection.temporary_rows:
                    key = str(row["id"])
                    current = self.backend.transform_rows.get(key)
                    if current is None or _integer(row, "updated_at") >= _integer(
                        current, "updated_at"
                    ):
                        self.backend.transform_rows[key] = dict(row)
        elif compact.startswith("SELECT COUNT"):
            self._row = (self.backend.assertion_failure_count,)
        return self

    def executemany(
        self,
        command: str,
        seq_of_parameters: Sequence[Sequence[object]],
    ) -> Self:
        parameters = tuple(tuple(row) for row in seq_of_parameters)
        compact = " ".join(command.split())
        self.backend.statements.append((compact, parameters))
        self.sfqid = f"query-{len(self.backend.statements)}"
        if self.backend.fail_prefix and compact.startswith(self.backend.fail_prefix):
            raise RuntimeError("private provider response")
        self.rowcount = len(parameters)
        return self

    def fetchone(self) -> object | None:
        return self._row

    def fetchall(self) -> list[object]:
        return list(self._rows)

    def close(self) -> None:
        return None


def _config() -> dict[str, object]:
    return {
        "provider": "snowflake",
        "account": "org-account",
        "user": "dander_user",
        "database": "DANDER_TEST",
        "schema": "raw",
        "warehouse": "DANDER_WH",
        "role": "DANDER_ROLE",
        "auth": {
            "method": "key_pair",
            "private_key_file_env": "DANDER_TEST_SNOWFLAKE_KEY_FILE",
        },
        "max_rows_per_file": 2,
        "max_logical_bytes_per_file": 1_048_576,
    }


def _integer(row: dict[str, object], field_name: str) -> int:
    value = row[field_name]
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


@pytest.fixture
def snowflake_runtime(
    tmp_path: Path,
) -> tuple[WarehouseRuntime, _FakeSnowflake, Path]:
    backend = _FakeSnowflake()
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.WAREHOUSE, _config())
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={
            "catalog": "DANDER_TEST",
            "connection_factory": backend.connect,
            "staging_root": tmp_path,
        },
    )
    assert isinstance(runtime, WarehouseRuntime)
    assert runtime.ingestion_schema_mapper is runtime.schema_mapper
    return runtime, backend, tmp_path


def _direct_runtime(
    tmp_path: Path,
    *,
    max_rows: int = 2,
    max_logical_bytes: int = 4_096,
) -> tuple[WarehouseRuntime, _FakeSnowflake]:
    backend = _FakeSnowflake()
    registry = default_provider_registry()
    raw = _config()
    raw["direct_max_rows"] = max_rows
    raw["direct_max_logical_bytes"] = max_logical_bytes
    config = registry.parse(ProviderKind.WAREHOUSE, raw)
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={
            "catalog": "DANDER_TEST",
            "connection_factory": backend.connect,
            "staging_root": tmp_path,
        },
    )
    assert isinstance(runtime, WarehouseRuntime)
    return runtime, backend


def test_snowflake_registration_is_lazy_and_credentials_remain_references() -> None:
    module_name = "dander.providers.snowflake.runtime"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()

    config = registry.parse(ProviderKind.WAREHOUSE, _config())

    assert module_name not in sys.modules
    assert isinstance(config, SnowflakeWarehouseConfig)
    dumped = config.model_dump(mode="json")
    assert dumped["schema"] == "raw"
    assert "schema_name" not in dumped
    assert dumped["auth"] == {
        "method": "key_pair",
        "private_key_file_env": "DANDER_TEST_SNOWFLAKE_KEY_FILE",
        "private_key_password_env": None,
    }

    relation = config.raw_relation(
        "records",
        compatibility_catalog="ignored-gcp-project",
        compatibility_namespace=None,
    )
    assert relation == RelationRef(catalog="DANDER_TEST", namespace="raw", name="records")


def test_snowflake_direct_thresholds_are_explicit_and_paired() -> None:
    raw = _config()
    raw["direct_max_rows"] = 10

    with pytest.raises(ValueError, match="must both be zero or positive"):
        SnowflakeWarehouseConfig.model_validate(raw)


def test_snowflake_schema_mapper_accepts_only_explicit_json_variant_without_io(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    before = list(backend.statements)
    variant = ProviderExtension(provider="snowflake", name="fallback", value="variant")

    schema = runtime.schema_mapper.canonical_schema(
        (WriteField(name="payload", data_type="JSON", extensions=(variant,)),)
    )

    assert variant in schema.fields[0].extensions
    assert backend.statements == before
    with pytest.raises(WarehouseSchemaSupportError, match="require snowflake/fallback=variant"):
        runtime.schema_mapper.canonical_schema((WriteField(name="payload", data_type="JSON"),))
    assert backend.statements == before


def test_snowflake_runtime_requires_projected_oauth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DANDER_MISSING_SNOWFLAKE_TOKEN", raising=False)
    registry = default_provider_registry()
    raw = _config()
    raw["auth"] = {"method": "oauth", "token_env": "DANDER_MISSING_SNOWFLAKE_TOKEN"}
    config = registry.parse(ProviderKind.WAREHOUSE, raw)

    with pytest.raises(ProviderFactoryError, match="requires a token"):
        registry.build(
            ProviderKind.WAREHOUSE,
            config,
            context={"catalog": "DANDER_TEST"},
        )


def test_snowflake_claim_commits_when_account_autocommit_is_disabled(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="records")

    runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_records",
            run_id="run-claim",
            token=3,
            authority_id="postgresql:test-state",
        ),
    )

    assert backend.committed_claims == 1
    assert backend.discarded_claims == 0
    assert backend.commits == 1
    claim_sql = next(
        statement for statement, _parameters in backend.statements if statement.startswith("MERGE")
    )
    assert " AS target_row USING " in claim_sql
    assert " AS current " not in claim_sql and "current." not in claim_sql


def test_snowflake_stages_bounded_parts_merges_last_record_and_cleans(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, staging_root = snowflake_runtime
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=3,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    lease = FencingToken(
        lease_table=None,
        pipeline_id="snowflake_records",
        run_id="run-one",
        token=4,
        authority_id="postgresql:test-state",
    )
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="records")
    publication = runtime.target_fence.claim(relation, lease)
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=publication,
    )
    consumed = 0

    def records() -> Iterator[dict[str, object]]:
        nonlocal consumed
        fixture: tuple[dict[str, object], ...] = (
            {"id": "one", "label": "old"},
            {"id": "one", "label": "new"},
            {"id": "two", "label": "second"},
        )
        for record in fixture:
            consumed += 1
            yield record

    assert writer.write(records(), target) == 2
    assert consumed == 3
    sql = [statement for statement, _parameters in backend.statements]
    assert len([statement for statement in sql if statement.startswith("PUT ")]) == 2
    assert len([statement for statement in sql if statement.startswith("COPY INTO")]) == 2
    assert any(statement.startswith("CREATE OR REPLACE TEMPORARY TABLE") for statement in sql)
    copy = next(statement for statement in sql if statement.startswith("COPY INTO"))
    assert "USE_LOGICAL_TYPE = TRUE" in copy
    assert "BINARY_AS_TEXT = FALSE" in copy
    merge = next(
        statement
        for statement in sql
        if statement.startswith("MERGE INTO") and "dander_target_commits" not in statement
    )
    assert 'ROW_NUMBER() OVER (PARTITION BY "id" ORDER BY "_dander_ordinal" DESC)' in merge
    assert 'WHEN MATCHED THEN UPDATE SET target."label" = incoming."label"' in merge
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in sql)
    assert any(statement.startswith("DROP STAGE IF EXISTS") for statement in sql)
    assert not tuple(staging_root.iterdir())
    assert backend.commits == 2
    assert backend.rollbacks == 0
    assert len(backend.committed_checksums) == 2
    assert runtime.capabilities.write_modes == frozenset(WriteMode)
    assert runtime.capabilities.transports == frozenset(
        {WriteTransport.COPY, WriteTransport.DIRECT}
    )
    assert runtime.capabilities.supports_transforms is True
    assert runtime.relation_codec.render(relation) == '"DANDER_TEST"."raw"."records"'


def test_snowflake_direct_load_parses_explicit_variant_and_emits_transport(
    tmp_path: Path,
) -> None:
    runtime, backend = _direct_runtime(tmp_path)
    backend.query_history_template = ("X-SMALL", 2_048, 11, 2, 3, 4, 7)
    backend.describe_rows = [
        ("id", "VARCHAR(16777216)", "COLUMN", "N"),
        ("payload", "VARIANT", "COLUMN", "Y"),
    ]
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="direct_records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_direct_records",
            run_id="run-direct",
            token=4,
            authority_id="postgresql:test-state",
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    variant = ProviderExtension(provider="snowflake", name="fallback", value="variant")
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="payload", data_type="JSON", extensions=(variant,)),
        ),
        publication_fence=publication,
    )

    assert writer.write(({"id": "one", "payload": {"ready": True}},), target) == 2

    sql = [statement for statement, _parameters in backend.statements]
    direct = next(
        (statement, parameters)
        for statement, parameters in backend.statements
        if statement.startswith('INSERT INTO "DANDER_TEST"."raw"."dander_stage_')
        and "dander_stage_loads" not in statement
    )
    use_schema_index = sql.index('USE SCHEMA "DANDER_TEST"."raw"')
    direct_insert_index = sql.index(direct[0])
    assert use_schema_index < direct_insert_index
    assert '"payload" VARCHAR' in next(
        statement for statement in sql if statement.startswith("CREATE OR REPLACE TEMPORARY TABLE")
    )
    assert direct[1] == (("one", '{"ready":true}', 0),)
    assert not any(statement.startswith(("PUT ", "COPY INTO")) for statement in sql)
    publication_sql = next(
        statement
        for statement in sql
        if statement.startswith("MERGE INTO") and "dander_target_commits" not in statement
    )
    assert 'PARSE_JSON(staged."payload") AS "payload"' in publication_sql
    operations = writer.drain_telemetry()
    assert [operation.operation for operation in operations] == [
        TelemetryOperation.LOAD,
        TelemetryOperation.QUERY,
    ]
    assert all(operation.transport is WriteTransport.DIRECT for operation in operations)
    assert all(operation.resource_name == "DANDER_WH" for operation in operations)
    assert all(operation.resource_size == "X-SMALL" for operation in operations)
    assert all(operation.bytes_processed == 2_048 for operation in operations)
    assert all(operation.execution_duration_ms == 11 for operation in operations)
    assert all(operation.queue_duration_ms == 9 for operation in operations)
    assert operations[0].rows_written == 1
    assert operations[1].rows_written == 7
    assert operations[0].query_id != operations[1].query_id
    history_sql, history_parameters = next(
        (statement, parameters)
        for statement, parameters in backend.statements
        if statement.startswith("SELECT QUERY_ID, WAREHOUSE_SIZE, BYTES_SCANNED")
    )
    assert "QUERY_TEXT" not in history_sql
    assert "BIND_VALUES" not in history_sql
    assert len(history_parameters) == 2
    assert writer.drain_telemetry() == ()
    direct_inserts = sum(
        statement.startswith('INSERT INTO "DANDER_TEST"."raw"."dander_stage_')
        and "dander_stage_loads" not in statement
        for statement, _parameters in backend.statements
    )
    assert writer.write(({"id": "one", "payload": {"ready": True}},), target) == 0
    assert (
        sum(
            statement.startswith('INSERT INTO "DANDER_TEST"."raw"."dander_stage_')
            and "dander_stage_loads" not in statement
            for statement, _parameters in backend.statements
        )
        == direct_inserts
    )
    replay_operations = writer.drain_telemetry()
    assert [operation.operation for operation in replay_operations] == [TelemetryOperation.QUERY]
    assert replay_operations[0].transport is WriteTransport.DIRECT
    assert not tuple(tmp_path.iterdir())


def test_snowflake_query_history_is_best_effort_and_bounded(tmp_path: Path) -> None:
    runtime, backend = _direct_runtime(tmp_path)
    backend.fail_prefix = "SELECT QUERY_ID, WAREHOUSE_SIZE, BYTES_SCANNED"
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="history_records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_history_records",
            run_id="run-history",
            token=6,
            authority_id="postgresql:test-state",
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=publication,
    )

    assert writer.write(({"id": "one", "label": "first"},), target) == 2
    assert all(operation.bytes_processed == 0 for operation in writer.drain_telemetry())

    backend.fail_prefix = None
    operations = tuple(
        OperationTelemetry(
            provider="snowflake",
            operation=TelemetryOperation.QUERY,
            query_id=f"query-history-{index}",
        )
        for index in range(1_002)
    )
    connection = backend.connect()
    assert enrich_operation_telemetry(connection, operations) == operations
    _history_sql, parameters = backend.statements[-1]
    assert len(parameters) == 1_000
    assert parameters[0] == "query-history-2"
    assert parameters[-1] == "query-history-1001"
    connection.close()


def test_snowflake_direct_threshold_is_decided_for_the_complete_endpoint(
    tmp_path: Path,
) -> None:
    runtime, backend = _direct_runtime(tmp_path, max_rows=2)
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="threshold_records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_threshold_records",
            run_id="run-threshold",
            token=5,
            authority_id="postgresql:test-state",
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=publication,
    )
    consumed = 0

    def records() -> Iterator[dict[str, object]]:
        nonlocal consumed
        for index in range(3):
            consumed += 1
            yield {"id": str(index), "label": f"record-{index}"}

    writer.write(records(), target)

    assert consumed == 3
    sql = [statement for statement, _parameters in backend.statements]
    assert any(statement.startswith("COPY INTO") for statement in sql)
    assert not any(
        statement.startswith('INSERT INTO "DANDER_TEST"."raw"."dander_stage_')
        and "dander_stage_loads" not in statement
        for statement in sql
    )
    assert all(operation.transport is WriteTransport.COPY for operation in writer.drain_telemetry())
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("mode", "field_name", "expected_sql"),
    [
        (
            WriteMode.INCREMENTAL,
            "updated_at",
            'WHEN MATCHED AND incoming."updated_at" >= target."updated_at"',
        ),
        (WriteMode.SNAPSHOT, "snapshot_at", "SELECT DISTINCT"),
        (WriteMode.SCD2, None, '"valid_to" = $DANDER_SCD2_EFFECTIVE_AT'),
        (WriteMode.REPLACE, None, 'SELECT "id", "label" FROM'),
    ],
)
def test_snowflake_factory_reaches_every_fenced_write_mode(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
    mode: WriteMode,
    field_name: str | None,
    expected_sql: str,
) -> None:
    runtime, backend, staging_root = snowflake_runtime
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=mode,
        cursor_field=field_name if mode is WriteMode.INCREMENTAL else None,
        snapshot_field=field_name if mode is WriteMode.SNAPSHOT else None,
    )
    assert writer.mode is mode
    assert writer.supports_batched_writes is False
    assert writer.accepts_streaming_input is True

    relation = RelationRef(
        catalog="DANDER_TEST",
        namespace="raw",
        name=f"records_{mode.value}",
    )
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=f"snowflake_{mode.value}",
            run_id=f"run-{mode.value}",
            token=5,
            authority_id="postgresql:test-state",
        ),
    )
    schema = [
        WriteField(name="id", data_type="STRING"),
        WriteField(name="label", data_type="STRING"),
    ]
    backend.describe_rows = [
        (
            "id",
            "VARCHAR(16777216)",
            "COLUMN",
            "N" if mode in {WriteMode.INCREMENTAL, WriteMode.SCD2} else "Y",
        ),
        ("label", "VARCHAR(16777216)", "COLUMN", "Y"),
    ]
    if field_name is not None:
        schema.append(WriteField(name=field_name, data_type="STRING"))
        backend.describe_rows.append((field_name, "VARCHAR(16777216)", "COLUMN", "Y"))
    if mode is WriteMode.SCD2:
        backend.describe_rows.extend(
            [
                ("valid_from", "TIMESTAMP_TZ(9)", "COLUMN", "N"),
                ("valid_to", "TIMESTAMP_TZ(9)", "COLUMN", "Y"),
                ("is_current", "BOOLEAN", "COLUMN", "N"),
            ]
        )
    target = WriteTarget(
        relation=relation,
        business_key=("id",) if mode in {WriteMode.INCREMENTAL, WriteMode.SCD2} else (),
        schema=tuple(schema),
        publication_fence=publication,
    )
    first = {"id": "one", "label": "first"}
    second = {"id": "two", "label": "second"}
    if field_name is not None:
        first[field_name] = "2026-08-10T02:00:00Z"
        second.update({"id": "one", field_name: "2026-08-10T01:00:00Z"})
    elif mode is WriteMode.SCD2:
        second["id"] = "one"

    writer.write((first, second), target)

    sql = [statement for statement, _parameters in backend.statements]
    assert any(expected_sql in statement for statement in sql)
    if mode is WriteMode.INCREMENTAL:
        assert any(
            'PARTITION BY "id" ORDER BY "updated_at" DESC NULLS LAST, '
            '"_dander_ordinal" DESC' in item
            for item in sql
        )
    elif mode is WriteMode.SNAPSHOT:
        assert any("EQUAL_NULL(existing." in item and "NOT EXISTS" in item for item in sql)
    elif mode is WriteMode.SCD2:
        assert sql.count("SET DANDER_SCD2_EFFECTIVE_AT = CURRENT_TIMESTAMP()") == 1
        assert len([item for item in sql if "$DANDER_SCD2_EFFECTIVE_AT" in item]) == 2
        assert all(" AS current " not in item and "current." not in item for item in sql)
        assert any(" AS target_row SET " in item for item in sql)
        assert any(
            '"valid_from" TIMESTAMP_TZ(9) NOT NULL' in item
            and '"is_current" BOOLEAN NOT NULL' in item
            for item in sql
        )
    elif mode is WriteMode.REPLACE:
        target_name = '"DANDER_TEST"."raw"."records_replace"'
        assert f"DELETE FROM {target_name}" in sql
        assert any(item.startswith(f"INSERT INTO {target_name}") for item in sql)
    assert any(
        'UPDATE "DANDER_TEST"."raw"."dander_target_commits"' in statement for statement in sql
    )
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in sql)
    assert any(statement.startswith("DROP STAGE IF EXISTS") for statement in sql)
    assert not tuple(staging_root.iterdir())


def test_snowflake_mode_selection_fails_before_provider_mutation(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    before = list(backend.statements)

    with pytest.raises(ValueError, match="incremental writes require cursor_field"):
        runtime.writers.build_ingestion_writer(
            sandbox=False,
            batch_rows=2,
            schema_evolution=SchemaEvolution.STRICT,
            mode=WriteMode.INCREMENTAL,
        )

    assert backend.statements == before


def test_snowflake_replace_partial_replay_reloads_the_complete_manifest(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="replace_replay")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_replace_replay",
            run_id="run-replace-replay",
            token=7,
            authority_id="postgresql:test-state",
        ),
    )
    backend.describe_rows = [
        ("id", "VARCHAR(16777216)", "COLUMN", "Y"),
        ("label", "VARCHAR(16777216)", "COLUMN", "Y"),
    ]
    checks = 0

    def artifact_committed(*_args: object) -> bool:
        nonlocal checks
        checks += 1
        return checks == 1

    monkeypatch.setattr(
        "dander.providers.snowflake.writer._artifact_committed",
        artifact_committed,
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=WriteMode.REPLACE,
    )
    writer.write(
        (
            {"id": "one", "label": "first"},
            {"id": "two", "label": "second"},
            {"id": "three", "label": "third"},
        ),
        WriteTarget(
            relation=relation,
            schema=(
                WriteField(name="id", data_type="STRING"),
                WriteField(name="label", data_type="STRING"),
            ),
            publication_fence=publication,
        ),
    )

    sql = [statement for statement, _parameters in backend.statements]
    assert checks == 2
    assert len([statement for statement in sql if statement.startswith("PUT ")]) == 2
    assert len([statement for statement in sql if statement.startswith("COPY INTO")]) == 2
    assert (
        len(
            [
                statement
                for statement in sql
                if statement.startswith("INSERT INTO") and "dander_stage_loads" in statement
            ]
        )
        == 1
    )
    assert 'DELETE FROM "DANDER_TEST"."raw"."replace_replay"' in sql


@pytest.mark.parametrize("mode", [WriteMode.REPLACE, WriteMode.SNAPSHOT])
def test_snowflake_unkeyed_empty_writes_remain_fenced(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
    mode: WriteMode,
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    relation = RelationRef(
        catalog="DANDER_TEST",
        namespace="raw",
        name=f"empty_{mode.value}",
    )
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=f"empty_{mode.value}",
            run_id=f"run-empty-{mode.value}",
            token=6,
            authority_id="postgresql:test-state",
        ),
    )
    backend.describe_rows = [("id", "VARCHAR(16777216)", "COLUMN", "Y")]
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=mode,
        snapshot_field="id" if mode is WriteMode.SNAPSHOT else None,
    )

    affected = writer.write(
        (),
        WriteTarget(
            relation=relation,
            schema=(WriteField(name="id", data_type="STRING"),),
            publication_fence=publication,
        ),
    )

    assert affected == 0

    sql = [statement for statement, _parameters in backend.statements]
    expected = (
        f'DELETE FROM "DANDER_TEST"."raw"."empty_{mode.value}"'
        if mode is WriteMode.REPLACE
        else f'UPDATE "DANDER_TEST"."raw"."empty_{mode.value}" SET "id" = "id" WHERE FALSE'
    )
    assert expected in sql
    assert not any(statement.startswith("PUT ") for statement in sql)
    assert any(
        'UPDATE "DANDER_TEST"."raw"."dander_target_commits"' in statement for statement in sql
    )


@dataclass
class _Ownership:
    fence: FencingToken
    verifications: int = 0

    def verify(self) -> None:
        self.verifications += 1


def _snowflake_graph_plan(
    *,
    include_unsupported_target: bool = False,
) -> GraphExecutionPlan:
    source = SourceConfig(
        name="fixture",
        base_url="https://example.test",
        auth_strategy="none",
        endpoints=[
            Endpoint(
                name="records",
                path="/records",
                primary_key=["id"],
                raw_schema=[
                    RawField(name="id", data_type="STRING"),
                    RawField(name="label", data_type="STRING"),
                ],
            )
        ],
    )
    targets: list[dict[str, object]] = [
        {
            "id": "target",
            "type": "target",
            "name": "Target",
            "config": {
                "writer": {
                    "write_mode": "replace",
                    "destination": {
                        "dataset": "analytics",
                        "table": "graph_records",
                        "business_key": [],
                    },
                }
            },
            "fields": [
                {"name": "id", "type": "STRING"},
                {"name": "label", "type": "STRING"},
            ],
        }
    ]
    edges: list[dict[str, object]] = [
        {
            "from": "records",
            "to": "target",
            "mappings": [
                {"source": "id", "target": "id"},
                {"source": "label", "target": "label"},
            ],
        }
    ]
    if include_unsupported_target:
        targets.append(
            {
                "id": "unsupported",
                "type": "target",
                "name": "Unsupported",
                "config": {
                    "writer": {
                        "write_mode": "replace",
                        "destination": {
                            "dataset": "analytics",
                            "table": "unsupported_records",
                            "business_key": [],
                        },
                    }
                },
                "fields": [
                    {"name": "id", "type": "STRING", "cast_to": "STRING"},
                    {"name": "label", "type": "STRING"},
                ],
            }
        )
        edges.append(
            {
                "from": "records",
                "to": "unsupported",
                "mappings": [
                    {"source": "id", "target": "id"},
                    {"source": "label", "target": "label"},
                ],
            }
        )
    graph = PipelineGraph.model_validate(
        {
            "name": "snowflake_graph",
            "nodes": [
                {
                    "id": "records",
                    "type": "source",
                    "name": "Records",
                    "config": {"connector": "fixture", "endpoint": "records"},
                    "fields": [
                        {"name": "id", "type": "STRING"},
                        {"name": "label", "type": "STRING"},
                    ],
                },
                *targets,
            ],
            "edges": edges,
        }
    )
    return plan_graph_execution(
        graph,
        source,
        endpoint_relations={
            "records": RelationRef(
                catalog="DANDER_TEST",
                namespace="raw",
                name="fixture_records",
            )
        },
    )


def test_snowflake_builds_fenced_table_and_incremental_models(
    tmp_path: Path,
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    backend.query_history_template = ("SMALL", 4_096, 19, 1, 2, 3, 5)
    runner = runtime.transforms.build_transform_runner(
        graph_plan=None,
        build_models=True,
        raw_namespace="raw",
    )
    assert isinstance(runner, SnowflakeTransformRunner)
    _write_snowflake_model(
        tmp_path,
        name="table_model",
        materialization="table",
        with_tests=True,
    )
    backend.describe_rows = [
        ("id", "VARCHAR(16777216)", "COLUMN", "Y"),
        ("label", "VARCHAR(16777216)", "COLUMN", "Y"),
        ("updated_at", "NUMBER(38,0)", "COLUMN", "Y"),
    ]
    backend.transform_rows = {"stale": {"id": "stale", "label": "old-target", "updated_at": 0}}
    backend.transform_source_rows = [{"id": "fresh", "label": "new-target", "updated_at": 1}]
    table_ownership = _ownership("run-table", 1)

    result = runner.build(tmp_path, selected=["table_model"], ownership=table_ownership)

    assert result.models == ("table_model",)
    assert result.assertions == 4
    assert [operation.operation for operation in result.telemetry] == [
        TelemetryOperation.TRANSFORM,
        TelemetryOperation.TRANSFORM,
        TelemetryOperation.TEST,
        TelemetryOperation.TEST,
        TelemetryOperation.TEST,
        TelemetryOperation.TEST,
    ]
    assert all(operation.query_id for operation in result.telemetry)
    assert all(operation.resource_name == "DANDER_WH" for operation in result.telemetry)
    assert all(operation.resource_size == "SMALL" for operation in result.telemetry)
    assert all(operation.bytes_processed == 4_096 for operation in result.telemetry)
    assert all(operation.execution_duration_ms == 19 for operation in result.telemetry)
    assert all(operation.queue_duration_ms == 6 for operation in result.telemetry)
    sql = [statement for statement, _parameters in backend.statements]
    assert any(
        statement.startswith("DELETE FROM") and "table_model" in statement for statement in sql
    )
    assert any(
        statement.startswith("INSERT INTO") and "table_model" in statement for statement in sql
    )
    assert not any(statement.startswith("ALTER TABLE") for statement in sql)
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in sql)
    assert backend.transform_rows == {
        "fresh": {"id": "fresh", "label": "new-target", "updated_at": 1}
    }
    backend.assertion_failure_count = 1
    with pytest.raises(TransformRunError, match=r"table_model\.id\.not_null") as error:
        runner.test(tmp_path, selected=["table_model"])
    assert "older" not in str(error.value)
    backend.assertion_failure_count = 0
    tested = runner.test(tmp_path, selected=["table_model"])
    assert len(tested.telemetry) == 4
    assert all(operation.operation is TelemetryOperation.TEST for operation in tested.telemetry)
    backend.transform_rows.clear()

    before_incremental = len(backend.statements)
    _write_snowflake_model(tmp_path, name="incremental_model", materialization="incremental")
    backend.describe_rows = [
        ("id", "VARCHAR(16777216)", "COLUMN", "N"),
        ("label", "VARCHAR(16777216)", "COLUMN", "Y"),
        ("updated_at", "NUMBER(38,0)", "COLUMN", "N"),
    ]
    backend.transform_source_rows = [
        {"id": "one", "label": "older", "updated_at": 1},
        {"id": "one", "label": "aaa-equal", "updated_at": 2},
        {"id": "one", "label": "zzz-equal", "updated_at": 2},
        {"id": "two", "label": "second", "updated_at": 1},
    ]
    incremental_ownership = _ownership("run-incremental", 2)
    result = runner.build(
        tmp_path,
        selected=["incremental_model"],
        ownership=incremental_ownership,
    )

    assert result.models == ("incremental_model",)
    incremental_sql = [
        statement for statement, _parameters in backend.statements[before_incremental:]
    ]
    temporary = next(
        statement
        for statement in incremental_sql
        if statement.startswith("CREATE OR REPLACE TEMPORARY TABLE")
    )
    assert (
        'ROW_NUMBER() OVER (PARTITION BY source."id" ORDER BY '
        'source."updated_at" DESC NULLS LAST, source."label" DESC NULLS LAST)' in temporary
    )
    merge = next(
        statement
        for statement in incremental_sql
        if statement.startswith("MERGE INTO") and "dander_target_commits" not in statement
    )
    assert 'incoming."updated_at" >= target."updated_at"' in merge
    assert 'target."label" = incoming."label"' in merge
    assert backend.transform_rows == {
        "one": {"id": "one", "label": "zzz-equal", "updated_at": 2},
        "two": {"id": "two", "label": "second", "updated_at": 1},
    }
    backend.transform_source_rows = [{"id": "one", "label": "stale-replay", "updated_at": 1}]
    runner.build(
        tmp_path,
        selected=["incremental_model"],
        ownership=_ownership("run-replay", 3),
    )
    assert backend.transform_rows["one"] == {
        "id": "one",
        "label": "zzz-equal",
        "updated_at": 2,
    }
    assert backend.rollbacks == 0


def test_snowflake_preflights_whole_dag_before_provider_mutation(
    tmp_path: Path,
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    runner = runtime.transforms.build_transform_runner(graph_plan=None, build_models=True)
    assert isinstance(runner, SnowflakeTransformRunner)
    _write_snowflake_model(tmp_path, name="good_model", materialization="table")
    _write_snowflake_model(tmp_path, name="unsupported_view", materialization="view")
    before = list(backend.statements)

    with pytest.raises(TransformProjectError, match="view materialization is unavailable"):
        runner.build(
            tmp_path,
            ownership=_ownership("run-preflight", 3),
        )

    assert backend.statements == before


def test_snowflake_transform_coordinates_are_canonical(
    tmp_path: Path,
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, _backend, _staging_root = snowflake_runtime
    _write_snowflake_model(tmp_path, name="coordinate_model", materialization="table")
    project = TransformProject.load(
        tmp_path,
        catalog="DANDER-TEST",
        raw_namespace="RAW_DATA",
        target_dialect=SqlDialect.SNOWFLAKE,
    )
    model = project.ordered(["coordinate_model"])[0]
    compiled = project.compile(model)

    assert project.relation_ref_for_model(model).coordinates == (
        "DANDER-TEST",
        "analytics",
        "coordinate_model",
    )
    assert 'SELECT "id", "label", "updated_at"' in compiled
    assert '"DANDER-TEST"."RAW_DATA"."records"' in compiled
    with pytest.raises(TypeError, match="graph plan has the wrong type"):
        runtime.transforms.build_transform_runner(graph_plan=object(), build_models=False)
    assert runtime.transforms.build_transform_runner(graph_plan=None, build_models=False) is None


def test_snowflake_graph_uses_canonical_plan_fencing_cleanup_and_telemetry(
    tmp_path: Path,
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    runner = runtime.transforms.build_transform_runner(
        graph_plan=_snowflake_graph_plan(),
        build_models=True,
    )
    assert isinstance(runner, SnowflakeGraphRunner)
    backend.describe_rows = [
        ("id", "VARCHAR(16777216)", "COLUMN", "Y"),
        ("label", "VARCHAR(16777216)", "COLUMN", "Y"),
    ]
    backend.transform_source_rows = [{"id": "fresh", "label": "from-graph"}]
    ownership = _ownership("run-graph", 5)

    result = runner.build(tmp_path, ownership=ownership)

    assert result.models == ("target",)
    assert result.assertions == 0
    assert [operation.operation for operation in result.telemetry] == [
        TelemetryOperation.TRANSFORM,
        TelemetryOperation.TRANSFORM,
    ]
    assert all(operation.query_id for operation in result.telemetry)
    assert all(operation.resource_name == "DANDER_WH" for operation in result.telemetry)
    sql = [statement for statement, _parameters in backend.statements]
    staged = next(
        statement for statement in sql if statement.startswith("CREATE OR REPLACE TEMPORARY TABLE")
    )
    assert 'FROM "DANDER_TEST"."raw"."fixture_records"' in staged
    assert "`" not in staged
    assert any(
        statement.startswith("DELETE FROM") and '"analytics"."graph_records"' in statement
        for statement in sql
    )
    assert any(
        statement.startswith("INSERT INTO") and '"analytics"."graph_records"' in statement
        for statement in sql
    )
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in sql)
    assert ownership.verifications == 2
    assert backend.transform_rows == {"fresh": {"id": "fresh", "label": "from-graph"}}


def test_snowflake_graph_preflights_every_selected_target_before_mutation(
    tmp_path: Path,
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    runner = runtime.transforms.build_transform_runner(
        graph_plan=_snowflake_graph_plan(include_unsupported_target=True),
        build_models=True,
    )
    assert isinstance(runner, SnowflakeGraphRunner)
    before = list(backend.statements)

    with pytest.raises(GraphRuntimeError, match="safe-cast semantics"):
        runner.build(tmp_path, ownership=_ownership("run-preflight-graph", 6))

    assert backend.statements == before


def test_snowflake_graph_selection_and_stale_fence_fail_closed(
    tmp_path: Path,
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    runner = runtime.transforms.build_transform_runner(
        graph_plan=_snowflake_graph_plan(),
        build_models=True,
    )
    assert isinstance(runner, SnowflakeGraphRunner)
    before = list(backend.statements)
    with pytest.raises(GraphRuntimeError, match="Unknown graph target"):
        runner.build(tmp_path, selected=["missing"], ownership=_ownership("run-missing", 7))
    assert backend.statements == before

    backend.describe_rows = [
        ("id", "VARCHAR(16777216)", "COLUMN", "Y"),
        ("label", "VARCHAR(16777216)", "COLUMN", "Y"),
    ]
    backend.transform_source_rows = [{"id": "blocked", "label": "stale"}]
    backend.fence_touch_rowcount = 0
    with pytest.raises(TargetFenceLostError, match="lost before publication"):
        runner.build(tmp_path, ownership=_ownership("run-stale-graph", 8))

    attempted = [statement for statement, _parameters in backend.statements[len(before) :]]
    assert any(statement.startswith("CREATE OR REPLACE TEMPORARY TABLE") for statement in attempted)
    assert not any(statement.startswith(("DELETE FROM", "INSERT INTO")) for statement in attempted)
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in attempted)
    assert backend.transform_rows == {}


def test_snowflake_transform_requires_ownership_and_fails_closed_after_staging(
    tmp_path: Path,
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    runner = runtime.transforms.build_transform_runner(graph_plan=None, build_models=True)
    assert isinstance(runner, SnowflakeTransformRunner)
    _write_snowflake_model(tmp_path, name="owned_model", materialization="table")
    with pytest.raises(TransformRunError, match="require active lease ownership"):
        runner.build(tmp_path)
    backend.transform_source_rows = [
        {"id": "blocked", "label": "must-not-publish", "updated_at": 1}
    ]
    backend.describe_rows = [
        ("id", "VARCHAR(16777216)", "COLUMN", "Y"),
        ("label", "VARCHAR(16777216)", "COLUMN", "Y"),
        ("updated_at", "NUMBER(38,0)", "COLUMN", "Y"),
    ]
    backend.fence_touch_rowcount = 0
    before_publication = len(backend.statements)
    rollbacks = backend.rollbacks

    with pytest.raises(TargetFenceLostError, match="lost before publication"):
        runner.build(tmp_path, ownership=_ownership("run-stale", 1))

    attempted = [statement for statement, _ in backend.statements[before_publication:]]
    assert any(statement.startswith("CREATE OR REPLACE TEMPORARY TABLE") for statement in attempted)
    assert not any(statement.startswith(("DELETE FROM", "INSERT INTO")) for statement in attempted)
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in attempted)
    assert backend.rollbacks == rollbacks + 1
    assert backend.transform_rows == {}


def _ownership(run_id: str, token: int) -> _Ownership:
    return _Ownership(
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_transforms",
            run_id=run_id,
            token=token,
            authority_id="postgresql:test-state",
        )
    )


def _write_snowflake_model(
    root: Path,
    *,
    name: str,
    materialization: str,
    with_tests: bool = False,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.sql").write_text("SELECT id, label, updated_at FROM {{ ref('raw_records') }}")
    incremental = (
        "unique_key: [id]\nincremental_cursor: updated_at\n"
        if materialization == "incremental"
        else ""
    )
    tests = (
        "tests:\n"
        "  - column: id\n"
        "    not_null: true\n"
        "    unique: true\n"
        "  - column: label\n"
        "    accepted_values: [older, newer, second]\n"
        "  - column: id\n"
        "    relationships:\n"
        "      to: raw_parent_records\n"
        "      field: id\n"
        if with_tests
        else "tests: []\n"
    )
    (root / f"{name}.yml").write_text(
        f"model: {name}\n"
        "description: Portable Snowflake transform fixture.\n"
        "owner: data-eng\n"
        "dialect: portable\n"
        f"materialization: {materialization}\n"
        "dataset: analytics\n"
        "source_system: fixture\n"
        "sensitivity: public\n"
        f"{incremental}"
        "columns:\n"
        "  - name: id\n"
        "    type: STRING\n"
        "    description: Stable fixture identifier.\n"
        "  - name: label\n"
        "    type: STRING\n"
        "    description: Deterministic tie breaker.\n"
        "  - name: updated_at\n"
        "    type: INT64\n"
        "    description: Monotonic fixture cursor.\n"
        f"{tests}"
    )


def test_snowflake_retry_skips_committed_files_but_still_touches_fence(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    lease = FencingToken(
        lease_table=None,
        pipeline_id="snowflake_records",
        run_id="run-retry",
        token=8,
        authority_id="postgresql:test-state",
    )
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="records")
    publication = runtime.target_fence.claim(relation, lease)
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=publication,
    )
    rows = ({"id": "one", "label": "same"},)

    assert writer.write(rows, target) == 2
    first_puts = sum(statement.startswith("PUT ") for statement, _ in backend.statements)
    assert writer.write(rows, target) == 0
    second_puts = sum(statement.startswith("PUT ") for statement, _ in backend.statements)

    assert first_puts == second_puts == 1
    assert backend.commits == 3


def test_snowflake_copy_preserves_binary_and_temporal_parquet_types(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    backend.describe_rows = [
        ("id", "VARCHAR(16777216)", "COLUMN", "N"),
        ("payload", "BINARY(8388608)", "COLUMN", "Y"),
        ("observed_at", "TIMESTAMP_TZ(6)", "COLUMN", "Y"),
    ]
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="typed_records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_typed_records",
            run_id="run-typed",
            token=10,
            authority_id="postgresql:test-state",
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="payload", data_type="BYTES"),
            WriteField(name="observed_at", data_type="TIMESTAMP"),
        ),
        publication_fence=publication,
    )

    assert (
        writer.write(
            (
                {
                    "id": "one",
                    "payload": b"\x00\xff",
                    "observed_at": datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
                },
            ),
            target,
        )
        == 2
    )
    copy = next(statement for statement, _ in backend.statements if statement.startswith("COPY"))
    assert "USE_LOGICAL_TYPE = TRUE" in copy
    assert "BINARY_AS_TEXT = FALSE" in copy


def test_snowflake_rejects_oversized_singleton_before_remote_staging(tmp_path: Path) -> None:
    backend = _FakeSnowflake()
    registry = default_provider_registry()
    raw = _config()
    raw["max_logical_bytes_per_file"] = 1_024
    config = registry.parse(ProviderKind.WAREHOUSE, raw)
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={
            "catalog": "DANDER_TEST",
            "connection_factory": backend.connect,
            "staging_root": tmp_path,
        },
    )
    assert isinstance(runtime, WarehouseRuntime)
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_records",
            run_id="run-oversized",
            token=11,
            authority_id="postgresql:test-state",
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=publication,
    )
    statements_before_write = len(backend.statements)

    with pytest.raises(SnowflakeWriteError, match="exceeds max_logical_bytes_per_file"):
        writer.write(({"id": "one", "label": "x" * 3_000},), target)

    assert len(backend.statements) == statements_before_write
    assert not tuple(tmp_path.iterdir())


def test_snowflake_fails_closed_on_stale_fence_and_cleans_after_copy_failure(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, staging_root = snowflake_runtime
    backend.claim_rowcount = 0
    lease = FencingToken(
        lease_table=None,
        pipeline_id="snowflake_records",
        run_id="run-stale",
        token=1,
        authority_id="postgresql:test-state",
    )
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="records")
    with pytest.raises(TargetFenceLostError, match="rejected stale"):
        runtime.target_fence.claim(relation, lease)

    backend.claim_rowcount = 1
    publication = runtime.target_fence.claim(relation, lease)
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=publication,
    )
    backend.fail_prefix = "COPY INTO"

    with pytest.raises(SnowflakeWriteError, match="staged SCD1 write failed") as captured:
        writer.write(({"id": "private-record", "label": "secret-value"},), target)

    assert "private-record" not in str(captured.value)
    assert "secret-value" not in str(captured.value)
    assert not tuple(staging_root.iterdir())
    sql = [statement for statement, _ in backend.statements]
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in sql)
    assert any(statement.startswith("DROP STAGE IF EXISTS") for statement in sql)


def test_snowflake_lost_fence_blocks_publication_and_rolls_back(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, staging_root = snowflake_runtime
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_records",
            run_id="run-lost",
            token=12,
            authority_id="postgresql:test-state",
        ),
    )
    backend.fence_touch_rowcount = 0
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=publication,
    )

    with pytest.raises(TargetFenceLostError, match="lost before publication"):
        writer.write(({"id": "one", "label": "blocked"},), target)

    assert backend.rollbacks == 1
    assert not any(
        statement.startswith("MERGE INTO") and "dander_target_commits" not in statement
        for statement, _ in backend.statements
    )
    assert not tuple(staging_root.iterdir())


def test_snowflake_requires_explicit_variant_and_telemetry_is_bounded(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, _backend, _staging_root = snowflake_runtime
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    lease = FencingToken(
        lease_table=None,
        pipeline_id="snowflake_records",
        run_id="run-variant",
        token=9,
        authority_id="postgresql:test-state",
    )
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="records")
    publication = runtime.target_fence.claim(relation, lease)
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="payload", data_type="JSON"),
        ),
        publication_fence=publication,
    )

    with pytest.raises(SnowflakeWriteError, match="require snowflake/fallback=variant"):
        writer.write(({"id": "one", "payload": {"ready": True}},), target)

    class _Result:
        rowcount = 7
        query_id = "query-safe-7"

    telemetry = runtime.telemetry.operation(_Result(), operation=TelemetryOperation.LOAD)
    assert telemetry.rows_affected == 7
    assert telemetry.query_id == "query-safe-7"
    assert telemetry.resource_name == "DANDER_WH"


@pytest.mark.parametrize(
    ("mode", "business_key", "cursor_field", "snapshot_field"),
    [
        (WriteMode.SCD1, ("payload",), None, None),
        (WriteMode.INCREMENTAL, ("id",), "payload", None),
        (WriteMode.SNAPSHOT, (), None, "payload"),
    ],
)
def test_snowflake_variant_cannot_define_identity_or_ordering(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
    mode: WriteMode,
    business_key: tuple[str, ...],
    cursor_field: str | None,
    snapshot_field: str | None,
) -> None:
    runtime, backend, _staging_root = snowflake_runtime
    fallback = ProviderExtension(provider="snowflake", name="fallback", value="variant")
    relation = RelationRef(
        catalog="DANDER_TEST",
        namespace="raw",
        name=f"variant_{mode.value}_role",
    )
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=f"snowflake_variant_{mode.value}_role",
            run_id=f"run-variant-{mode.value}-role",
            token=12,
            authority_id="postgresql:test-state",
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=mode,
        cursor_field=cursor_field,
        snapshot_field=snapshot_field,
    )
    before = len(backend.statements)

    with pytest.raises(SnowflakeWriteError, match="cannot be a key, cursor, or snapshot"):
        writer.write(
            ({"id": "one", "payload": {"rank": 1}},),
            WriteTarget(
                relation=relation,
                business_key=business_key,
                schema=(
                    WriteField(name="id", data_type="STRING"),
                    WriteField(
                        name="payload",
                        data_type="JSON",
                        extensions=(fallback,),
                    ),
                ),
                publication_fence=publication,
            ),
        )

    assert len(backend.statements) == before


def test_snowflake_copy_parses_explicit_variant_before_publication(
    snowflake_runtime: tuple[WarehouseRuntime, _FakeSnowflake, Path],
) -> None:
    runtime, backend, staging_root = snowflake_runtime
    backend.describe_rows = [
        ("id", "VARCHAR(16777216)", "COLUMN", "N"),
        ("payload", "VARIANT", "COLUMN", "Y"),
    ]
    relation = RelationRef(catalog="DANDER_TEST", namespace="raw", name="variant_records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="snowflake_variant_records",
            run_id="run-variant-copy",
            token=10,
            authority_id="postgresql:test-state",
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    variant = ProviderExtension(provider="snowflake", name="fallback", value="variant")
    writer.write(
        ({"id": "one", "payload": {"nested": {"ready": True}}},),
        WriteTarget(
            relation=relation,
            business_key=("id",),
            schema=(
                WriteField(name="id", data_type="STRING"),
                WriteField(name="payload", data_type="JSON", extensions=(variant,)),
            ),
            publication_fence=publication,
        ),
    )

    sql = [statement for statement, _parameters in backend.statements]
    assert any(statement.startswith("COPY INTO") for statement in sql)
    merge = next(
        statement
        for statement in sql
        if statement.startswith("MERGE INTO") and "dander_target_commits" not in statement
    )
    assert 'PARSE_JSON(staged."payload") AS "payload"' in merge
    assert all(operation.transport is WriteTransport.COPY for operation in writer.drain_telemetry())
    assert not tuple(staging_root.iterdir())
