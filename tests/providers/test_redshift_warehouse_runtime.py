"""Redshift registration and stateful Parquet/COPY SCD1 conformance."""

# ruff: noqa: N803 -- boto3's public S3 API uses capitalized parameter names.

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Self

import pytest

from dander.concurrency import FencingToken, TargetFenceLostError
from dander.ingestion import Endpoint, RawField, SourceConfig
from dander.pipeline.graph import PipelineGraph
from dander.pipeline.runtime import GraphExecutionPlan, GraphRuntimeError, plan_graph_execution
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry
from dander.providers.redshift import RedshiftWarehouseConfig
from dander.providers.redshift.session import enrich_operation_telemetry
from dander.providers.redshift.transform import RedshiftGraphRunner, RedshiftTransformRunner
from dander.providers.redshift.writer import RedshiftWriteError, _delete_owned
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
    from collections.abc import Iterable, Mapping, Sequence


@dataclass
class _FakeRedshift:
    statements: list[tuple[int, str, tuple[object, ...]]] = field(default_factory=list)
    schema_rows: list[object] = field(
        default_factory=lambda: [
            ("id", "character varying", 65_535, None, None, "NO"),
            ("label", "character varying", 65_535, None, None, "YES"),
        ]
    )
    claim: tuple[str, int, str, int] | None = None
    fence_touch_rowcount: int = 1
    merge_rowcount: int = 1
    history: set[tuple[object, ...]] = field(default_factory=set)
    commits: int = 0
    rollbacks: int = 0
    closes: int = 0
    connections: int = 0
    transform_source_rows: list[dict[str, object]] | None = None
    transform_rows: dict[str, dict[str, object]] = field(default_factory=dict)
    assertion_failure_count: int = 0
    query_counter: int = 100
    telemetry_rows: list[object] = field(default_factory=list)
    telemetry_error: bool = False
    direct_error: bool = False

    def connect(self) -> _FakeConnection:
        self.connections += 1
        return _FakeConnection(self, self.connections)


class _FakeConnection:
    def __init__(self, backend: _FakeRedshift, connection_id: int) -> None:
        self.backend = backend
        self.connection_id = connection_id
        self.autocommit = True
        self.in_transaction = False
        self.aborted = False
        self.last_query_id: int | None = None
        self.temporary_rows: list[dict[str, object]] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.in_transaction = False
        self.aborted = False
        self.backend.commits += 1

    def rollback(self) -> None:
        self.in_transaction = False
        self.aborted = False
        self.backend.rollbacks += 1

    def close(self) -> None:
        self.backend.closes += 1


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.backend = connection.backend
        self.rowcount = 0
        self._row: object | None = None
        self._rows: list[object] = []

    def execute(self, command: str, args: Sequence[object] | None = None) -> Self:
        if self.connection.aborted:
            raise RuntimeError("Redshift transaction is aborted until rollback")
        parameters = tuple(args or ())
        compact = " ".join(command.split())
        if compact == "BEGIN" and self.connection.in_transaction:
            raise AssertionError("Redshift writer attempted to nest a transaction")
        self.connection.in_transaction = True
        self.backend.statements.append((self.connection.connection_id, compact, parameters))
        if compact.startswith("COPY ") or (
            compact.startswith("CREATE TEMP TABLE") and " AS SELECT" in compact
        ):
            self.backend.query_counter += 1
            self.connection.last_query_id = self.backend.query_counter
        if compact == "SELECT current_database(), current_user":
            self._row = ("analytics", "dander_user")
        elif compact == "SELECT pg_last_query_id()":
            self._row = (self.connection.last_query_id,)
        elif compact.startswith("WITH recent AS (SELECT query_id"):
            if self.backend.telemetry_error:
                self.connection.aborted = True
                raise RuntimeError("system view denied")
            self._rows = list(self.backend.telemetry_rows)
        elif compact.startswith('SELECT "authority_id"'):
            self._row = self.backend.claim
        elif compact.startswith("INSERT INTO") and "dander_target_commits" in compact:
            self.backend.claim = (
                str(parameters[2]),
                _required_int(parameters[3]),
                str(parameters[4]),
                _required_int(parameters[5]),
            )
            self.rowcount = 1
        elif (
            compact.startswith("UPDATE")
            and "dander_target_commits" in compact
            and 'SET "authority_id"' in compact
        ):
            self.backend.claim = (
                str(parameters[0]),
                _required_int(parameters[1]),
                str(parameters[2]),
                _required_int(parameters[3]),
            )
            self.rowcount = 1
        elif compact.startswith("UPDATE") and "dander_target_commits" in compact:
            self.rowcount = self.backend.fence_touch_rowcount
        elif compact.startswith("SELECT column_name") and "svv_columns" in compact:
            self._rows = list(self.backend.schema_rows)
        elif compact.startswith("CREATE TEMP TABLE") and " AS SELECT" in compact:
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
        elif (
            compact.startswith("UPDATE")
            and "dander_target_commits" not in compact
            and self.backend.transform_source_rows is not None
        ):
            assert 'incoming."updated_at" >= target."updated_at"' in compact
            for row in self.connection.temporary_rows:
                key = str(row["id"])
                current = self.backend.transform_rows.get(key)
                if current is not None and _integer(row, "updated_at") >= _integer(
                    current, "updated_at"
                ):
                    self.backend.transform_rows[key] = dict(row)
        elif (
            compact.startswith("INSERT INTO")
            and "dander_target_commits" not in compact
            and "dander_stage_loads" not in compact
            and self.backend.transform_source_rows is not None
        ):
            if "WHERE NOT EXISTS" in compact:
                for row in self.connection.temporary_rows:
                    self.backend.transform_rows.setdefault(str(row["id"]), dict(row))
            else:
                self.backend.transform_rows = {
                    str(row["id"]): dict(row) for row in self.connection.temporary_rows
                }
        elif compact.startswith("INSERT INTO") and "dander_stage_loads" in compact:
            self.rowcount = 0 if parameters[:5] in self.backend.history else 1
            self.backend.history.add(parameters[:5])
        elif len(parameters) == 5 and compact.startswith(("DELETE FROM", "INSERT INTO", "UPDATE")):
            self.rowcount = 0 if parameters in self.backend.history else self.backend.merge_rowcount
        elif compact.startswith("MERGE INTO"):
            self.rowcount = (
                0 if parameters[:5] in self.backend.history else self.backend.merge_rowcount
            )
        elif compact.startswith("SELECT COUNT"):
            self._row = (self.backend.assertion_failure_count,)
        return self

    def executemany(self, command: str, args: Iterable[Sequence[object]]) -> Self:
        if self.connection.aborted:
            raise RuntimeError("Redshift transaction is aborted until rollback")
        rows = tuple(tuple(row) for row in args)
        compact = " ".join(command.split())
        self.connection.in_transaction = True
        self.backend.statements.append((self.connection.connection_id, compact, rows))
        if self.backend.direct_error:
            self.connection.aborted = True
            raise RuntimeError("private Redshift direct-load response")
        self.rowcount = len(rows)
        return self

    def fetchone(self) -> object | None:
        return self._row

    def fetchall(self) -> list[object]:
        return list(self._rows)

    def close(self) -> None:
        return None


