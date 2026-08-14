"""Contracts for the exact-candidate PostgreSQL Phase 8 benchmark harness."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts.benchmarks.postgresql_phase8 import Phase8PostgreSQLConfig, load_approval

from dander.qualification import BenchmarkClass

if TYPE_CHECKING:
    from pathlib import Path


def test_phase8_postgresql_config_hashes_each_class_deterministically() -> None:
    config = Phase8PostgreSQLConfig()

    bulk = config.configuration_sha256(BenchmarkClass.BULK_THROUGHPUT)
    incremental = config.configuration_sha256(BenchmarkClass.INCREMENTAL)

    assert len(bulk) == 64
    assert len(incremental) == 64
    assert bulk != incremental
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
    payload["workload"]["wide_rows"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="workload"):
        load_approval(
            path,
            config=config,
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
        )
    payload = _manifest(config)
    payload["approved_objectives"]["names"].pop()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="required objective set"):
        load_approval(
            path,
            config=config,
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
        )


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
            "configuration_sha256": config.configuration_sha256(
                BenchmarkClass.BULK_THROUGHPUT
            ),
            "approval_reference": approval,
        },
    }
