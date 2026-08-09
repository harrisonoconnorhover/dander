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
from dander.providers.redshift.writer import RedshiftWriteError, _delete_owned
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

    def connect(self) -> _FakeConnection:
        self.connections += 1
        return _FakeConnection(self, self.connections)


class _FakeConnection:
    def __init__(self, backend: _FakeRedshift, connection_id: int) -> None:
        self.backend = backend
        self.connection_id = connection_id
        self.autocommit = True
        self.in_transaction = False

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
        elif compact.startswith("INSERT INTO") and "dander_stage_loads" in compact:
            self.rowcount = 0 if parameters[:5] in self.backend.history else 1
            self.backend.history.add(parameters[:5])
        elif compact.startswith("MERGE INTO"):
            self.rowcount = (
                0 if parameters[:5] in self.backend.history else self.backend.merge_rowcount
            )
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
        default_namespace="ignored_bigquery_default",
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


def test_redshift_runtime_validates_connection_and_fails_closed_for_transforms(
    redshift_runtime: tuple[WarehouseRuntime, _FakeRedshift, _FakeS3, Path],
) -> None:
    runtime, _backend, _s3, _root = redshift_runtime
    relation = RelationRef(catalog="analytics", namespace="raw", name="records")

    assert runtime.relation_codec.render(relation) == '"analytics"."raw"."records"'
    assert runtime.capabilities.write_modes == frozenset({WriteMode.SCD1})
    assert runtime.capabilities.transports == frozenset({WriteTransport.COPY})
    assert runtime.capabilities.supports_transforms is False
    with pytest.raises(ValueError, match="transforms are not available"):
        runtime.transforms.build_transform_runner(graph_plan=None, build_models=True)


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