@dataclass
class _FakeS3:
    region: str = "us-east-1"
    uploads: list[tuple[str, str, bytes]] = field(default_factory=list)
    puts: list[tuple[str, str, bytes]] = field(default_factory=list)
    deleted: list[tuple[str, ...]] = field(default_factory=list)
    region_checks: int = 0
    fail_upload_at: int | None = None

    def get_bucket_location(self, *, Bucket: str) -> Mapping[str, object]:
        del Bucket
        self.region_checks += 1
        return {"LocationConstraint": None if self.region == "us-east-1" else self.region}

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None:
        self.uploads.append((Bucket, Key, Path(Filename).read_bytes()))
        if self.fail_upload_at == len(self.uploads):
            raise RuntimeError("ambiguous S3 upload failure")

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.puts.append((Bucket, Key, Body))

    def delete_objects(
        self,
        *,
        Bucket: str,
        Delete: Mapping[str, object],
    ) -> Mapping[str, object]:
        del Bucket
        raw = Delete["Objects"]
        assert isinstance(raw, list)
        keys = tuple(str(item["Key"]) for item in raw if isinstance(item, dict))
        self.deleted.append(keys)
        return {}


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError("fake Redshift received a non-integer fence value")
    return value


def _integer(row: dict[str, object], field_name: str) -> int:
    value = row[field_name]
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


def _config() -> dict[str, object]:
    return {
        "provider": "redshift",
        "deployment": "provisioned",
        "host": "example.abc123.us-east-1.redshift.amazonaws.com",
        "database": "analytics",
        "schema": "raw",
        "db_user": "dander_user",
        "region": "us-east-1",
        "cluster_identifier": "dander-test",
        "copy_role_arn": "arn:aws:iam::123456789012:role/DanderRedshiftCopy",
        "staging_bucket": "dander-redshift-staging",
        "max_rows_per_file": 2,
        "max_logical_bytes_per_file": 1_048_576,
    }


@pytest.fixture
def redshift_runtime(
    tmp_path: Path,
) -> tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path]:
    backend = _FakeRedshift()
    s3 = _FakeS3()
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.WAREHOUSE, _config())
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={
            "catalog": "analytics",
            "connection_factory": backend.connect,
            "s3_client": s3,
            "staging_root": tmp_path,
        },
    )
    assert isinstance(runtime, WarehouseRuntime)
    assert runtime.ingestion_schema_mapper is runtime.schema_mapper
    return runtime, backend, s3, tmp_path


def _direct_redshift_runtime(
    tmp_path: Path,
    *,
    max_rows: int = 2,
    max_logical_bytes: int = 4_096,
) -> tuple[WarehouseRuntime, _FakeRedshift, _FakeS3]:
    backend = _FakeRedshift()
    s3 = _FakeS3()
    registry = default_provider_registry()
    raw = _config()
    raw["direct_max_rows"] = max_rows
    raw["direct_max_logical_bytes"] = max_logical_bytes
    config = registry.parse(ProviderKind.WAREHOUSE, raw)
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={
            "catalog": "analytics",
            "connection_factory": backend.connect,
            "s3_client": s3,
            "staging_root": tmp_path,
        },
    )
    assert isinstance(runtime, WarehouseRuntime)
    return runtime, backend, s3


def _target(runtime: WarehouseRuntime, *, run_id: str = "run-one") -> WriteTarget:
    relation = RelationRef(catalog="analytics", namespace="raw", name="records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="redshift_records",
            run_id=run_id,
            token=4,
            authority_id="bigquery:gcp-control.dander_state",
        ),
    )
    return WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="label", data_type="STRING"),
        ),
        publication_fence=publication,
    )


def test_redshift_registration_is_lazy_and_uses_native_coordinates() -> None:
    module_name = "dander.providers.redshift.runtime"
    sys.modules.pop(module_name, None)
    registry = default_provider_registry()

    config = registry.parse(ProviderKind.WAREHOUSE, _config())

    assert module_name not in sys.modules
    assert isinstance(config, RedshiftWarehouseConfig)
    relation = config.raw_relation(
        "records",
        compatibility_catalog="ignored-gcp-project",
        compatibility_namespace=None,
    )
    assert relation == RelationRef(catalog="analytics", namespace="raw", name="records")
    assert "password" not in config.model_dump_json()
    assert config.direct_max_rows == 0
    assert config.direct_max_logical_bytes == 0


def test_redshift_direct_thresholds_are_explicit_and_paired() -> None:
    raw = _config()
    raw["direct_max_rows"] = 10

    with pytest.raises(ValueError, match="must both be zero or positive"):
        RedshiftWarehouseConfig.model_validate(raw)


def test_redshift_schema_mapper_requires_explicit_json_super_without_io(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    before = list(backend.statements)
    fallback = ProviderExtension(provider="redshift", name="fallback", value="super")

    schema = runtime.schema_mapper.canonical_schema(
        (WriteField(name="payload", data_type="JSON", extensions=(fallback,)),)
    )

    assert fallback in schema.fields[0].extensions
    assert backend.statements == before
    with pytest.raises(WarehouseSchemaSupportError, match="require redshift/fallback=super"):
        runtime.schema_mapper.canonical_schema((WriteField(name="payload", data_type="JSON"),))
    with pytest.raises(WarehouseSchemaSupportError, match="unsupported for this type"):
        runtime.schema_mapper.canonical_schema(
            (WriteField(name="payload", data_type="STRING", extensions=(fallback,)),)
        )
    assert backend.statements == before


def test_redshift_serverless_uses_an_aws_derived_database_user() -> None:
    registry = default_provider_registry()
    raw = _config()
    raw.update(
        {
            "deployment": "serverless",
            "workgroup_name": "dander-test",
        }
    )
    raw.pop("cluster_identifier")
    raw.pop("db_user")

    config = registry.parse(ProviderKind.WAREHOUSE, raw)

    assert isinstance(config, RedshiftWarehouseConfig)
    assert config.db_user is None
    with pytest.raises(ProviderFactoryError, match="Invalid warehouse provider"):
        registry.parse(ProviderKind.WAREHOUSE, {**raw, "db_user": "dander_user"})


def test_redshift_runtime_validates_connection_and_exposes_fenced_transforms(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, _backend, _s3, _root = redshift_runtime
    relation = RelationRef(catalog="analytics", namespace="raw", name="records")

    assert runtime.relation_codec.render(relation) == '"analytics"."raw"."records"'
    assert runtime.capabilities.write_modes == frozenset(WriteMode)
    assert runtime.capabilities.transports == frozenset(
        {WriteTransport.COPY, WriteTransport.DIRECT}
    )
    assert runtime.capabilities.supports_transforms is True
    assert runtime.capabilities.supports_graphs is True
    assert isinstance(
        runtime.transforms.build_transform_runner(graph_plan=None, build_models=True),
        RedshiftTransformRunner,
    )


@dataclass
class _Ownership:
    fence: FencingToken
    verifications: int = 0

    def verify(self) -> None:
        self.verifications += 1


def _redshift_graph_plan(*, include_unsupported_target: bool = False) -> GraphExecutionPlan:
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
                    {"name": "id", "type": "STRING"},
                    {
                        "name": "label",
                        "type": "STRING",
                        "cast_to": "JSON",
                        "extensions": [
                            {
                                "provider": "redshift",
                                "name": "fallback",
                                "value": "super",
                            }
                        ],
                    },
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
            "name": "redshift_graph",
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
                catalog="analytics",
                namespace="raw",
                name="fixture_records",
            )
        },
    )


