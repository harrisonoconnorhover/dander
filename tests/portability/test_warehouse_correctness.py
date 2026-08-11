"""Credential-free contract checks for the four-warehouse correctness gate."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from scripts.benchmarks import warehouse_correctness as correctness

from dander.concurrency import TargetFence
from dander.providers.bigquery.runtime import BIGQUERY_SCHEMA_SUPPORT, BigQuerySchemaMapper
from dander.providers.postgresql.runtime import (
    POSTGRESQL_SCHEMA_SUPPORT,
    PostgreSQLSchemaMapper,
)
from dander.providers.redshift.runtime import REDSHIFT_SCHEMA_SUPPORT, RedshiftSchemaMapper
from dander.providers.snowflake.runtime import SNOWFLAKE_SCHEMA_SUPPORT, SnowflakeSchemaMapper
from dander.warehouse import LogicalTypeKind, RelationRef
from dander.warehouse.runtime import WarehouseCapabilities, WarehouseRuntime
from dander.writer import WriteMode, WritePattern, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path
    from typing import Any

    from dander.concurrency import FencingToken
    from dander.warehouse import RelationSchema
    from dander.warehouse.runtime import PreparedWarehouseStatement
    from dander.writer import SchemaEvolution, WriteTarget


class _SchemaMapper:
    def canonical_schema(self, fields: Sequence[object]) -> RelationSchema:
        del fields
        return correctness.COMMON_SCHEMA


class _Codec:
    provider_id = "bigquery"

    def render(self, relation: RelationRef) -> str:
        return ".".join(relation.coordinates)


class _Fence:
    def claim(self, target: RelationRef, fence: FencingToken) -> TargetFence:
        return TargetFence(
            fence_table=f"{target.namespace}.dander_target_commits",
            target_id=".".join(target.coordinates),
            authority_id=fence.resolved_authority_id,
            authority_epoch=fence.authority_epoch,
            pipeline_id=fence.pipeline_id,
            run_id=fence.run_id,
            token=fence.token,
        )

    def prepare_dml(self, statement: str, fence: TargetFence) -> PreparedWarehouseStatement:
        del statement, fence
        raise AssertionError("fake writer does not prepare SQL")


class _Writer(WritePattern):
    mode = WriteMode.SCD1
    requires_publication_fence = True

    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows

    def write(
        self,
        records: Iterable[Mapping[str, object]],
        target: WriteTarget,
    ) -> int:
        assert target.publication_fence is not None
        count = 0
        for record in records:
            row = dict(record)
            self.rows[cast("str", row["id"])] = row
            count += 1
        return count


class _Writers:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows

    def build_ingestion_writer(
        self,
        *,
        sandbox: bool,
        batch_rows: int,
        schema_evolution: SchemaEvolution,
        mode: WriteMode = WriteMode.SCD1,
        cursor_field: str | None = None,
        snapshot_field: str | None = None,
    ) -> WritePattern:
        del batch_rows, schema_evolution, cursor_field, snapshot_field
        assert sandbox is False
        assert mode is WriteMode.SCD1
        return _Writer(self.rows)


def _evidence(provider: str) -> correctness.WarehouseCorrectnessEvidence:
    return correctness.WarehouseCorrectnessEvidence(
        schema="io.dander.conformance.warehouse-correctness/v1",
        fixture_version="warehouse-common-scalar-v1",
        provider=provider,
        candidate_commit="a" * 40,
        dander_version="test",
        started_at_utc="2026-08-10T00:00:00Z",
        ended_at_utc="2026-08-10T00:00:01Z",
        fixture_hash="b" * 64,
        canonical_schema_hash="c" * 64,
        normalized_rows_hash="d" * 64,
        normalized_row_count=3,
        write_mode="scd1",
        transport="direct" if provider in {"snowflake", "redshift"} else "copy",
        replay_equal=True,
        cleanup_verified=True,
        approved_cost_ceiling_usd="1.00",
        cost_approval_reference="review/phase5-conformance",
    )


def test_fixture_uses_only_the_declared_common_scalar_intersection() -> None:
    supports = (
        BIGQUERY_SCHEMA_SUPPORT,
        POSTGRESQL_SCHEMA_SUPPORT,
        SNOWFLAKE_SCHEMA_SUPPORT,
        REDSHIFT_SCHEMA_SUPPORT,
    )

    assert {field.data_type.kind for field in correctness.COMMON_SCHEMA.fields} == {
        LogicalTypeKind.BOOLEAN,
        LogicalTypeKind.INTEGER,
        LogicalTypeKind.DECIMAL,
        LogicalTypeKind.FLOAT,
        LogicalTypeKind.STRING,
        LogicalTypeKind.BINARY,
        LogicalTypeKind.DATE,
        LogicalTypeKind.TIME,
        LogicalTypeKind.TIMESTAMP,
    }
    for support in supports:
        assert support.require(correctness.COMMON_SCHEMA) is correctness.COMMON_SCHEMA

    assert (
        correctness._without_extensions(
            BigQuerySchemaMapper().canonical_schema(correctness.LEGACY_BIGQUERY_FIELDS)
        )
        == correctness.COMMON_SCHEMA
    )
    for mapper in (PostgreSQLSchemaMapper(), SnowflakeSchemaMapper(), RedshiftSchemaMapper()):
        assert (
            mapper.canonical_schema(correctness.COMMON_SCHEMA.fields) == correctness.COMMON_SCHEMA
        )


def test_normalization_preserves_canonical_time_binary_decimal_and_unicode_semantics() -> None:
    rows = [dict(correctness._UPDATE_ROWS[0])]
    rows[0]["payload"] = memoryview(b"updated")
    rows[0]["label"] = "cafe\N{COMBINING ACUTE ACCENT}"
    rows[0]["observed_at"] = datetime(
        2026,
        3,
        4,
        4,
        10,
        11,
        654321,
        tzinfo=timezone(-timedelta(hours=5)),
    )

    normalized = correctness.normalized_rows(rows)[0]

    assert normalized["amount"] == "12.500000000"
    assert normalized["payload"] == "dXBkYXRlZA=="
    assert normalized["label"] == "caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    assert normalized["observed_at"] == "2026-03-04T09:10:11.654321Z"
    assert normalized["local_at"] == "2026-03-04T09:10:11.654321"


def test_shared_runner_uses_runtime_contract_replays_and_cleans_without_row_evidence() -> None:
    rows: dict[str, dict[str, object]] = {}
    cleaned = False
    schema_support = BIGQUERY_SCHEMA_SUPPORT
    capabilities = WarehouseCapabilities(
        provider_id="bigquery",
        schema_contract_version=1,
        write_modes=frozenset({WriteMode.SCD1}),
        transports=frozenset({WriteTransport.LOAD_JOB}),
        supports_transforms=False,
        supports_graphs=False,
        supports_target_fencing=True,
        schema_support=schema_support,
    )
    runtime = WarehouseRuntime(
        provider_id="bigquery",
        relation_codec=_Codec(),
        schema_mapper=_SchemaMapper(),
        writers=_Writers(rows),
        transforms=cast("Any", SimpleNamespace()),
        target_fence=_Fence(),
        telemetry=cast("Any", SimpleNamespace()),
        capabilities=capabilities,
    )

    def cleanup() -> None:
        nonlocal cleaned
        rows.clear()
        cleaned = True

    session = correctness._WarehouseSession(
        provider="bigquery",
        runtime=runtime,
        relation=RelationRef(catalog="project", namespace="raw", name="records"),
        schema_inputs=correctness.COMMON_SCHEMA.fields,
        transport=WriteTransport.LOAD_JOB,
        read_rows=lambda: tuple(rows.values()),
        cleanup=cleanup,
        cleanup_verified=lambda: cleaned and not rows,
        close=lambda: None,
    )

    evidence = correctness._run_session(
        session,
        candidate_commit="a" * 40,
        cost_ceiling=correctness.ApprovedCostCeiling("1.00", "review/phase5-conformance"),
        started_at_utc="2026-08-10T00:00:00Z",
    )
    payload = evidence.to_json()

    assert evidence.normalized_row_count == 3
    assert evidence.replay_equal is True
    assert evidence.cleanup_verified is True
    assert "alpha" not in payload
    assert "café" not in payload
    assert "payload" not in payload


def test_shared_runner_cleans_when_publication_fence_fails() -> None:
    rows: dict[str, dict[str, object]] = {}
    cleaned = False

    class _FailingFence(_Fence):
        def claim(self, target: RelationRef, fence: FencingToken) -> TargetFence:
            del target, fence
            raise RuntimeError("provider detail must not enter failure evidence")

    runtime = WarehouseRuntime(
        provider_id="bigquery",
        relation_codec=_Codec(),
        schema_mapper=_SchemaMapper(),
        writers=_Writers(rows),
        transforms=cast("Any", SimpleNamespace()),
        target_fence=_FailingFence(),
        telemetry=cast("Any", SimpleNamespace()),
        capabilities=WarehouseCapabilities(
            provider_id="bigquery",
            schema_contract_version=1,
            write_modes=frozenset({WriteMode.SCD1}),
            transports=frozenset({WriteTransport.LOAD_JOB}),
            supports_transforms=False,
            supports_graphs=False,
            supports_target_fencing=True,
            schema_support=BIGQUERY_SCHEMA_SUPPORT,
        ),
    )

    def cleanup() -> None:
        nonlocal cleaned
        cleaned = True

    session = correctness._WarehouseSession(
        provider="bigquery",
        runtime=runtime,
        relation=RelationRef(catalog="project", namespace="raw", name="records"),
        schema_inputs=correctness.COMMON_SCHEMA.fields,
        transport=WriteTransport.LOAD_JOB,
        read_rows=lambda: (),
        cleanup=cleanup,
        cleanup_verified=lambda: cleaned,
        close=lambda: None,
    )

    with pytest.raises(correctness._WarehouseCorrectnessRunError) as captured:
        correctness._run_session(
            session,
            candidate_commit="a" * 40,
            cost_ceiling=correctness.ApprovedCostCeiling("1.00", "review/phase5-conformance"),
            started_at_utc="2026-08-10T00:00:00Z",
        )

    evidence = captured.value.evidence
    assert evidence.stage == "fence"
    assert evidence.primary_error_types == ("RuntimeError",)
    assert evidence.cleanup_attempted is True
    assert evidence.cleanup_verified is True
    assert "provider detail" not in evidence.to_json()


def test_bigquery_metadata_queries_allow_the_minimum_billable_partition() -> None:
    assert correctness._BIGQUERY_MAXIMUM_BYTES_BILLED == 10 * 1_024 * 1_024


def test_redshift_readback_projects_binary_through_strict_base64() -> None:
    projection = correctness._redshift_read_projection(correctness.COMMON_SCHEMA)
    values: list[object] = [None] * len(correctness.COMMON_SCHEMA.fields)
    payload_index = next(
        index
        for index, field in enumerate(correctness.COMMON_SCHEMA.fields)
        if field.name == "payload"
    )
    values[payload_index] = "AP8="

    rows = correctness._decode_redshift_read_rows((tuple(values),))

    assert 'FROM_VARBYTE("payload", \'base64\') AS "payload"' in projection
    assert projection.count("FROM_VARBYTE(") == 1
    assert rows[0]["payload"] == b"\x00\xff"

    values[payload_index] = "not-base64!"
    with pytest.raises(correctness.WarehouseCorrectnessError, match="not valid base64"):
        correctness._decode_redshift_read_rows((tuple(values),))

    values[payload_index] = b"already-bytes"
    with pytest.raises(correctness.WarehouseCorrectnessError, match="wrong type"):
        correctness._decode_redshift_read_rows((tuple(values),))


def test_comparison_requires_exact_four_provider_hash_and_candidate_equality() -> None:
    evidence = tuple(_evidence(provider) for provider in sorted(correctness._PROVIDERS))

    comparison = correctness.compare_evidence(evidence)

    assert comparison.providers == ("bigquery", "postgresql", "redshift", "snowflake")
    assert comparison.all_rows_equal is True
    assert comparison.all_cleanup_verified is True
    with pytest.raises(correctness.WarehouseCorrectnessError, match="normalized_rows_hash"):
        correctness.compare_evidence(
            (*evidence[:-1], replace(evidence[-1], normalized_rows_hash="e" * 64))
        )
    with pytest.raises(correctness.WarehouseCorrectnessError, match="one unique"):
        correctness.compare_evidence(evidence[:-1])


def test_cost_ceiling_and_evidence_shape_fail_closed() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        correctness.ApprovedCostCeiling("-0.01", "review/phase5-conformance")
    with pytest.raises(ValueError, match="approval reference"):
        correctness.ApprovedCostCeiling("1.00", "not a stable reference")

    payload = json.loads(_evidence("bigquery").to_json())
    payload["rows"] = [{"id": "must-not-be-accepted"}]
    with pytest.raises(ValueError, match="invalid shape"):
        correctness.WarehouseCorrectnessEvidence.from_mapping(payload)


def test_compare_cli_writes_only_sanitized_equal_result_evidence(tmp_path: Path) -> None:
    paths: list[Path] = []
    for provider in sorted(correctness._PROVIDERS):
        path = tmp_path / f"{provider}.json"
        path.write_text(f"{_evidence(provider).to_json()}\n", encoding="utf-8")
        paths.append(path)
    output = tmp_path / "comparison.json"
    arguments = ["compare"]
    for path in paths:
        arguments.extend(("--evidence", str(path)))
    arguments.extend(("--output", str(output)))

    assert correctness.main(arguments) == 0
    payload = output.read_text(encoding="utf-8")
    assert '"all_rows_equal":true' in payload
    assert '"providers":["bigquery","postgresql","redshift","snowflake"]' in payload
    assert "alpha" not in payload
    assert "café" not in payload


def test_run_cli_writes_sanitized_failure_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text('{"provider":"bigquery"}\n', encoding="utf-8")
    output = tmp_path / "failure.json"

    def fail_open(_profile: Mapping[str, object]) -> correctness._WarehouseSession:
        raise RuntimeError("credential-and-provider-detail-must-not-escape")

    monkeypatch.setattr(correctness, "_open_session", fail_open)

    assert (
        correctness.main(
            [
                "run",
                "--profile-json",
                str(profile),
                "--candidate-commit",
                "a" * 40,
                "--approved-cost-ceiling-usd",
                "1.00",
                "--cost-approval-reference",
                "review/phase5-conformance",
                "--output",
                str(output),
            ]
        )
        == 1
    )
    payload = output.read_text(encoding="utf-8")
    record = json.loads(payload)
    assert record["schema"] == "io.dander.conformance.warehouse-correctness-failure/v1"
    assert record["stage"] == "open_session"
    assert record["primary_error_types"] == ["RuntimeError"]
    assert record["cleanup_attempted"] is False
    assert "credential" not in payload
