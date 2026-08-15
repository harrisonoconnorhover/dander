"""Contracts for the exact-candidate PostgreSQL Phase 8 benchmark harness."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from scripts.benchmarks.postgresql_phase8 import (
    Phase8PostgreSQLConfig,
    _write_transform_models,
    load_approval,
)

from dander.qualification import BenchmarkClass
from dander.transform import SqlDialect, TransformProject

if TYPE_CHECKING:
    from pathlib import Path


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