def test_redshift_builds_fenced_table_and_incremental_models(
    tmp_path: Path,
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    runner = runtime.transforms.build_transform_runner(
        graph_plan=None,
        build_models=True,
        raw_namespace="raw",
    )
    assert isinstance(runner, RedshiftTransformRunner)
    _write_redshift_model(tmp_path, name="table_model", materialization="table", with_tests=True)
    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "YES"),
        ("label", "character varying", 65_535, None, None, "YES"),
        ("updated_at", "bigint", None, None, None, "YES"),
    ]
    backend.transform_rows = {"stale": {"id": "stale", "label": "old", "updated_at": 0}}
    backend.transform_source_rows = [{"id": "fresh", "label": "newer", "updated_at": 1}]

    result = runner.build(
        tmp_path,
        selected=["table_model"],
        ownership=_ownership("run-table", 1),
    )

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
    assert result.telemetry[0].query_id == "101"
    assert all(operation.query_id is None for operation in result.telemetry[1:])
    assert backend.transform_rows == {"fresh": {"id": "fresh", "label": "newer", "updated_at": 1}}
    sql = [statement for _, statement, _ in backend.statements]
    assert any(
        statement.startswith("DELETE FROM") and "table_model" in statement for statement in sql
    )
    assert any(
        statement.startswith("INSERT INTO") and "table_model" in statement for statement in sql
    )
    assert not any(statement.startswith("ALTER TABLE") for statement in sql)
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in sql)

    _write_redshift_model(tmp_path, name="incremental_model", materialization="incremental")
    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "NO"),
        ("label", "character varying", 65_535, None, None, "YES"),
        ("updated_at", "bigint", None, None, None, "NO"),
    ]
    backend.transform_rows.clear()
    backend.transform_source_rows = [
        {"id": "one", "label": "older", "updated_at": 1},
        {"id": "one", "label": "aaa-equal", "updated_at": 2},
        {"id": "one", "label": "zzz-equal", "updated_at": 2},
        {"id": "two", "label": "second", "updated_at": 1},
    ]
    before_incremental = len(backend.statements)

    result = runner.build(
        tmp_path,
        selected=["incremental_model"],
        ownership=_ownership("run-incremental", 2),
    )

    assert result.models == ("incremental_model",)
    incremental_sql = [statement for _, statement, _ in backend.statements[before_incremental:]]
    temporary = next(
        statement
        for statement in incremental_sql
        if statement.startswith("CREATE TEMP TABLE") and " AS SELECT" in statement
    )
    assert (
        'ROW_NUMBER() OVER (PARTITION BY source."id" ORDER BY '
        'source."updated_at" DESC NULLS LAST, source."label" DESC NULLS LAST)' in temporary
    )
    update = next(
        statement
        for statement in incremental_sql
        if statement.startswith("UPDATE ") and "incremental_model" in statement
    )
    insert = next(
        statement
        for statement in incremental_sql
        if statement.startswith("INSERT INTO") and "incremental_model" in statement
    )
    assert 'incoming."updated_at" >= target."updated_at"' in update
    assert '"label" = incoming."label"' in update
    assert "WHERE NOT EXISTS" in insert
    assert backend.transform_rows == {
        "one": {"id": "one", "label": "zzz-equal", "updated_at": 2},
        "two": {"id": "two", "label": "second", "updated_at": 1},
    }
    backend.transform_source_rows = [{"id": "one", "label": "stale", "updated_at": 1}]
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


def test_redshift_preflights_the_dag_and_preserves_canonical_coordinates(
    tmp_path: Path,
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    runner = runtime.transforms.build_transform_runner(graph_plan=None, build_models=True)
    assert isinstance(runner, RedshiftTransformRunner)
    _write_redshift_model(tmp_path, name="good_model", materialization="table")
    _write_redshift_model(tmp_path, name="unsupported_view", materialization="view")
    before = list(backend.statements)

    with pytest.raises(TransformProjectError, match="view materialization is unavailable"):
        runner.build(tmp_path, ownership=_ownership("run-preflight", 1))

    assert backend.statements == before
    project = TransformProject.load(
        tmp_path,
        catalog="analytics",
        raw_namespace="raw_data",
        target_dialect=SqlDialect.REDSHIFT,
    )
    model = project.ordered(["good_model"])[0]
    assert project.relation_ref_for_model(model).coordinates == (
        "analytics",
        "analytics",
        "good_model",
    )
    assert '"analytics"."raw_data"."records"' in project.compile(model)
    with pytest.raises(TypeError, match="graph plan has the wrong type"):
        runtime.transforms.build_transform_runner(graph_plan=object(), build_models=False)
    assert runtime.transforms.build_transform_runner(graph_plan=None, build_models=False) is None


def test_redshift_graph_uses_canonical_plan_fencing_and_cleanup(
    tmp_path: Path,
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    runner = runtime.transforms.build_transform_runner(
        graph_plan=_redshift_graph_plan(),
        build_models=True,
    )
    assert isinstance(runner, RedshiftGraphRunner)
    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "YES"),
        ("label", "character varying", 65_535, None, None, "YES"),
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
    assert result.telemetry[0].query_id is not None
    assert result.telemetry[1].query_id is None
    sql = [statement for _, statement, _ in backend.statements]
    staged = next(
        statement
        for statement in sql
        if statement.startswith("CREATE TEMP TABLE") and " AS SELECT" in statement
    )
    assert 'FROM "analytics"."raw"."fixture_records"' in staged
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


def test_redshift_graph_preflights_every_selected_target_before_mutation(
    tmp_path: Path,
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    runner = runtime.transforms.build_transform_runner(
        graph_plan=_redshift_graph_plan(include_unsupported_target=True),
        build_models=True,
    )
    assert isinstance(runner, RedshiftGraphRunner)
    before = list(backend.statements)

    with pytest.raises(GraphRuntimeError, match="safe-cast semantics"):
        runner.build(tmp_path, ownership=_ownership("run-preflight-graph", 6))

    assert backend.statements == before


def test_redshift_graph_rejects_invalid_target_contracts_before_mutation(
    tmp_path: Path,
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    plan = _redshift_graph_plan()
    compiled = plan.targets[0]
    other_target = WriteTarget(
        relation=RelationRef(catalog="other", namespace="analytics", name="graph_records"),
        business_key=compiled.target.business_key,
        schema=compiled.target.schema,
    )
    invalid_plans = (
        (
            replace(plan, targets=(replace(compiled, write_mode=WriteMode.SCD1),)),
            "requires replace mode",
        ),
        (
            replace(plan, targets=(replace(compiled, target=other_target),)),
            "belongs to another database",
        ),
    )
    before = list(backend.statements)

    for invalid, message in invalid_plans:
        runner = runtime.transforms.build_transform_runner(
            graph_plan=invalid,
            build_models=True,
        )
        assert isinstance(runner, RedshiftGraphRunner)
        with pytest.raises(GraphRuntimeError, match=message):
            runner.build(tmp_path, ownership=_ownership("run-invalid-graph", 7))

    assert backend.statements == before


def test_redshift_graph_selection_and_stale_fence_fail_closed(
    tmp_path: Path,
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    runner = runtime.transforms.build_transform_runner(
        graph_plan=_redshift_graph_plan(),
        build_models=True,
    )
    assert isinstance(runner, RedshiftGraphRunner)
    before = list(backend.statements)
    with pytest.raises(GraphRuntimeError, match="Unknown graph target"):
        runner.build(tmp_path, selected=["missing"], ownership=_ownership("run-missing", 8))
    with pytest.raises(GraphRuntimeError, match="selected no targets"):
        runner.build(tmp_path, selected=[], ownership=_ownership("run-empty", 9))
    assert backend.statements == before

    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "YES"),
        ("label", "character varying", 65_535, None, None, "YES"),
    ]
    backend.transform_source_rows = [{"id": "blocked", "label": "stale"}]
    backend.fence_touch_rowcount = 0
    with pytest.raises(TargetFenceLostError, match="lost before publication"):
        runner.build(tmp_path, ownership=_ownership("run-stale-graph", 10))

    attempted = [statement for _, statement, _ in backend.statements[len(before) :]]
    assert any(statement.startswith("CREATE TEMP TABLE") for statement in attempted)
    assert not any(statement.startswith("DELETE FROM") for statement in attempted)
    assert not any(
        statement.startswith("INSERT INTO") and "graph_records" in statement
        for statement in attempted
    )
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in attempted)
    assert backend.transform_rows == {}


def test_redshift_transform_requires_ownership_and_fails_closed_after_staging(
    tmp_path: Path,
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    runner = runtime.transforms.build_transform_runner(graph_plan=None, build_models=True)
    assert isinstance(runner, RedshiftTransformRunner)
    _write_redshift_model(tmp_path, name="owned_model", materialization="table")
    with pytest.raises(TransformRunError, match="require active lease ownership"):
        runner.build(tmp_path)
    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "YES"),
        ("label", "character varying", 65_535, None, None, "YES"),
        ("updated_at", "bigint", None, None, None, "YES"),
    ]
    backend.transform_source_rows = [
        {"id": "blocked", "label": "must-not-publish", "updated_at": 1}
    ]
    backend.fence_touch_rowcount = 0
    before_publication = len(backend.statements)
    rollbacks = backend.rollbacks

    with pytest.raises(TargetFenceLostError, match="lost before publication"):
        runner.build(tmp_path, ownership=_ownership("run-stale", 1))

    attempted = [statement for _, statement, _ in backend.statements[before_publication:]]
    assert any(statement.startswith("CREATE TEMP TABLE") for statement in attempted)
    assert not any(statement.startswith("DELETE FROM") for statement in attempted)
    assert not any(
        statement.startswith("INSERT INTO") and "owned_model" in statement
        for statement in attempted
    )
    assert any(statement.startswith("DROP TABLE IF EXISTS") for statement in attempted)
    # Query-ID capture ends its telemetry-only transaction before the failed fence rolls back.
    assert backend.rollbacks == rollbacks + 2
    assert backend.transform_rows == {}


