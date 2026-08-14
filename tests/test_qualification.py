"""Fail-closed Phase 8 qualification report contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from dander.qualification import (
    ApprovedCostCeiling,
    BenchmarkClass,
    BenchmarkWorkload,
    ObjectiveResult,
    ObjectiveStatus,
    QualificationContext,
    QualificationReport,
    QualificationStatus,
)
from dander.telemetry import CostAttribution, PerformanceMeasurement, RunPerformance


def test_complete_bounded_memory_report_serializes_deterministically() -> None:
    report = _report()

    payload = json.loads(report.to_json())

    assert payload["schema"] == "io.dander.qualification.report/v1"
    assert payload["status"] == "passed"
    assert payload["context"]["cost_ceiling"] == {
        "amount_usd": "0",
        "approval_reference": "phase8/local-zero-cost",
    }
    assert payload["workload"]["benchmark_class"] == "bounded_memory"
    assert payload["performance"]["costs"][0]["amount"] == "0"


def test_pass_rejects_unavailable_common_metric_or_unmeasured_cost() -> None:
    incomplete = replace(
        _performance(),
        queue_duration_ms=PerformanceMeasurement.unavailable(
            "queue_duration_ms",
            "milliseconds",
            "provider_metric_unavailable",
        ),
    )
    with pytest.raises(ValueError, match="every common metric"):
        _report(performance=incomplete)
    with pytest.raises(ValueError, match="explicit cost evidence"):
        _report(performance=replace(_performance(), costs=()))


def test_pass_rejects_incomplete_context_or_workload() -> None:
    with pytest.raises(ValueError, match="candidate and provider context"):
        _report(context=replace(_context(), image_digest=None, provider_job_ids=()))
    with pytest.raises(ValueError, match="every workload dimension"):
        _report(workload=replace(_workload(), schema_depth=None))


def test_pass_rejects_missing_or_failed_objectives_and_cost_overrun() -> None:
    with pytest.raises(ValueError, match="every objective"):
        _report(
            objectives=(
                ObjectiveResult(
                    "memory_bound",
                    ObjectiveStatus.NOT_EVALUATED,
                    "phase8/objectives/memory",
                ),
            )
        )
    costly = replace(
        _performance(),
        costs=(CostAttribution("local", "postgresql", Decimal("0.01"), estimated=False),),
    )
    with pytest.raises(ValueError, match="exceeds"):
        _report(performance=costly)
    estimated = replace(
        _performance(),
        costs=(CostAttribution("local", "postgresql", Decimal(0), estimated=True),),
    )
    with pytest.raises(ValueError, match="measured cost evidence"):
        _report(performance=estimated)


def test_bounded_memory_pass_enforces_input_ratio_and_peak_limit() -> None:
    with pytest.raises(ValueError, match="ten times"):
        _report(workload=replace(_workload(), logical_input_bytes=9_999))
    excessive_peak = replace(
        _performance(),
        peak_rss_bytes=PerformanceMeasurement.measured("peak_rss_bytes", "bytes", 801),
    )
    with pytest.raises(ValueError, match="80 percent"):
        _report(performance=excessive_peak)


def test_partial_historical_report_stays_not_evaluated() -> None:
    report = _report(
        status=QualificationStatus.NOT_EVALUATED,
        context=replace(
            _context(),
            release_version=None,
            git_commit=None,
            image_digest=None,
            benchmark_date=None,
            regions=(),
            service_shapes=(),
            provider_job_ids=(),
            cost_ceiling=None,
        ),
        workload=replace(
            _workload(),
            schema_depth=None,
            source_rate_limit=None,
            configuration_sha256=None,
        ),
        performance=replace(
            _performance(),
            peak_rss_bytes=PerformanceMeasurement.unavailable(
                "peak_rss_bytes",
                "bytes",
                "historical_limit_not_enforced",
            ),
            costs=(),
        ),
        objectives=(
            ObjectiveResult(
                "memory_bound",
                ObjectiveStatus.NOT_EVALUATED,
                "phase8/historical/postgresql",
            ),
        ),
    )

    assert json.loads(report.to_json())["status"] == "not_evaluated"


def _report(
    *,
    status: QualificationStatus = QualificationStatus.PASSED,
    context: QualificationContext | None = None,
    workload: BenchmarkWorkload | None = None,
    performance: RunPerformance | None = None,
    objectives: tuple[ObjectiveResult, ...] | None = None,
) -> QualificationReport:
    return QualificationReport(
        context=context or _context(),
        workload=workload or _workload(),
        performance=performance or _performance(),
        objectives=objectives
        or (
            ObjectiveResult(
                "cost_ceiling",
                ObjectiveStatus.PASSED,
                "phase8/objectives/cost",
            ),
            ObjectiveResult(
                "memory_bound",
                ObjectiveStatus.PASSED,
                "phase8/objectives/memory",
            ),
        ),
        status=status,
    )


def _context() -> QualificationContext:
    return QualificationContext(
        release_version="0.9.0rc17",
        git_commit="a" * 40,
        image_digest=f"sha256:{'b' * 64}",
        benchmark_date=date(2026, 8, 13),
        profile_id="kubernetes_portable",
        launcher="kubernetes",
        warehouse="postgresql",
        state_backend="postgresql",
        catalog="none",
        secret_provider="environment",
        regions=("local",),
        service_shapes=("kind_postgresql_15",),
        provider_job_ids=("job-123",),
        cost_ceiling=ApprovedCostCeiling(Decimal(0), "phase8/local-zero-cost"),
    )


def _workload() -> BenchmarkWorkload:
    return BenchmarkWorkload(
        benchmark_class=BenchmarkClass.BOUNDED_MEMORY,
        input_rows=100,
        logical_input_bytes=10_000,
        row_width_bytes=100,
        schema_depth=1,
        source_rate_limit="unlimited",
        transform_complexity="none",
        concurrency=1,
        batch_rows=10,
        batch_bytes=1_000,
        configuration_sha256="c" * 64,
        memory_limit_bytes=1_000,
    )


def _performance() -> RunPerformance:
    measured = PerformanceMeasurement.measured
    return RunPerformance(
        rows=measured("rows", "rows", 100),
        logical_bytes=measured("logical_bytes", "bytes", 10_000),
        duration_ms=measured("duration_ms", "milliseconds", 1_000),
        throughput_rows_per_second=measured("throughput_rows_per_second", "rows_per_second", 100),
        peak_rss_bytes=measured("peak_rss_bytes", "bytes", 800),
        retries=measured("retries", "count", 0),
        queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
        load_duration_ms=measured("load_duration_ms", "milliseconds", 900),
        transform_duration_ms=measured("transform_duration_ms", "milliseconds", 100),
        catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
        costs=(CostAttribution("local", "postgresql", Decimal(0), estimated=False),),
    )
