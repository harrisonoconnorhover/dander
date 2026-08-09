"""Smoke the reproducible PostgreSQL scale harness without claiming live qualification."""

from __future__ import annotations

import json
import os

import pytest
from scripts.benchmarks.postgresql import BenchmarkConfig, run_postgresql_benchmark


def test_postgresql_benchmark_streams_and_rejects_stale_publication() -> None:
    dsn = os.environ.get("DANDER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("DANDER_TEST_POSTGRES_DSN is not configured")

    report = run_postgresql_benchmark(
        dsn,
        BenchmarkConfig(
            rows=2_000,
            payload_bytes=256,
            batch_rows=100,
            concurrent_pipelines=3,
            concurrent_rows_per_pipeline=200,
        ),
    )
    payload = json.loads(report.to_json())

    assert payload["schema"] == "io.dander.benchmark.postgresql/v1"
    assert payload["provider"] == "postgresql"
    assert payload["rows"] == 2_000
    assert payload["concurrent_rows"] == 600
    assert payload["stale_publication_rejected"] is True
    assert payload["temporary_staging_relations"] == 0
    assert payload["qualification_status"] == "not_evaluated"
    assert "postgresql://" not in report.to_json()


def test_benchmark_qualification_is_explicit_not_inferred() -> None:
    with pytest.raises(ValueError, match="rows must be a positive integer"):
        BenchmarkConfig(rows=0)
    with pytest.raises(ValueError, match="batch must not exceed 256 MiB"):
        BenchmarkConfig(batch_rows=100_000, payload_bytes=4_096)