def _ownership(run_id: str, token: int) -> _Ownership:
    return _Ownership(
        FencingToken(
            lease_table=None,
            pipeline_id="redshift_transforms",
            run_id=run_id,
            token=token,
            authority_id="postgresql:test-state",
        )
    )


def _write_redshift_model(
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
        "description: Portable Redshift transform fixture.\n"
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


def _write_redshift_super_model(root: Path) -> None:
    (root / "super_model.sql").write_text("SELECT id, payload FROM {{ ref('raw_super_records') }}")
    (root / "super_model.yml").write_text(
        "model: super_model\n"
        "description: Portable Redshift SUPER fixture.\n"
        "owner: data-eng\n"
        "dialect: portable\n"
        "materialization: table\n"
        "dataset: analytics\n"
        "source_system: fixture\n"
        "sensitivity: internal\n"
        "columns:\n"
        "  - name: id\n"
        "    type: STRING\n"
        "    description: Stable fixture identifier.\n"
        "  - name: payload\n"
        "    type: JSON\n"
        "    description: Explicit Redshift SUPER payload.\n"
        "    extensions:\n"
        "      - provider: redshift\n"
        "        name: fallback\n"
        "        value: super\n"
        "tests: []\n"
    )


def test_redshift_claim_serializes_and_rejects_a_stale_token(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    relation = RelationRef(catalog="analytics", namespace="raw", name="records")
    newer = FencingToken(
        lease_table=None,
        pipeline_id="redshift_records",
        run_id="new-run",
        token=9,
        authority_id="postgresql:test-state",
    )
    runtime.target_fence.claim(relation, newer)
    rollback_before = backend.rollbacks

    with pytest.raises(TargetFenceLostError, match="rejected stale"):
        runtime.target_fence.claim(
            relation,
            FencingToken(
                lease_table=None,
                pipeline_id="redshift_records",
                run_id="old-run",
                token=8,
                authority_id="postgresql:test-state",
            ),
        )

    assert backend.rollbacks == rollback_before + 1
    claim_sql = [statement for _, statement, _ in backend.statements]
    assert "BEGIN" in claim_sql
    assert any(
        statement.startswith('LOCK "raw"."dander_target_commits"') for statement in claim_sql
    )


def test_redshift_rejects_truncated_relation_identifiers_before_connecting(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    relation = RelationRef(catalog="analytics", namespace="n" * 128, name="records")
    connections_before = backend.connections

    with pytest.raises(ValueError, match="cannot exceed 127 bytes"):
        runtime.target_fence.claim(
            relation,
            FencingToken(
                lease_table=None,
                pipeline_id="redshift_records",
                run_id="too-long",
                token=1,
                authority_id="postgresql:test-state",
            ),
        )

    assert backend.connections == connections_before


def test_redshift_stages_bounded_parts_merges_deterministically_and_cleans(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, s3, staging_root = redshift_runtime
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=3,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = _target(runtime)
    backend.merge_rowcount = 2

    assert (
        writer.write(
            (
                {"id": "one", "label": "old"},
                {"id": "one", "label": "new"},
                {"id": "two", "label": "second"},
            ),
            target,
        )
        == 2
    )

    assert len(s3.uploads) == 2
    assert len(s3.puts) == 1
    manifest = json.loads(s3.puts[0][2])
    assert all(entry["mandatory"] is True for entry in manifest["entries"])
    assert all(entry["meta"]["content_length"] > 0 for entry in manifest["entries"])
    assert s3.deleted and len(s3.deleted[-1]) == 3
    assert not tuple(staging_root.iterdir())

    writer_connection = max(connection_id for connection_id, _, _ in backend.statements)
    sql = [
        statement
        for connection_id, statement, _ in backend.statements
        if connection_id == writer_connection
    ]
    assert any(statement.startswith("CREATE TEMP TABLE") for statement in sql)
    copy = next(statement for statement in sql if statement.startswith("COPY "))
    assert "IAM_ROLE 'arn:aws:iam::123456789012:role/DanderRedshiftCopy'" in copy
    assert "ACCESS_KEY_ID" not in copy and "SECRET_ACCESS_KEY" not in copy
    merge = next(statement for statement in sql if statement.startswith("MERGE INTO"))
    assert 'ROW_NUMBER() OVER (PARTITION BY "id" ORDER BY "_dander_ordinal" DESC)' in merge
    touch_indexes = [
        index
        for index, statement in enumerate(sql)
        if statement.startswith("UPDATE") and "dander_target_commits" in statement
    ]
    assert len(touch_indexes) == 2
    assert touch_indexes[0] < sql.index(merge) < touch_indexes[1]
    assert sql[touch_indexes[0] - 1].startswith("LOCK ")


def test_redshift_copy_telemetry_is_enriched_without_sensitive_history(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    backend.telemetry_rows = [
        (101, 1_500, 2_001, "service-class-5", "primary", 1_000, 1, 2, 3, 222, 333, 55)
    ]
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=3,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    assert (
        writer.write(
            (
                {"id": "one", "label": "first"},
                {"id": "two", "label": "second"},
                {"id": "three", "label": "third"},
            ),
            _target(runtime),
        )
        == 1
    )

    operations = writer.drain_telemetry()
    assert [operation.operation for operation in operations] == [
        TelemetryOperation.LOAD,
        TelemetryOperation.QUERY,
    ]
    loaded, published = operations
    assert loaded.query_id == "101"
    assert loaded.rows_written == 3
    assert loaded.bytes_read == 333
    assert loaded.bytes_written == 222
    assert loaded.bytes_processed == 1_000
    assert loaded.queue_duration_ms == 2
    assert loaded.execution_duration_ms == 3
    assert loaded.spill_bytes == 3 * 1_024 * 1_024
    assert loaded.job_id == "55"
    assert loaded.resource_name == "service-class-5"
    assert loaded.resource_size == "primary"
    assert loaded.transport is WriteTransport.COPY
    assert published.query_id is None
    assert published.rows_affected == 1
    assert writer.drain_telemetry() == ()
    history_statement, history_parameters = next(
        (statement, parameters)
        for _, statement, parameters in backend.statements
        if statement.startswith("WITH recent AS (SELECT query_id")
    )
    assert history_parameters == (101,)
    assert "metrics_level = 'Step'" in history_statement
    assert "query_text" not in history_statement.casefold()
    assert "error" not in history_statement.casefold()
    assert "data_source" not in history_statement.casefold()


def test_redshift_direct_load_parses_explicit_super_without_s3_or_query_attribution(
    tmp_path: Path,
) -> None:
    runtime, backend, s3 = _direct_redshift_runtime(tmp_path)
    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "NO"),
        ("payload", "super", None, None, None, "YES"),
    ]
    relation = RelationRef(catalog="analytics", namespace="raw", name="direct_records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="redshift_direct_records",
            run_id="run-direct",
            token=7,
            authority_id="postgresql:test-state",
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    fallback = ProviderExtension(provider="redshift", name="fallback", value="super")
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="payload", data_type="JSON", extensions=(fallback,)),
        ),
        publication_fence=publication,
    )

    assert writer.supports_batched_writes is False
    assert writer.accepts_streaming_input is True
    assert writer.write(({"id": "one", "payload": {"ready": True}},), target) == 1

    direct_statement, direct_parameters = next(
        (statement, parameters)
        for _, statement, parameters in backend.statements
        if statement.startswith('INSERT INTO "dander_stage_')
        and "dander_stage_loads" not in statement
    )
    assert '"payload" VARBYTE(16777216)' in next(
        statement
        for _, statement, _ in backend.statements
        if statement.startswith("CREATE TEMP TABLE")
    )
    assert direct_statement.endswith("VALUES (%s, %s, %s)")
    assert direct_parameters == (("one", b'{"ready":true}', 0),)
    assert 'JSON_PARSE(staged."payload") AS "payload"' in next(
        statement for _, statement, _ in backend.statements if statement.startswith("MERGE INTO")
    )
    assert s3.region_checks == 0
    assert not s3.uploads and not s3.puts and not s3.deleted
    operations = writer.drain_telemetry()
    assert [operation.operation for operation in operations] == [
        TelemetryOperation.LOAD,
        TelemetryOperation.QUERY,
    ]
    assert all(operation.transport is WriteTransport.DIRECT for operation in operations)
    assert operations[0].rows_written == 1
    assert operations[0].query_id is None
    assert not any(
        statement.startswith("WITH recent AS (SELECT query_id")
        for _, statement, _ in backend.statements
    )
    history_before = set(backend.history)
    assert writer.write(({"id": "one", "payload": {"ready": True}},), target) == 0
    assert backend.history == history_before
    assert not tuple(tmp_path.iterdir())


def test_redshift_direct_load_accepts_a_valid_super_row_above_the_copy_limit(
    tmp_path: Path,
) -> None:
    runtime, backend, s3 = _direct_redshift_runtime(
        tmp_path,
        max_rows=1,
        max_logical_bytes=8 * 1_024 * 1_024,
    )
    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "NO"),
        ("payload", "super", None, None, None, "YES"),
    ]
    relation = RelationRef(catalog="analytics", namespace="raw", name="large_direct_records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id="redshift_large_direct",
            run_id="run-large-direct",
            token=8,
            authority_id="postgresql:test-state",
        ),
    )
    fallback = ProviderExtension(provider="redshift", name="fallback", value="super")
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="payload", data_type="JSON", extensions=(fallback,)),
        ),
        publication_fence=publication,
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    assert (
        writer.write(
            ({"id": "one", "payload": {"value": "x" * 4_300_000}},),
            target,
        )
        == backend.merge_rowcount
    )

    direct_parameters = next(
        parameters
        for _, statement, parameters in backend.statements
        if statement.startswith('INSERT INTO "dander_stage_')
        and "dander_stage_loads" not in statement
    )
    direct_row = direct_parameters[0]
    assert isinstance(direct_row, (tuple, list))
    assert isinstance(direct_row[1], bytes)
    assert len(direct_row[1]) > 4 * 1_024 * 1_024
    assert s3.region_checks == 0
    assert not s3.uploads and not s3.puts and not s3.deleted
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("max_rows", "max_logical_bytes"),
    ((2, 4_096), (10, 1)),
)
def test_redshift_direct_threshold_is_decided_for_the_complete_endpoint(
    tmp_path: Path,
    max_rows: int,
    max_logical_bytes: int,
) -> None:
    runtime, backend, s3 = _direct_redshift_runtime(
        tmp_path,
        max_rows=max_rows,
        max_logical_bytes=max_logical_bytes,
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    consumed = 0

    def records() -> Iterable[dict[str, object]]:
        nonlocal consumed
        for index in range(3):
            consumed += 1
            yield {"id": str(index), "label": f"record-{index}"}

    assert writer.supports_batched_writes is False
    assert writer.accepts_streaming_input is True
    assert writer.write(records(), _target(runtime, run_id="run-threshold")) == 1

    assert consumed == 3
    assert s3.region_checks == 1
    assert s3.uploads and s3.puts and s3.deleted
    sql = [statement for _, statement, _ in backend.statements]
    assert any(statement.startswith("COPY ") for statement in sql)
    assert not any(
        statement.startswith('INSERT INTO "dander_stage_') and "dander_stage_loads" not in statement
        for statement in sql
    )
    assert all(operation.transport is WriteTransport.COPY for operation in writer.drain_telemetry())
    assert not tuple(tmp_path.iterdir())


def test_redshift_direct_empty_stream_skips_s3_and_parameter_inserts(tmp_path: Path) -> None:
    runtime, backend, s3 = _direct_redshift_runtime(tmp_path)
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    assert writer.write((), _target(runtime, run_id="run-empty-direct")) == 0

    sql = [statement for _, statement, _ in backend.statements]
    assert not any(
        statement.startswith(("COPY ", 'INSERT INTO "dander_stage_')) for statement in sql
    )
    assert s3.region_checks == 0
    assert not s3.uploads and not s3.puts and not s3.deleted
    operations = writer.drain_telemetry()
    assert len(operations) == 1
    assert operations[0].transport is WriteTransport.DIRECT
    assert not tuple(tmp_path.iterdir())


def test_redshift_direct_failure_is_sanitized_and_uses_no_s3(tmp_path: Path) -> None:
    runtime, backend, s3 = _direct_redshift_runtime(tmp_path)
    backend.direct_error = True
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    with pytest.raises(RedshiftWriteError, match="staged SCD1 write failed") as raised:
        writer.write(({"id": "one", "label": "first"},), _target(runtime, run_id="run-bad"))

    assert "private Redshift" not in str(raised.value)
    assert s3.region_checks == 0
    assert not s3.uploads and not s3.puts and not s3.deleted
    assert writer.drain_telemetry() == ()
    assert backend.rollbacks >= 1
    assert not tuple(tmp_path.iterdir())


def test_redshift_telemetry_failure_rolls_back_before_staging_cleanup(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    backend.telemetry_error = True
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    assert writer.write(({"id": "one", "label": "first"},), _target(runtime)) == 1

    assert [operation.operation for operation in writer.drain_telemetry()] == [
        TelemetryOperation.LOAD,
        TelemetryOperation.QUERY,
    ]
    assert backend.rollbacks >= 2
    assert any(
        statement.startswith("DROP TABLE IF EXISTS") for _, statement, _ in backend.statements
    )


def test_redshift_history_enrichment_is_bounded_and_ignores_malformed_metrics() -> None:
    backend = _FakeRedshift(telemetry_rows=[("bad", -1)])
    connection = backend.connect()
    operations = tuple(
        OperationTelemetry(
            provider="redshift",
            operation=TelemetryOperation.QUERY,
            query_id=str(query_id),
        )
        for query_id in range(1_002)
    )

    assert enrich_operation_telemetry(connection, operations) == operations

    history_statement, history_parameters = next(
        (statement, parameters)
        for _, statement, parameters in backend.statements
        if statement.startswith("WITH recent AS (SELECT query_id")
    )
    assert len(history_parameters) == 1_000
    assert history_parameters[0] == 2
    assert history_parameters[-1] == 1_001
    assert "metrics_level = 'Step'" in history_statement
    assert backend.rollbacks == 1


@pytest.mark.parametrize(
    ("mode", "field_name", "expected_sql"),
    [
        (
            WriteMode.INCREMENTAL,
            "updated_at",
            '"updated_at" < "records"."updated_at"',
        ),
        (WriteMode.SNAPSHOT, "snapshot_at", "SELECT DISTINCT"),
        (WriteMode.SCD2, None, '"valid_to" = SYSDATE'),
        (WriteMode.REPLACE, None, 'DELETE FROM "raw"."records" WHERE NOT EXISTS'),
    ],
)
def test_redshift_factory_reaches_each_additional_fenced_write_mode(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
    mode: WriteMode,
    field_name: str | None,
    expected_sql: str,
) -> None:
    runtime, backend, _s3, staging_root = redshift_runtime
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=mode,
        cursor_field=field_name if mode is WriteMode.INCREMENTAL else None,
        snapshot_field=field_name if mode is WriteMode.SNAPSHOT else None,
    )
    assert writer.mode is mode
    assert writer.supports_batched_writes is (mode in {WriteMode.INCREMENTAL, WriteMode.SNAPSHOT})
    assert writer.accepts_streaming_input is (mode in {WriteMode.SCD2, WriteMode.REPLACE})
    relation = RelationRef(catalog="analytics", namespace="raw", name="records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=f"redshift_{mode.value}",
            run_id=f"run-{mode.value}",
            token=5,
            authority_id="postgresql:test-state",
        ),
    )
    schema = [
        WriteField(name="id", data_type="STRING"),
        WriteField(name="label", data_type="STRING"),
    ]
    record: dict[str, object] = {"id": "one", "label": "first"}
    if field_name is not None:
        schema.append(WriteField(name=field_name, data_type="INT64"))
        record[field_name] = 2
        backend.schema_rows.append((field_name, "bigint", None, None, None, "YES"))
    if mode is WriteMode.SCD2:
        backend.schema_rows = []
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=tuple(schema),
        publication_fence=publication,
    )

    assert writer.write((record,), target) == backend.merge_rowcount

    sql = [statement for _, statement, _ in backend.statements]
    assert any(expected_sql in statement for statement in sql)
    if mode is WriteMode.INCREMENTAL:
        merge = next(statement for statement in sql if statement.startswith("MERGE INTO"))
        prune = next(statement for statement in sql if statement.startswith("DELETE FROM"))
        assert " USING " in prune
        assert "WHEN MATCHED AND" not in merge
        assert 'ORDER BY "updated_at" DESC NULLS LAST, "_dander_ordinal" DESC' in merge
    if mode is WriteMode.SNAPSHOT:
        snapshot = next(
            statement
            for statement in sql
            if statement.startswith('INSERT INTO "raw"."records"')
            and "SELECT DISTINCT" in statement
        )
        assert 'existing."label" IS NULL AND incoming."label" IS NULL' in snapshot
    if mode is WriteMode.SCD2:
        create = next(
            statement
            for statement in sql
            if statement.startswith('CREATE TABLE IF NOT EXISTS "raw"."records"')
        )
        assert '"valid_from" TIMESTAMP NOT NULL' in create
        assert '"valid_to" TIMESTAMP' in create
        assert '"is_current" BOOLEAN NOT NULL' in create
        close = next(
            statement
            for statement in sql
            if statement.startswith('UPDATE "raw"."records" AS current')
        )
        insert = next(
            statement
            for statement in sql
            if statement.startswith('INSERT INTO "raw"."records"') and '"valid_from"' in statement
        )
        assert "SYSDATE" in close and "SYSDATE" in insert
        assert 'ORDER BY "_dander_ordinal" DESC' in close
    assert not tuple(staging_root.iterdir())


