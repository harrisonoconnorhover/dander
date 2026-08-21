"""Credential-free checks for the PostgreSQL concurrency harness."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from scripts.benchmarks import postgresql_concurrency_phase8 as concurrency

from dander import __version__
from dander.qualification import (
    ApprovedCostCeiling,
    ApprovedObjectiveSet,
    BenchmarkClass,
)

_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-gke-concurrency"


def _config(**overrides: object) -> concurrency.PostgreSQLConcurrencyConfig:
    values: dict[str, object] = {
        "concurrent_pipelines": 4,
        "rows_per_pipeline": 50,
        "payload_bytes": 128,
        "batch_rows": 10,
        "memory_limit_mib": 256,
    }
    values.update(overrides)
    return concurrency.PostgreSQLConcurrencyConfig(**values)  # type: ignore[arg-type]


def _identity() -> concurrency.CandidateIdentity:
    return concurrency.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 21),
        launcher="kubernetes",
        regions=("gcp:us-central1-a",),
        secret_provider="kubernetes",
        provider_job_ids=("cluster:test", "job:test"),
        service_shapes=(
            "dander_job_2cpu_256mib",
            "gke_standard_e2_standard_4",
            "postgresql_15.18_2cpu_1gib_tls",
        ),
    )


def _approval(config: concurrency.PostgreSQLConcurrencyConfig) -> concurrency._Approval:
    return concurrency._Approval(
        objectives=ApprovedObjectiveSet(
            names=concurrency._OBJECTIVES,
            benchmark_class=BenchmarkClass.CONCURRENT_PIPELINES,
            profile_id="gke_standard_postgresql",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(Decimal("0.50"), _REFERENCE),
    )


def _result(config: concurrency.PostgreSQLConcurrencyConfig) -> concurrency._ConcurrencyResult:
    return concurrency._ConcurrencyResult(
        duration_ms=400,
        peak_rss_bytes=128 * 1_024 * 1_024,
        total_rows=config.total_rows,
        logical_input_bytes=config.logical_input_bytes,
        independent_targets=config.concurrent_pipelines,
        stale_publications_rejected=1,
        temporary_staging_relations=0,
        tls_verified=True,
        cleanup_verified=True,
    )


def test_concurrency_report_requires_exact_rows_fence_cleanup_and_zero_retries() -> None:
    config = _config()
    report = concurrency._report(
        config,
        _identity(),
        _approval(config),
        _result(config),
        provider_cost_usd=Decimal("0.05"),
    )
    payload = report.to_payload()
    performance = cast("dict[str, Any]", payload["performance"])
    workload = cast("dict[str, Any]", payload["workload"])
    measurements = cast("list[dict[str, Any]]", performance["measurements"])
    measured = {item["name"]: item["value"] for item in measurements}

    assert payload["status"] == "passed"
    assert workload["input_rows"] == 200
    assert measured["retries"] == "0"
    assert measured["kubernetes_job_retries"] == "0"
    assert measured["provider_operation_retries"] == "0"
    assert measured["stale_publications_rejected"] == "1"
    assert performance["costs"] == [
        {
            "provider": "gcp",
            "service": "gke_standard_zonal",
            "amount": "0.05",
            "currency": "USD",
            "estimated": False,
        }
    ]

    with pytest.raises(
        concurrency.PostgreSQLConcurrencyQualificationError,
        match="exactly one stale publication",
    ):
        concurrency._report(
            config,
            _identity(),
            _approval(config),
            replace(_result(config), stale_publications_rejected=0),
            provider_cost_usd=Decimal("0.05"),
        )


def test_concurrency_report_keeps_delayed_provider_cost_open() -> None:
    config = _config()
    report = concurrency._report(
        config,
        _identity(),
        _approval(config),
        _result(config),
        provider_cost_usd=None,
    )

    assert report.status.value == "not_evaluated"
    assert next(item for item in report.objectives if item.name == "cost_ceiling").status.value == (
        "not_evaluated"
    )


def test_concurrency_requires_the_approved_container_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    monkeypatch.setattr(
        concurrency, "_container_memory_limit_bytes", lambda: config.memory_limit_bytes
    )
    concurrency._require_container_memory_limit(config)

    monkeypatch.setattr(
        concurrency, "_container_memory_limit_bytes", lambda: config.memory_limit_bytes * 2
    )
    with pytest.raises(
        concurrency.PostgreSQLConcurrencyQualificationError,
        match="container memory limit",
    ):
        concurrency._require_container_memory_limit(config)


def test_gke_concurrency_objective_binds_candidate_harness_and_dependency() -> None:
    reference = "codex-goal-02043c37-096e-416a-875c-b405c4af0594-existing-gke-usd-0.50"
    config = concurrency.PostgreSQLConcurrencyConfig()
    identity = replace(
        _identity(),
        release_version="0.9.0rc30",
        git_commit="d27dd880fc7676c15969bff76aaabb64c22be7c2",
        image_digest=("sha256:355c096f03cb8352b14d3afce00f5065b88d7477e9ceaaf436e79668941ad315"),
        approval_reference=reference,
    )
    approval = concurrency._load_approval(
        Path(
            "docs/evidence/phase8/2026-08-21/"
            "gke-standard-rc30-postgresql-concurrency-objectives.json"
        ),
        config=config,
        identity=identity,
    )

    assert approval.objectives.benchmark_class is BenchmarkClass.CONCURRENT_PIPELINES
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")


def test_corrective_gke_concurrency_objective_binds_rc31_without_changing_workload() -> None:
    reference = "codex-goal-02043c37-096e-416a-875c-b405c4af0594-existing-gke-usd-0.50"
    config = concurrency.PostgreSQLConcurrencyConfig()
    identity = replace(
        _identity(),
        release_version="0.9.0rc31",
        git_commit="3d6a59484737bf1192f0389b8f93a3a24c780fc4",
        image_digest=("sha256:26dac10d6cd81eef15a96a26fb011c0266ed4de6e4e5b21f596185edd3c387c9"),
        approval_reference=reference,
    )
    objective_path = Path(
        "docs/evidence/phase8/2026-08-21/gke-standard-rc31-postgresql-concurrency-objectives.json"
    )
    approval = concurrency._load_approval(
        objective_path,
        config=config,
        identity=identity,
    )
    objective = json.loads(objective_path.read_text(encoding="utf-8"))

    assert approval.objectives.benchmark_class is BenchmarkClass.CONCURRENT_PIPELINES
    assert approval.objectives.configuration_sha256 == config.configuration_sha256()
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")
    assert objective["budget_allocation"]["run_ceiling_usd"] == "0.25"
    assert objective["corrective_basis"]["failed_candidate_executions"] == 1
    assert objective["corrective_basis"]["final_evidence_must_record_both_attempts"] is True
