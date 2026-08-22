"""Contracts for the exact-candidate PostgreSQL Phase 8 benchmark harness."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from scripts.benchmarks import postgresql_phase8 as harness
from scripts.benchmarks.postgresql_phase8 import (
    CandidateIdentity,
    Phase8PostgreSQLConfig,
    _bulk_report,
    _BulkResult,
    _correctness_report,
    _CorrectnessResult,
    _incremental_report,
    _IncrementalResult,
    _write_transform_models,
    load_approval,
)

from dander.qualification import BenchmarkClass
from dander.transform import SqlDialect, TransformProject


def test_phase8_postgresql_config_hashes_each_class_deterministically() -> None:
    config = Phase8PostgreSQLConfig()

    correctness = config.configuration_sha256(BenchmarkClass.CORRECTNESS)
    bulk = config.configuration_sha256(BenchmarkClass.BULK_THROUGHPUT)
    incremental = config.configuration_sha256(BenchmarkClass.INCREMENTAL)
    transform = config.configuration_sha256(BenchmarkClass.TRANSFORM)
    failure = config.configuration_sha256(BenchmarkClass.FAILURE)

    assert len(correctness) == 64
    assert len(bulk) == 64
    assert len(incremental) == 64
    assert len(transform) == 64
    assert len(failure) == 64
    assert len({correctness, bulk, incremental, transform, failure}) == 5
    assert (
        config.workload_payload(BenchmarkClass.CORRECTNESS)["expected_normalized_sha256"]
        == "82886fc4c0bc5cfb248df1196b9d29763cad4fac60cf248a91084a185d78c2ee"
    )
    assert config.workload_payload(BenchmarkClass.INCREMENTAL)["delta_rows"] == 3_000


def test_phase8_postgresql_config_rejects_non_small_or_odd_delta() -> None:
    with pytest.raises(ValueError, match="even"):
        Phase8PostgreSQLConfig(incremental_delta_rows=3)
    with pytest.raises(ValueError, match="must not exceed"):
        Phase8PostgreSQLConfig(incremental_seed_rows=2, incremental_delta_rows=4)


def test_load_approval_rejects_workload_or_objective_drift(tmp_path: Path) -> None:
    config = Phase8PostgreSQLConfig()
    path = tmp_path / "objectives.json"
    payload = _manifest(config)
    path.write_text(json.dumps(payload), encoding="utf-8")

    approval = load_approval(
        path,
        config=config,
        benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
    )

    assert approval.objectives.configuration_sha256 == config.configuration_sha256(
        BenchmarkClass.BULK_THROUGHPUT
    )
    workload = cast("dict[str, object]", payload["workload"])
    workload["wide_rows"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="workload"):
        load_approval(
            path,
            config=config,
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
        )


def test_gke_correctness_approval_binds_protected_harness_and_retry_policy(
    tmp_path: Path,
) -> None:
    config = Phase8PostgreSQLConfig()
    path = tmp_path / "objectives.json"
    payload = _gke_correctness_manifest(config)
    path.write_text(json.dumps(payload), encoding="utf-8")

    approval = load_approval(
        path,
        config=config,
        benchmark_class=BenchmarkClass.CORRECTNESS,
    )

    assert approval.cost_ceiling.amount_usd == Decimal("0.50")
    execution = cast(
        "dict[str, object]",
        cast("dict[str, object]", payload["configuration"])["execution"],
    )
    execution["harness_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="protected PostgreSQL harness"):
        load_approval(
            path,
            config=config,
            benchmark_class=BenchmarkClass.CORRECTNESS,
        )


def test_gke_correctness_report_keeps_only_provider_cost_pending(tmp_path: Path) -> None:
    config = Phase8PostgreSQLConfig()
    path = tmp_path / "objectives.json"
    path.write_text(json.dumps(_gke_correctness_manifest(config)), encoding="utf-8")
    approval = load_approval(
        path,
        config=config,
        benchmark_class=BenchmarkClass.CORRECTNESS,
    )
    identity = CandidateIdentity(
        release_version="0.9.0rc31",
        git_commit="a" * 40,
        image_digest=f"sha256:{'b' * 64}",
        approval_reference="codex-goal-gke-correctness",
        benchmark_date=date(2026, 8, 21),
        launcher="gke_standard_zonal",
        regions=("gcp:us-central1-a",),
        secret_provider="kubernetes",
        provider_job_ids=("cluster:test", "job:test"),
        service_shapes=("dander_job_2cpu_512mib",),
    )
    result = _CorrectnessResult(
        duration_ms=125,
        peak_rss_bytes=128_000_000,
        input_rows=7,
        output_rows=3,
        logical_input_bytes=317,
        normalized_sha256="82886fc4c0bc5cfb248df1196b9d29763cad4fac60cf248a91084a185d78c2ee",
        temporary_staging_relations=0,
        cleanup_verified=True,
    )

    pending = json.loads(_correctness_report(config, identity, approval, result).to_json())

    assert pending["status"] == "not_evaluated"
    assert {objective["name"]: objective["status"] for objective in pending["objectives"]} == {
        "cleanup": "passed",
        "cost_ceiling": "not_evaluated",
        "exact_normalized_output": "passed",
        "replay_equal": "passed",
        "scd1_copy_completion": "passed",
    }
    assert pending["performance"]["costs"] == [
        {
            "amount": "0.50",
            "currency": "USD",
            "estimated": True,
            "provider": "gcp",
            "service": "gke_standard_zonal",
        }
    ]
    metrics = {
        measurement["name"]: measurement["value"]
        for measurement in pending["performance"]["measurements"]
    }
    assert metrics["kubernetes_job_retries"] == "0"
    assert metrics["provider_operation_retries"] == "0"

    posted = json.loads(
        _correctness_report(
            config,
            identity,
            approval,
            result,
            provider_cost_usd=Decimal("0.25"),
        ).to_json()
    )
    assert posted["status"] == "passed"
    assert posted["performance"]["costs"][0]["estimated"] is False
    payload = _manifest(config)
    approved = cast("dict[str, object]", payload["approved_objectives"])
    names = cast("list[str]", approved["names"])
    names.pop()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="required objective set"):
        load_approval(
            path,
            config=config,
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
        )


def test_gke_bulk_report_keeps_only_provider_cost_pending(tmp_path: Path) -> None:
    config = Phase8PostgreSQLConfig()
    path = tmp_path / "objectives.json"
    path.write_text(json.dumps(_gke_bulk_manifest(config)), encoding="utf-8")
    approval = load_approval(
        path,
        config=config,
        benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
    )
    identity = CandidateIdentity(
        release_version="0.9.0rc31",
        git_commit="a" * 40,
        image_digest=f"sha256:{'b' * 64}",
        approval_reference="codex-goal-gke-bulk",
        benchmark_date=date(2026, 8, 21),
        launcher="gke_standard_zonal",
        regions=("gcp:us-central1-a",),
        secret_provider="kubernetes",
        provider_job_ids=("cluster:test", "job:test"),
        service_shapes=("dander_job_2cpu_512mib",),
    )
    result = _BulkResult(
        duration_ms=30_000,
        peak_rss_bytes=200_000_000,
        narrow_duration_ms=12_000,
        narrow_rows=500_000,
        narrow_logical_bytes=28_000_000,
        wide_duration_ms=18_000,
        wide_rows=200_000,
        wide_logical_bytes=209_600_000,
        temporary_staging_relations=0,
        cleanup_verified=True,
    )

    pending = json.loads(_bulk_report(config, identity, approval, result).to_json())

    assert pending["status"] == "not_evaluated"
    assert {objective["name"]: objective["status"] for objective in pending["objectives"]} == {
        "cleanup": "passed",
        "cost_ceiling": "not_evaluated",
        "narrow_copy_completion": "passed",
        "narrow_throughput_measurement": "passed",
        "wide_copy_completion": "passed",
        "wide_throughput_measurement": "passed",
    }
    assert pending["performance"]["costs"] == [
        {
            "amount": "0.50",
            "currency": "USD",
            "estimated": True,
            "provider": "gcp",
            "service": "gke_standard_zonal",
        }
    ]
    metrics = {
        measurement["name"]: measurement["value"]
        for measurement in pending["performance"]["measurements"]
    }
    assert metrics["kubernetes_job_retries"] == "0"
    assert metrics["provider_operation_retries"] == "0"

    posted = json.loads(
        _bulk_report(
            config,
            identity,
            approval,
            result,
            provider_cost_usd=Decimal("0.25"),
        ).to_json()
    )
    assert posted["status"] == "passed"
    assert posted["performance"]["costs"][0]["estimated"] is False


def test_gke_incremental_report_keeps_only_provider_cost_pending(tmp_path: Path) -> None:
    config = Phase8PostgreSQLConfig()
    path = tmp_path / "objectives.json"
    path.write_text(json.dumps(_gke_incremental_manifest(config)), encoding="utf-8")
    approval = load_approval(
        path,
        config=config,
        benchmark_class=BenchmarkClass.INCREMENTAL,
    )
    identity = CandidateIdentity(
        release_version="0.9.0rc31",
        git_commit="a" * 40,
        image_digest=f"sha256:{'b' * 64}",
        approval_reference="codex-goal-gke-incremental",
        benchmark_date=date(2026, 8, 21),
        launcher="gke_standard_zonal",
        regions=("gcp:us-central1-a",),
        secret_provider="kubernetes",
        provider_job_ids=("cluster:test", "job:test"),
        service_shapes=("dander_job_2cpu_512mib",),
    )
    result = _IncrementalResult(
        duration_ms=25_000,
        peak_rss_bytes=200_000_000,
        seed_duration_ms=20_000,
        seed_rows=300_000,
        seed_logical_bytes=48_000_000,
        delta_duration_ms=5_000,
        delta_rows=3_000,
        delta_logical_bytes=480_000,
        final_rows=301_500,
        regression_rows_affected=0,
        temporary_staging_relations=0,
        cleanup_verified=True,
    )

    pending = json.loads(_incremental_report(config, identity, approval, result).to_json())

    assert pending["status"] == "not_evaluated"
    assert {objective["name"]: objective["status"] for objective in pending["objectives"]} == {
        "cleanup": "passed",
        "cost_ceiling": "not_evaluated",
        "delta_target_ratio": "passed",
        "exact_result": "passed",
        "incremental_cursor_monotonic": "passed",
        "incremental_throughput_measurement": "passed",
    }
    assert pending["performance"]["costs"] == [
        {
            "amount": "0.50",
            "currency": "USD",
            "estimated": True,
            "provider": "gcp",
            "service": "gke_standard_zonal",
        }
    ]
    metrics = {
        measurement["name"]: measurement["value"]
        for measurement in pending["performance"]["measurements"]
    }
    assert metrics["kubernetes_job_retries"] == "0"
    assert metrics["provider_operation_retries"] == "0"

    posted = json.loads(
        _incremental_report(
            config,
            identity,
            approval,
            result,
            provider_cost_usd=Decimal("0.25"),
        ).to_json()
    )
    assert posted["status"] == "passed"
    assert posted["performance"]["costs"][0]["estimated"] is False


def test_transform_fixture_compiles_all_required_models(tmp_path: Path) -> None:
    _write_transform_models(tmp_path, target_schema="phase8_models")

    project = TransformProject.load(
        tmp_path,
        catalog="dander",
        raw_namespace="phase8_raw",
        target_dialect=SqlDialect.POSTGRES,
    )

    assert tuple(model.name for model in project.ordered()) == (
        "scan_records",
        "joined_records",
        "aggregate_records",
        "incremental_records",
    )
    assert all(project.compile(model) for model in project.ordered())


def _manifest(config: Phase8PostgreSQLConfig) -> dict[str, object]:
    approval = "codex-thread-phase8-2026-08-14"
    return {
        "schema": "io.dander.qualification.objective-approval/v1",
        "cost_ceiling": {"amount_usd": "0.00", "approval_reference": approval},
        "workload": config.workload_payload(BenchmarkClass.BULK_THROUGHPUT),
        "approved_objectives": {
            "names": [
                "cleanup",
                "cost_ceiling",
                "narrow_copy_completion",
                "narrow_throughput_measurement",
                "wide_copy_completion",
                "wide_throughput_measurement",
            ],
            "benchmark_class": "bulk_throughput",
            "profile_id": "postgresql_local_scale",
            "release_version": "0.9.0rc22",
            "git_commit": "a" * 40,
            "image_digest": f"sha256:{'b' * 64}",
            "configuration_sha256": config.configuration_sha256(BenchmarkClass.BULK_THROUGHPUT),
            "approval_reference": approval,
        },
    }


def _gke_correctness_manifest(config: Phase8PostgreSQLConfig) -> dict[str, object]:
    approval = "codex-goal-gke-correctness"
    return {
        "schema": "io.dander.qualification.objective-approval/v1",
        "cost_ceiling": {"amount_usd": "0.50", "approval_reference": approval},
        "workload": config.workload_payload(BenchmarkClass.CORRECTNESS),
        "configuration": {
            "execution": {
                "harness_sha256": harness._file_sha256(Path(harness.__file__)),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
            }
        },
        "approved_objectives": {
            "names": [
                "cleanup",
                "cost_ceiling",
                "exact_normalized_output",
                "replay_equal",
                "scd1_copy_completion",
            ],
            "benchmark_class": "correctness",
            "profile_id": "gke_standard_postgresql",
            "release_version": "0.9.0rc31",
            "git_commit": "a" * 40,
            "image_digest": f"sha256:{'b' * 64}",
            "configuration_sha256": config.configuration_sha256(BenchmarkClass.CORRECTNESS),
            "approval_reference": approval,
        },
    }


def _gke_bulk_manifest(config: Phase8PostgreSQLConfig) -> dict[str, object]:
    approval = "codex-goal-gke-bulk"
    return {
        "schema": "io.dander.qualification.objective-approval/v1",
        "cost_ceiling": {"amount_usd": "0.50", "approval_reference": approval},
        "workload": config.workload_payload(BenchmarkClass.BULK_THROUGHPUT),
        "configuration": {
            "execution": {
                "harness_sha256": harness._file_sha256(Path(harness.__file__)),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
            }
        },
        "approved_objectives": {
            "names": [
                "cleanup",
                "cost_ceiling",
                "narrow_copy_completion",
                "narrow_throughput_measurement",
                "wide_copy_completion",
                "wide_throughput_measurement",
            ],
            "benchmark_class": "bulk_throughput",
            "profile_id": "gke_standard_postgresql",
            "release_version": "0.9.0rc31",
            "git_commit": "a" * 40,
            "image_digest": f"sha256:{'b' * 64}",
            "configuration_sha256": config.configuration_sha256(BenchmarkClass.BULK_THROUGHPUT),
            "approval_reference": approval,
        },
    }


def _gke_incremental_manifest(config: Phase8PostgreSQLConfig) -> dict[str, object]:
    approval = "codex-goal-gke-incremental"
    return {
        "schema": "io.dander.qualification.objective-approval/v1",
        "cost_ceiling": {"amount_usd": "0.50", "approval_reference": approval},
        "workload": config.workload_payload(BenchmarkClass.INCREMENTAL),
        "configuration": {
            "execution": {
                "harness_sha256": harness._file_sha256(Path(harness.__file__)),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
            }
        },
        "approved_objectives": {
            "names": [
                "cleanup",
                "cost_ceiling",
                "delta_target_ratio",
                "exact_result",
                "incremental_cursor_monotonic",
                "incremental_throughput_measurement",
            ],
            "benchmark_class": "incremental",
            "profile_id": "gke_standard_postgresql",
            "release_version": "0.9.0rc31",
            "git_commit": "a" * 40,
            "image_digest": f"sha256:{'b' * 64}",
            "configuration_sha256": config.configuration_sha256(BenchmarkClass.INCREMENTAL),
            "approval_reference": approval,
        },
    }