@pytest.mark.parametrize("mode", tuple(WriteMode))
def test_redshift_direct_transport_reaches_every_fenced_write_mode(
    tmp_path: Path,
    mode: WriteMode,
) -> None:
    runtime, backend, s3 = _direct_redshift_runtime(tmp_path)
    cursor_field = "updated_at" if mode is WriteMode.INCREMENTAL else None
    snapshot_field = "snapshot_at" if mode is WriteMode.SNAPSHOT else None
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=mode,
        cursor_field=cursor_field,
        snapshot_field=snapshot_field,
    )
    relation = RelationRef(catalog="analytics", namespace="raw", name=f"direct_{mode.value}")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=f"redshift_direct_{mode.value}",
            run_id=f"run-direct-{mode.value}",
            token=9,
            authority_id="postgresql:test-state",
        ),
    )
    schema = [
        WriteField(name="id", data_type="STRING"),
        WriteField(name="label", data_type="STRING"),
    ]
    record: dict[str, object] = {"id": "one", "label": "first"}
    if cursor_field is not None:
        schema.append(WriteField(name=cursor_field, data_type="INT64"))
        record[cursor_field] = 2
        backend.schema_rows.append((cursor_field, "bigint", None, None, None, "YES"))
    if snapshot_field is not None:
        schema.append(WriteField(name=snapshot_field, data_type="INT64"))
        record[snapshot_field] = 2
        backend.schema_rows.append((snapshot_field, "bigint", None, None, None, "YES"))
    if mode is WriteMode.SCD2:
        backend.schema_rows = []
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=tuple(schema),
        publication_fence=publication,
    )

    assert writer.write((record,), target) == backend.merge_rowcount

    sql = [statement for _, statement, _ in backend.statements]
    assert any(statement.startswith('INSERT INTO "dander_stage_') for statement in sql)
    assert not any(statement.startswith("COPY ") for statement in sql)
    assert s3.region_checks == 0
    assert not s3.uploads and not s3.puts and not s3.deleted
    assert all(
        operation.transport is WriteTransport.DIRECT for operation in writer.drain_telemetry()
    )
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("mode", tuple(WriteMode))
def test_redshift_all_write_modes_parse_explicit_super_from_varbyte_staging(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
    mode: WriteMode,
) -> None:
    runtime, backend, _s3, staging_root = redshift_runtime
    cursor_field = "updated_at" if mode is WriteMode.INCREMENTAL else None
    snapshot_field = "snapshot_at" if mode is WriteMode.SNAPSHOT else None
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=mode,
        cursor_field=cursor_field,
        snapshot_field=snapshot_field,
    )
    relation = RelationRef(catalog="analytics", namespace="raw", name="super_records")
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=f"redshift_super_{mode.value}",
            run_id=f"run-super-{mode.value}",
            token=11,
            authority_id="postgresql:test-state",
        ),
    )
    fallback = ProviderExtension(provider="redshift", name="fallback", value="super")
    schema = [
        WriteField(name="id", data_type="STRING"),
        WriteField(name="payload", data_type="JSON", extensions=(fallback,)),
    ]
    record: dict[str, object] = {
        "id": "one",
        "payload": {"nested": {"ready": True}},
    }
    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "NO"),
        ("payload", "super", None, None, None, "YES"),
    ]
    if cursor_field is not None:
        schema.append(WriteField(name=cursor_field, data_type="INT64"))
        record[cursor_field] = 2
        backend.schema_rows.append((cursor_field, "bigint", None, None, None, "YES"))
    if snapshot_field is not None:
        schema.append(WriteField(name=snapshot_field, data_type="INT64"))
        record[snapshot_field] = 2
        backend.schema_rows.append((snapshot_field, "bigint", None, None, None, "YES"))
    if mode is WriteMode.SCD2:
        backend.schema_rows = []
    target = WriteTarget(
        relation=relation,
        business_key=("id",),
        schema=tuple(schema),
        publication_fence=publication,
    )

    assert writer.write((record,), target) == backend.merge_rowcount

    sql = [statement for _, statement, _ in backend.statements]
    temporary = next(statement for statement in sql if statement.startswith("CREATE TEMP TABLE"))
    target_ddl = next(
        statement
        for statement in sql
        if statement.startswith('CREATE TABLE IF NOT EXISTS "raw"."super_records"')
    )
    assert '"payload" VARBYTE(16777216)' in temporary
    assert '"payload" SUPER' in target_ddl
    assert any('JSON_PARSE(staged."payload") AS "payload"' in statement for statement in sql)
    assert not tuple(staging_root.iterdir())


