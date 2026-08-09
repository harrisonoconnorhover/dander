"""Redshift registration and stateful Parquet/COPY SCD1 conformance."""

# ruff: noqa: N803 -- boto3's public S3 API uses capitalized parameter names.

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Self

import pytest

from dander.concurrency import FencingToken, TargetFenceLostError
from dander.providers import ProviderFactoryError, ProviderKind, default_provider_registry
from dander.providers.redshift import RedshiftWarehouseConfig
from dander.providers.redshift.transform import RedshiftTransformRunner
from dander.providers.redshift.writer import RedshiftWriteError, _delete_owned
from dander.transform import SqlDialect, TransformProject, TransformProjectError, TransformRunError
from dander.warehouse import RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteMode, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


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

    def connect(self) -> _FakeConnection:
        self.connections += 1
        return _FakeConnection(self, self.connections)


class _FakeConnection:
    def __init__(self, backend: _FakeRedshift, connection_id: int) -> None:
        self.backend = backend
        self.connection_id = connection_id
        self.autocommit = True
        self.in_transaction = False
        self.temporary_rows: list[dict[str, object]] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.in_transaction = False
        self.backend.commits += 1

    def rollback(self) -> None:
        self.in_transaction = False
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
        parameters = tuple(args or ())
        compact = " ".join(command.split())
        if compact == "BEGIN" and self.connection.in_transaction:
            raise AssertionError("Redshift writer attempted to nest a transaction")
        self.connection.in_transaction = True
        self.backend.statements.append((self.connection.connection_id, compact, parameters))
        if compact == "SELECT current_database(), current_user":
            self._row = ("analytics", "dander_user")
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
        elif compact.startswith("MERGE INTO"):
            self.rowcount = (
                0 if parameters[:5] in self.backend.history else self.backend.merge_rowcount
            )
        elif compact.startswith("SELECT COUNT"):
            self._row = (self.backend.assertion_failure_count,)
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
    return runtime, backend, s3, tmp_path


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
    assert runtime.capabilities.write_modes == frozenset({WriteMode.SCD1})
    assert runtime.capabilities.transports == frozenset({WriteTransport.COPY})
    assert runtime.capabilities.supports_transforms is True
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
    with pytest.raises(ValueError, match="graph execution is not available"):
        runtime.transforms.build_transform_runner(graph_plan=object(), build_models=False)
    assert runtime.transforms.build_transform_runner(graph_plan=None, build_models=False) is None


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
    assert backend.rollbacks == rollbacks + 1
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

    assert backend.rollbacks == rollback_before + 1
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
