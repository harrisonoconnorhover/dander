"""Credential-free checks for the Phase 8 Snowflake bulk harness."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from scripts.benchmarks import snowflake_bulk_phase8 as bulk

from dander import __version__
from dander.qualification import (
    ApprovedCostCeiling,
    ApprovedObjectiveSet,
    BenchmarkClass,
    ObjectiveStatus,
    QualificationStatus,
)

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


def test_records_are_streamed_with_exact_payload_width() -> None:
    rows = bulk._records(2, 4)

    assert next(rows) == {"id": "000000000000", "payload": "xxxx"}
    assert next(rows) == {"id": "000000000001", "payload": "xxxx"}
    with pytest.raises(StopIteration):
        next(rows)


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