def test_redshift_transform_accepts_explicit_native_super_column(
    tmp_path: Path,
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    _write_redshift_super_model(tmp_path)
    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "YES"),
        ("payload", "super", None, None, None, "YES"),
    ]
    backend.transform_source_rows = [{"id": "one", "payload": {"nested": True}}]
    runner = runtime.transforms.build_transform_runner(
        graph_plan=None,
        build_models=True,
        raw_namespace="raw",
    )
    assert isinstance(runner, RedshiftTransformRunner)

    result = runner.build(tmp_path, ownership=_ownership("run-super-model", 12))

    assert result.models == ("super_model",)
    target_ddl = next(
        statement
        for _, statement, _ in backend.statements
        if statement.startswith('CREATE TABLE IF NOT EXISTS "analytics"."super_model"')
    )
    assert '"payload" SUPER' in target_ddl


def test_redshift_super_staging_accepts_json_larger_than_varchar_limit(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, s3, _root = redshift_runtime
    backend.schema_rows = [
        ("id", "character varying", 65_535, None, None, "NO"),
        ("payload", "super", None, None, None, "YES"),
    ]
    fallback = ProviderExtension(provider="redshift", name="fallback", value="super")
    target = _target(runtime, run_id="run-super-boundary")
    target = WriteTarget(
        relation=target.relation_ref,
        business_key=target.business_key,
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="payload", data_type="JSON", extensions=(fallback,)),
        ),
        publication_fence=target.publication_fence,
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    assert writer.write(({"id": "one", "payload": {"blob": "x" * 70_000}},), target) == 1
    assert s3.uploads


