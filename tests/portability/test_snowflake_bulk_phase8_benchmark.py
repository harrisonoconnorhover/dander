"""Credential-free checks for the Phase 8 Snowflake scale harness."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from scripts.benchmarks import snowflake_bulk_phase8 as bulk

from dander import __version__
from dander.concurrency import TargetFenceLostError
from dander.qualification import (
    ApprovedCostCeiling,
    ApprovedObjectiveSet,
    BenchmarkClass,
    ObjectiveStatus,
    QualificationStatus,
)

if TYPE_CHECKING:
    from dander.concurrency import FencingToken
    from dander.telemetry import OperationTelemetry
    from dander.warehouse import RelationRef, WarehouseRuntime

_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-thread-phase8-snowflake-bulk"


def _config(**overrides: object) -> bulk.SnowflakeBulkConfig:
    values: dict[str, object] = {
        "account": "org-account",
        "user": "DANDER_USER",
        "database": "DANDER_TEST",
        "warehouse": "DANDER_WH",
        "role": "DANDER_ROLE",
    }
    values.update(overrides)
    return bulk.SnowflakeBulkConfig(**values)  # type: ignore[arg-type]


def _incremental_config(**overrides: object) -> bulk.SnowflakeIncrementalConfig:
    values: dict[str, object] = {
        "account": "org-account",
        "user": "DANDER_USER",
        "database": "DANDER_INCREMENTAL_TEST",
        "warehouse": "DANDER_INCREMENTAL_WH",
        "role": "DANDER_INCREMENTAL_ROLE",
    }
    values.update(overrides)
    return bulk.SnowflakeIncrementalConfig(**values)  # type: ignore[arg-type]


def _concurrency_config(**overrides: object) -> bulk.SnowflakeConcurrencyConfig:
    values: dict[str, object] = {
        "account": "org-account",
        "user": "DANDER_USER",
        "database": "DANDER_CONCURRENCY_TEST",
        "warehouse": "DANDER_CONCURRENCY_WH",
        "role": "DANDER_CONCURRENCY_ROLE",
    }
    values.update(overrides)
    return bulk.SnowflakeConcurrencyConfig(**values)  # type: ignore[arg-type]


def _identity() -> bulk.CandidateIdentity:
    return bulk.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 17),
        launcher="docker_local",
        regions=("local",),
        secret_provider="environment",
        provider_job_ids=("container:test",),
        service_shapes=("snowflake_xsmall",),
    )


def _approval(config: bulk.SnowflakeBulkConfig) -> bulk._Approval:
    return bulk._Approval(
        objectives=ApprovedObjectiveSet(
            names=bulk._OBJECTIVES,
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
            profile_id="snowflake_local_scale",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(Decimal("0.50"), _REFERENCE),
        account_sha256=bulk._identifier_sha256(config.account),
        operator_user_sha256=bulk._identifier_sha256(config.user),
        database=config.database,
        warehouse=config.warehouse,
        role=config.role or "",
    )


def _incremental_approval(
    config: bulk.SnowflakeIncrementalConfig,
) -> bulk._Approval:
    return bulk._Approval(
        objectives=ApprovedObjectiveSet(
            names=bulk._INCREMENTAL_OBJECTIVES,
            benchmark_class=BenchmarkClass.INCREMENTAL,
            profile_id="snowflake_local_scale",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(Decimal("0.50"), _REFERENCE),
        account_sha256=bulk._identifier_sha256(config.account),
        operator_user_sha256=bulk._identifier_sha256(config.user),
        database=config.database,
        warehouse=config.warehouse,
        role=config.role or "",
    )


def _concurrency_approval(
    config: bulk.SnowflakeConcurrencyConfig,
) -> bulk._Approval:
    return bulk._Approval(
        objectives=ApprovedObjectiveSet(
            names=bulk._CONCURRENCY_OBJECTIVES,
            benchmark_class=BenchmarkClass.CONCURRENT_PIPELINES,
            profile_id="snowflake_local_scale",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(Decimal("0.50"), _REFERENCE),
        account_sha256=bulk._identifier_sha256(config.account),
        operator_user_sha256=bulk._identifier_sha256(config.user),
        database=config.database,
        warehouse=config.warehouse,
        role=config.role or "",
    )


def _result() -> bulk._BulkResult:
    return bulk._BulkResult(
        duration_ms=2_000,
        peak_rss_bytes=128 * 1_024 * 1_024,
        narrow_duration_ms=1_200,
        narrow_rows=500_000,
        narrow_logical_bytes=28_000_000,
        wide_duration_ms=800,
        wide_rows=200_000,
        wide_logical_bytes=209_600_000,
        copy_operations=25,
        query_ids=("query-one", "query-two"),
        staging_tables=0,
        staging_stages=0,
        cleanup_verified=True,
    )


def _incremental_result() -> bulk._IncrementalResult:
    return bulk._IncrementalResult(
        duration_ms=2_000,
        peak_rss_bytes=128 * 1_024 * 1_024,
        seed_duration_ms=1_500,
        seed_rows=300_000,
        seed_logical_bytes=48_000_000,
        delta_duration_ms=500,
        delta_rows=3_000,
        delta_logical_bytes=480_000,
        final_rows=301_500,
        regression_rows_affected=0,
        copy_operations=9,
        query_ids=("query-one", "query-two"),
        staging_tables=0,
        staging_stages=0,
        cleanup_verified=True,
    )


def _concurrency_result() -> bulk._ConcurrencyResult:
    return bulk._ConcurrencyResult(
        duration_ms=1_000,
        peak_rss_bytes=128 * 1_024 * 1_024,
        pipeline_count=4,
        rows_per_pipeline=5_000,
        total_rows=20_000,
        logical_input_bytes=3_040_000,
        concurrent_claim_attempts=2,
        stale_publications_rejected=1,
        copy_operations=4,
        query_ids=("query-one", "query-two"),
        staging_tables=0,
        staging_stages=0,
        cleanup_verified=True,
    )


def _manifest(config: bulk.SnowflakeBulkConfig) -> dict[str, object]:
    approval = _approval(config)
    return {
        "schema": bulk._APPROVAL_SCHEMA,
        "cost_ceiling": approval.cost_ceiling.to_payload(),
        "workload": config.workload_payload(),
        "configuration": {
            "snowflake": {
                "account_sha256": approval.account_sha256,
                "operator_user_sha256": approval.operator_user_sha256,
                "database": approval.database,
                "warehouse": approval.warehouse,
                "role": approval.role,
            }
        },
        "approved_objectives": approval.objectives.to_payload(),
    }


def _incremental_manifest(
    config: bulk.SnowflakeIncrementalConfig,
) -> dict[str, object]:
    approval = _incremental_approval(config)
    return {
        "schema": bulk._APPROVAL_SCHEMA,
        "cost_ceiling": approval.cost_ceiling.to_payload(),
        "workload": config.workload_payload(),
        "configuration": {
            "snowflake": {
                "account_sha256": approval.account_sha256,
                "operator_user_sha256": approval.operator_user_sha256,
                "database": approval.database,
                "warehouse": approval.warehouse,
                "role": approval.role,
            }
        },
        "approved_objectives": approval.objectives.to_payload(),
    }


def _concurrency_manifest(
    config: bulk.SnowflakeConcurrencyConfig,
) -> dict[str, object]:
    approval = _concurrency_approval(config)
    return {
        "schema": bulk._APPROVAL_SCHEMA,
        "cost_ceiling": approval.cost_ceiling.to_payload(),
        "workload": config.workload_payload(),
        "configuration": {
            "snowflake": {
                "account_sha256": approval.account_sha256,
                "operator_user_sha256": approval.operator_user_sha256,
                "database": approval.database,
                "warehouse": approval.warehouse,
                "role": approval.role,
            }
        },
        "approved_objectives": approval.objectives.to_payload(),
    }


def test_config_is_bounded_and_provider_values_store_only_secret_references() -> None:
    config = _config(auth_method="oauth", token_env="DANDER_TEST_SNOWFLAKE_TOKEN")

    values = bulk._provider_values(config, schema_name="DANDER_PHASE8_BULK_TEST")

    assert values["auth"] == {
        "method": "oauth",
        "token_env": "DANDER_TEST_SNOWFLAKE_TOKEN",
    }
    assert values["direct_max_rows"] == 0
    assert values["max_rows_per_file"] == 50_000
    assert "token" not in values
    assert config.workload_payload()["benchmark_class"] == "bulk_throughput"


def test_incremental_config_binds_accepted_workload_and_secret_references() -> None:
    config = _incremental_config(
        auth_method="oauth",
        token_env="DANDER_TEST_SNOWFLAKE_TOKEN",
    )

    values = bulk._provider_values(config, schema_name="DANDER_PHASE8_INCREMENTAL_TEST")

    assert values["auth"] == {
        "method": "oauth",
        "token_env": "DANDER_TEST_SNOWFLAKE_TOKEN",
    }
    assert values["direct_max_rows"] == 0
    assert values["max_rows_per_file"] == 50_000
    assert "token" not in values
    assert config.workload_payload() == {
        "schema": "io.dander.phase8.snowflake-incremental/v1",
        "benchmark_class": "incremental",
        "seed_rows": 300_000,
        "delta_rows": 3_000,
        "payload_bytes": 128,
        "copy_part_rows": 50_000,
        "copy_part_logical_bytes": 16 * 1_024 * 1_024,
    }


def test_concurrency_config_binds_four_pipeline_workload_and_secret_references() -> None:
    config = _concurrency_config(
        auth_method="oauth",
        token_env="DANDER_TEST_SNOWFLAKE_TOKEN",
    )

    values = bulk._provider_values(config, schema_name="DANDER_PHASE8_CONCURRENCY_TEST")

    assert values["auth"] == {
        "method": "oauth",
        "token_env": "DANDER_TEST_SNOWFLAKE_TOKEN",
    }
    assert values["direct_max_rows"] == 0
    assert values["max_rows_per_file"] == 5_000
    assert "token" not in values
    assert config.workload_payload() == {
        "schema": "io.dander.phase8.snowflake-concurrency/v1",
        "benchmark_class": "concurrent_pipelines",
        "concurrent_pipelines": 4,
        "rows_per_pipeline": 5_000,
        "payload_bytes": 128,
        "copy_part_rows": 5_000,
        "copy_part_logical_bytes": 16 * 1_024 * 1_024,
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"auth_method": "password"}, "auth_method"),
        ({"copy_part_rows": 0}, "copy_part_rows"),
        ({"copy_part_rows": 500_001}, "smaller workload"),
        ({"token_env": "not-an-env", "auth_method": "oauth"}, "token_env"),
    ],
)
def test_config_fails_before_provider_io(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _config(**overrides)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"concurrent_pipelines": 1}, "between 2 and 32"),
        ({"concurrent_pipelines": 33}, "between 2 and 32"),
        ({"rows_per_pipeline": 0}, "rows_per_pipeline"),
        ({"copy_part_rows": 5_001}, "rows_per_pipeline"),
    ],
)
def test_concurrency_config_fails_before_provider_io(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _concurrency_config(**overrides)


def test_load_approval_binds_exact_workload_and_candidate(tmp_path: Path) -> None:
    config = _config()
    path = tmp_path / "objectives.json"
    path.write_text(json.dumps(_manifest(config)), encoding="utf-8")

    approval = bulk.load_approval(path, config=config)

    assert approval.objectives.configuration_sha256 == config.configuration_sha256()
    assert approval.objectives.names == bulk._OBJECTIVES
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")
    bulk._require_provider_match(config, approval)

    payload = _manifest(config)
    workload = payload["workload"]
    assert isinstance(workload, dict)
    workload["narrow_rows"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="workload"):
        bulk.load_approval(path, config=config)


def test_load_incremental_approval_binds_exact_workload(tmp_path: Path) -> None:
    config = _incremental_config()
    path = tmp_path / "incremental-objectives.json"
    path.write_text(json.dumps(_incremental_manifest(config)), encoding="utf-8")

    approval = bulk.load_approval(path, config=config)

    assert approval.objectives.benchmark_class is BenchmarkClass.INCREMENTAL
    assert approval.objectives.names == bulk._INCREMENTAL_OBJECTIVES
    assert approval.objectives.configuration_sha256 == config.configuration_sha256()
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")

    payload = _incremental_manifest(config)
    objectives = payload["approved_objectives"]
    assert isinstance(objectives, dict)
    objectives["names"] = list(bulk._OBJECTIVES)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="required objective set"):
        bulk.load_approval(path, config=config)


def test_load_concurrency_approval_binds_exact_workload(tmp_path: Path) -> None:
    config = _concurrency_config()
    path = tmp_path / "concurrency-objectives.json"
    path.write_text(json.dumps(_concurrency_manifest(config)), encoding="utf-8")

    approval = bulk.load_approval(path, config=config)

    assert approval.objectives.benchmark_class is BenchmarkClass.CONCURRENT_PIPELINES
    assert approval.objectives.names == bulk._CONCURRENCY_OBJECTIVES
    assert approval.objectives.configuration_sha256 == config.configuration_sha256()

    payload = _concurrency_manifest(config)
    workload = payload["workload"]
    assert isinstance(workload, dict)
    workload["concurrent_pipelines"] = 3
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="workload"):
        bulk.load_approval(path, config=config)


def test_provider_coordinates_fail_closed_before_runtime() -> None:
    config = _config()
    approval = _approval(config)

    with pytest.raises(ValueError, match="private Snowflake coordinates"):
        bulk._require_provider_match(_config(database="OTHER_DATABASE"), approval)


def test_committed_objective_matches_harness_defaults() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "docs/evidence/phase8/2026-08-17/snowflake-rc29-bulk-throughput-objectives.json"

    approval = bulk.load_approval(path, config=_config())

    assert approval.objectives.release_version == "0.9.0rc29"
    assert approval.objectives.git_commit == "7a6d138a5df19ab81df202b6cb6121e134e59991"
    assert approval.objectives.image_digest == (
        "sha256:e016419fda113a5288d82fdf37d23785d39d943750cb9e19be047edab6eaad54"
    )


def test_committed_incremental_objective_matches_harness_defaults() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "docs/evidence/phase8/2026-08-17/snowflake-rc29-incremental-objectives.json"
    config = bulk.SnowflakeIncrementalConfig(
        account="org-account",
        user="DANDER_USER",
        database="DANDER_P8_RC29_INCREMENTAL_17A2026A",
        warehouse="DANDER_P8_RC29_INCREMENTAL_17A2026A_WH",
        role="DANDER_P8_RC29_INCREMENTAL_17A2026A_ROLE",
    )

    approval = bulk.load_approval(path, config=config)

    assert approval.objectives.benchmark_class is BenchmarkClass.INCREMENTAL
    assert approval.objectives.release_version == "0.9.0rc29"
    assert approval.objectives.git_commit == "7a6d138a5df19ab81df202b6cb6121e134e59991"
    assert approval.objectives.image_digest == (
        "sha256:e016419fda113a5288d82fdf37d23785d39d943750cb9e19be047edab6eaad54"
    )


def test_records_are_streamed_with_exact_payload_width() -> None:
    rows = bulk._records(2, 4)

    assert next(rows) == {"id": "000000000000", "payload": "xxxx"}
    assert next(rows) == {"id": "000000000001", "payload": "xxxx"}
    with pytest.raises(StopIteration):
        next(rows)


def test_incremental_records_preserve_seed_delta_and_cursor_shape() -> None:
    config = _incremental_config(seed_rows=4, delta_rows=2, payload_bytes=3, copy_part_rows=2)

    assert list(bulk._incremental_seed_records(config)) == [
        {"id": "000000000000", "payload": "sss", "cursor_value": 1},
        {"id": "000000000001", "payload": "sss", "cursor_value": 1},
        {"id": "000000000002", "payload": "sss", "cursor_value": 1},
        {"id": "000000000003", "payload": "sss", "cursor_value": 1},
    ]
    assert list(bulk._incremental_delta_records(config)) == [
        {"id": "000000000000", "payload": "ddd", "cursor_value": 2},
        {"id": "000000000004", "payload": "ddd", "cursor_value": 2},
    ]


def test_concurrency_runner_uses_distinct_independent_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _concurrency_config(rows_per_pipeline=2, copy_part_rows=2)
    writes: list[tuple[str, str, str]] = []
    readbacks: list[str] = []

    def write_table(
        _runtime: WarehouseRuntime,
        *,
        database: str,
        schema: str,
        table: str,
        pipeline_id: str,
        rows: int,
        payload_bytes: int,
        copy_part_rows: int,
        authority_id: str,
    ) -> tuple[int, int, tuple[OperationTelemetry, ...]]:
        del database, schema, payload_bytes, copy_part_rows
        writes.append((table, pipeline_id, authority_id))
        return rows, 1, ()

    def require_shape(
        _runtime: WarehouseRuntime,
        *,
        database: str,
        schema: str,
        table: str,
        rows: int,
        payload_bytes: int,
    ) -> None:
        del database, schema, rows, payload_bytes
        readbacks.append(table)

    monkeypatch.setattr(bulk, "_write_table", write_table)
    monkeypatch.setattr(bulk, "_require_table_shape", require_shape)

    operations = bulk._write_concurrent_targets(
        cast("WarehouseRuntime", object()),
        config=config,
        schema="DANDER_CONCURRENCY",
    )

    assert operations == ()
    expected_tables = [f"pipeline_{index:02d}_records" for index in range(4)]
    assert sorted(table for table, _, _ in writes) == expected_tables
    assert sorted(readbacks) == expected_tables
    assert {authority for _, _, authority in writes} == {bulk._CONCURRENCY_AUTHORITY_ID}


def test_concurrency_contention_rejects_the_stale_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Fence:
        def __init__(self) -> None:
            self.claims: list[FencingToken] = []

        def claim(self, _relation: RelationRef, fence: FencingToken) -> object:
            self.claims.append(fence)
            return object()

    class Writer:
        def write(self, _rows: object, _target: object) -> int:
            raise TargetFenceLostError("stale")

    class Writers:
        def build_ingestion_writer(self, **_values: object) -> Writer:
            return Writer()

    fence = Fence()
    runtime = cast(
        "WarehouseRuntime",
        SimpleNamespace(target_fence=fence, writers=Writers()),
    )
    checked: list[tuple[str, int]] = []

    def require_count(
        _runtime: WarehouseRuntime,
        relation: RelationRef,
        *,
        expected: int,
    ) -> None:
        checked.append((relation.name, expected))

    monkeypatch.setattr(bulk, "_require_count", require_count)

    stale_rejected, attempts = bulk._reject_stale_publication(
        runtime,
        database="DANDER_CONCURRENCY_TEST",
        schema="DANDER_CONCURRENCY",
        copy_part_rows=5_000,
    )

    assert stale_rejected is True
    assert attempts == 2
    assert checked == [("contention_records", 0)]
    assert {claim.token for claim in fence.claims} == {20, 21}


def test_report_keeps_provider_cost_pending_without_claiming_pass() -> None:
    config = _config()

    report = bulk._bulk_report(
        config,
        _identity(),
        _approval(config),
        _result(),
        provider_cost_usd=None,
    )

    assert report.status is QualificationStatus.NOT_EVALUATED
    assert report.performance.costs[0].estimated is True
    assert report.performance.costs[0].amount == Decimal("0.50")
    statuses = {objective.name: objective.status for objective in report.objectives}
    assert statuses["cost_ceiling"] is ObjectiveStatus.NOT_EVALUATED
    assert all(
        status is ObjectiveStatus.PASSED
        for name, status in statuses.items()
        if name != "cost_ceiling"
    )
    assert report.context.provider_job_ids == (
        "container:test",
        "query-one",
        "query-two",
    )


def test_incremental_report_preserves_functional_pass_and_pending_cost() -> None:
    config = _incremental_config()

    report = bulk._incremental_report(
        config,
        _identity(),
        _incremental_approval(config),
        _incremental_result(),
        provider_cost_usd=None,
    )

    assert report.status is QualificationStatus.NOT_EVALUATED
    assert report.workload.benchmark_class is BenchmarkClass.INCREMENTAL
    assert report.workload.input_rows == 3_000
    assert report.performance.throughput_rows_per_second.value == Decimal("6000.000")
    metrics = {metric.name: metric.value for metric in report.performance.provider_metrics}
    assert metrics["delta_target_ratio"] == Decimal("100")
    assert metrics["final_target_rows"] == 301_500
    assert metrics["regression_rows_affected"] == 0
    statuses = {objective.name: objective.status for objective in report.objectives}
    assert statuses["cost_ceiling"] is ObjectiveStatus.NOT_EVALUATED
    assert all(
        status is ObjectiveStatus.PASSED
        for name, status in statuses.items()
        if name != "cost_ceiling"
    )


def test_concurrency_report_preserves_functional_pass_and_pending_cost() -> None:
    config = _concurrency_config()

    report = bulk._concurrency_report(
        config,
        _identity(),
        _concurrency_approval(config),
        _concurrency_result(),
        provider_cost_usd=None,
    )

    assert report.status is QualificationStatus.NOT_EVALUATED
    assert report.workload.benchmark_class is BenchmarkClass.CONCURRENT_PIPELINES
    assert report.workload.input_rows == 20_000
    assert report.workload.concurrency == 4
    assert report.performance.throughput_rows_per_second.value == Decimal("20000.000")
    metrics = {metric.name: metric.value for metric in report.performance.provider_metrics}
    assert metrics["pipeline_count"] == 4
    assert metrics["rows_per_pipeline"] == 5_000
    assert metrics["concurrent_claim_attempts"] == 2
    assert metrics["stale_publications_rejected"] == 1
    statuses = {objective.name: objective.status for objective in report.objectives}
    assert statuses["cost_ceiling"] is ObjectiveStatus.NOT_EVALUATED
    assert all(
        status is ObjectiveStatus.PASSED
        for name, status in statuses.items()
        if name != "cost_ceiling"
    )


def test_incremental_report_rejects_a_target_smaller_than_100x_delta() -> None:
    config = _incremental_config(seed_rows=10_000, delta_rows=2_000, copy_part_rows=1_000)
    result = replace(
        _incremental_result(),
        seed_rows=10_000,
        delta_rows=2_000,
    )

    with pytest.raises(bulk.SnowflakeIncrementalQualificationError, match="100 times"):
        bulk._incremental_report(
            config,
            _identity(),
            _incremental_approval(config),
            result,
            provider_cost_usd=None,
        )


@pytest.mark.parametrize(
    ("cost", "status"),
    [
        (Decimal("0.05"), QualificationStatus.PASSED),
        (Decimal("0.51"), QualificationStatus.FAILED),
    ],
)
def test_report_classifies_measured_cost_against_ceiling(
    cost: Decimal,
    status: QualificationStatus,
) -> None:
    config = _config()

    report = bulk._bulk_report(
        config,
        _identity(),
        _approval(config),
        _result(),
        provider_cost_usd=cost,
    )

    assert report.status is status
    assert report.performance.costs[0].estimated is False
    assert report.performance.costs[0].amount == cost


def test_cli_failure_record_never_exposes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config()
    objectives = tmp_path / "objectives.json"
    objectives.write_text(json.dumps(_manifest(config)), encoding="utf-8")
    secret = "provider-secret-response"

    def fail(
        _config: bulk.SnowflakeBulkConfig,
        *,
        identity: bulk.CandidateIdentity,
        approval: bulk._Approval,
        provider_cost_usd: Decimal | None,
    ) -> None:
        del identity, approval, provider_cost_usd
        raise bulk.SnowflakeBulkQualificationError(secret)

    monkeypatch.setattr(bulk, "run_phase8_snowflake_bulk", fail)

    exit_code = bulk.main(
        [
            "--account",
            "org-account",
            "--user",
            "DANDER_USER",
            "--database",
            "DANDER_TEST",
            "--warehouse",
            "DANDER_WH",
            "--role",
            "DANDER_ROLE",
            "--objectives",
            str(objectives),
            "--candidate-version",
            __version__,
            "--candidate-commit",
            _COMMIT,
            "--image-digest",
            _DIGEST,
            "--approval-reference",
            _REFERENCE,
            "--benchmark-date",
            "2026-08-17",
            "--provider-job-id",
            "container:test",
            "--service-shape",
            "snowflake_xsmall",
            "--output-file",
            str(tmp_path / "report.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert secret not in json.dumps(payload)


def test_incremental_cli_dispatches_and_sanitizes_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _incremental_config()
    objectives = tmp_path / "incremental-objectives.json"
    objectives.write_text(json.dumps(_incremental_manifest(config)), encoding="utf-8")
    secret = "incremental-provider-secret-response"

    def fail(
        _config: bulk.SnowflakeIncrementalConfig,
        *,
        identity: bulk.CandidateIdentity,
        approval: bulk._Approval,
        provider_cost_usd: Decimal | None,
    ) -> None:
        del identity, approval, provider_cost_usd
        raise bulk.SnowflakeIncrementalQualificationError(secret)

    monkeypatch.setattr(bulk, "run_phase8_snowflake_incremental", fail)

    exit_code = bulk.main(
        [
            "--benchmark-class",
            "incremental",
            "--account",
            config.account,
            "--user",
            config.user,
            "--database",
            config.database,
            "--warehouse",
            config.warehouse,
            "--role",
            config.role or "",
            "--objectives",
            str(objectives),
            "--candidate-version",
            __version__,
            "--candidate-commit",
            _COMMIT,
            "--image-digest",
            _DIGEST,
            "--approval-reference",
            _REFERENCE,
            "--benchmark-date",
            "2026-08-17",
            "--provider-job-id",
            "container:test",
            "--service-shape",
            "snowflake_xsmall",
            "--output-file",
            str(tmp_path / "incremental-report.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["benchmark_class"] == "incremental"
    assert payload["status"] == "failed"
    assert secret not in json.dumps(payload)


def test_concurrency_cli_dispatches_and_sanitizes_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _concurrency_config()
    objectives = tmp_path / "concurrency-objectives.json"
    objectives.write_text(json.dumps(_concurrency_manifest(config)), encoding="utf-8")
    secret = "concurrency-provider-secret-response"

    def fail(
        _config: bulk.SnowflakeConcurrencyConfig,
        *,
        identity: bulk.CandidateIdentity,
        approval: bulk._Approval,
        provider_cost_usd: Decimal | None,
    ) -> None:
        del identity, approval, provider_cost_usd
        raise bulk.SnowflakeConcurrencyQualificationError(secret)

    monkeypatch.setattr(bulk, "run_phase8_snowflake_concurrency", fail)

    exit_code = bulk.main(
        [
            "--benchmark-class",
            "concurrent_pipelines",
            "--account",
            config.account,
            "--user",
            config.user,
            "--database",
            config.database,
            "--warehouse",
            config.warehouse,
            "--role",
            config.role or "",
            "--objectives",
            str(objectives),
            "--candidate-version",
            __version__,
            "--candidate-commit",
            _COMMIT,
            "--image-digest",
            _DIGEST,
            "--approval-reference",
            _REFERENCE,
            "--benchmark-date",
            "2026-08-17",
            "--provider-job-id",
            "container:test",
            "--service-shape",
            "snowflake_xsmall",
            "--output-file",
            str(tmp_path / "concurrency-report.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["benchmark_class"] == "concurrent_pipelines"
    assert payload["status"] == "failed"
    assert secret not in json.dumps(payload)