@pytest.mark.parametrize("payload", [float("nan"), {1: "coerced-key"}])
def test_redshift_super_rejects_non_strict_json_before_upload(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
    payload: object,
) -> None:
    runtime, _backend, s3, _root = redshift_runtime
    fallback = ProviderExtension(provider="redshift", name="fallback", value="super")
    target = _target(runtime, run_id="run-invalid-super")
    target = WriteTarget(
        relation=target.relation_ref,
        business_key=target.business_key,
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="payload", data_type="JSON", extensions=(fallback,)),
        ),
        publication_fence=target.publication_fence,
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    with pytest.raises(RedshiftWriteError, match="invalid JSON|non-string JSON key"):
        writer.write(({"id": "one", "payload": payload},), target)

    assert not s3.uploads and not s3.puts


def test_redshift_super_cannot_be_a_business_key(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, _backend, s3, _root = redshift_runtime
    fallback = ProviderExtension(provider="redshift", name="fallback", value="super")
    target = _target(runtime, run_id="run-super-key")
    target = WriteTarget(
        relation=target.relation_ref,
        business_key=("payload",),
        schema=(WriteField(name="payload", data_type="JSON", extensions=(fallback,)),),
        publication_fence=target.publication_fence,
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    with pytest.raises(RedshiftWriteError, match="cannot be a key, cursor, or snapshot"):
        writer.write(({"payload": {"id": "one"}},), target)

    assert not s3.uploads and not s3.puts


def test_redshift_replace_replay_is_guarded_by_the_complete_manifest(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=WriteMode.REPLACE,
    )
    target = _target(runtime, run_id="run-replace-replay")
    original = ({"id": "one", "label": "first"},)

    assert writer.write(original, target) == 1
    assert writer.write(original, target) == 0
    assert writer.write(({"id": "two", "label": "changed"},), target) == 1

    mutations = [
        (statement, parameters)
        for _, statement, parameters in backend.statements
        if statement.startswith(("DELETE FROM", "INSERT INTO"))
        and "dander_stage_loads" not in statement
        and len(parameters) == 5
    ]
    assert all("NOT EXISTS" in statement for statement, _ in mutations)
    assert all(len(parameters) == 5 for _, parameters in mutations)
    history_identities = [
        parameters[:5]
        for _, statement, parameters in backend.statements
        if statement.startswith("INSERT INTO") and "dander_stage_loads" in statement
    ]
    assert history_identities[0] == history_identities[1]
    assert history_identities[-1] != history_identities[1]


def test_redshift_empty_replace_is_fenced_and_replay_safe(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, s3, _root = redshift_runtime
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=WriteMode.REPLACE,
    )
    target = _target(runtime, run_id="run-empty-replace")

    assert writer.write((), target) == 1
    assert writer.write((), target) == 0
    assert not s3.uploads and not s3.puts
    delete = next(
        (statement, parameters)
        for _, statement, parameters in backend.statements
        if statement.startswith('DELETE FROM "raw"."records"')
    )
    assert "NOT EXISTS" in delete[0]
    assert len(delete[1]) == 5


def test_redshift_mode_specific_fields_fail_before_staging(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, s3, _root = redshift_runtime
    connections = backend.connections
    with pytest.raises(ValueError, match="incremental writes require cursor_field"):
        runtime.writers.build_ingestion_writer(
            sandbox=False,
            batch_rows=1,
            schema_evolution=SchemaEvolution.ADDITIVE,
            mode=WriteMode.INCREMENTAL,
        )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
        mode=WriteMode.SNAPSHOT,
        snapshot_field="snapshot_at",
    )
    target = _target(runtime, run_id="run-null-snapshot")
    target = WriteTarget(
        relation=target.relation_ref,
        business_key=target.business_key,
        schema=(*target.schema, WriteField(name="snapshot_at", data_type="INT64")),
        publication_fence=target.publication_fence,
    )
    with pytest.raises(RedshiftWriteError, match="null snapshot value"):
        writer.write(({"id": "one", "label": "first", "snapshot_at": None},), target)
    assert not s3.uploads and not s3.puts
    assert backend.connections == connections + 1  # target-fence claim only


def test_redshift_retry_identity_excludes_random_local_staging_id(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, _s3, _root = redshift_runtime
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    target = _target(runtime, run_id="run-retry")
    row = ({"id": "one", "label": "same"},)

    assert writer.write(row, target) == 1
    assert writer.write(row, target) == 0

    history_parameters = [
        parameters[:5]
        for _, statement, parameters in backend.statements
        if statement.startswith("INSERT INTO") and "dander_stage_loads" in statement
    ]
    assert len(history_parameters) == 2
    assert history_parameters[0] == history_parameters[1]
    assert len(backend.history) == 1


def test_redshift_lost_publication_rolls_back_and_removes_owned_staging(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, s3, staging_root = redshift_runtime
    target = _target(runtime, run_id="run-stale")
    backend.fence_touch_rowcount = 0
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    rollback_before = backend.rollbacks

    with pytest.raises(TargetFenceLostError, match="lost before publication"):
        writer.write(({"id": "one", "label": "blocked"},), target)

    # Query-ID capture ends its telemetry-only transaction before the failed fence rolls back.
    assert backend.rollbacks == rollback_before + 2
    assert s3.deleted and len(s3.deleted[-1]) == 2
    assert not tuple(staging_root.iterdir())
    assert not any(statement.startswith("MERGE INTO") for _, statement, _ in backend.statements)


def test_redshift_ambiguous_upload_failure_still_deletes_the_owned_key(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, _backend, s3, staging_root = redshift_runtime
    target = _target(runtime, run_id="run-upload-failure")
    s3.fail_upload_at = 1
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )

    with pytest.raises(RedshiftWriteError, match="staged SCD1 write failed"):
        writer.write(({"id": "one", "label": "ambiguous"},), target)

    assert len(s3.deleted) == 1
    assert s3.deleted[0] == (s3.uploads[0][1],)
    assert not tuple(staging_root.iterdir())


def test_redshift_s3_cleanup_batches_the_service_limit() -> None:
    s3 = _FakeS3()
    keys = tuple(f"dander/staging/owned-{index}" for index in range(1_001))

    _delete_owned(s3, "dander-redshift-staging", keys)

    assert tuple(map(len, s3.deleted)) == (1_000, 1)
    assert s3.deleted[0][0] == keys[0]
    assert s3.deleted[1][0] == keys[-1]


def test_redshift_rejects_oversized_single_row_before_s3_or_database_mutation(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, backend, s3, _root = redshift_runtime
    target = _target(runtime, run_id="run-large")
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=1,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    connections_before = backend.connections

    with pytest.raises(RedshiftWriteError, match="4 MB COPY limit"):
        writer.write(({"id": "one", "label": "x" * 4_300_000},), target)

    assert s3.region_checks == 0
    assert not s3.uploads and not s3.puts
    assert backend.connections == connections_before


def test_redshift_rejects_oversized_row_inside_multirow_artifact_before_mutation(
    tmp_path: Path,
) -> None:
    backend = _FakeRedshift()
    s3 = _FakeS3()
    raw_config = _config()
    raw_config["max_logical_bytes_per_file"] = 8 * 1_024 * 1_024
    registry = default_provider_registry()
    config = registry.parse(ProviderKind.WAREHOUSE, raw_config)
    runtime = registry.build(
        ProviderKind.WAREHOUSE,
        config,
        context={
            "catalog": "analytics",
            "connection_factory": backend.connect,
            "s3_client": s3,
            "staging_root": tmp_path,
        },
    )
    assert isinstance(runtime, WarehouseRuntime)
    target = _target(runtime, run_id="run-large-multirow")
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=2,
        schema_evolution=SchemaEvolution.ADDITIVE,
    )
    connections_before = backend.connections

    with pytest.raises(RedshiftWriteError, match="4 MB COPY limit"):
        writer.write(
            (
                {"id": "small", "label": "ok"},
                {"id": "large", "label": "x" * 4_300_000},
            ),
            target,
        )

    assert s3.region_checks == 0
    assert not s3.uploads and not s3.puts
    assert backend.connections == connections_before
    assert not tuple(tmp_path.iterdir())


def test_redshift_connection_validation_errors_are_sanitized(tmp_path: Path) -> None:
    class _BrokenConnectionFactory:
        def __call__(self) -> object:
            raise RuntimeError("private AWS response")

    registry = default_provider_registry()
    config = registry.parse(ProviderKind.WAREHOUSE, _config())

    with pytest.raises(ProviderFactoryError, match="connection validation failed") as raised:
        registry.build(
            ProviderKind.WAREHOUSE,
            config,
            context={
                "catalog": "analytics",
                "connection_factory": _BrokenConnectionFactory(),
                "s3_client": _FakeS3(),
                "staging_root": tmp_path,
            },
        )
    assert "private AWS response" not in str(raised.value)
