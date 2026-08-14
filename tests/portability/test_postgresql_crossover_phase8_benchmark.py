"""Contracts for the exact-candidate PostgreSQL crossover harness."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from scripts.benchmarks.postgresql_crossover_phase8 import (
    PostgreSQLCrossoverConfig,
    _median_durations,
    _Sample,
    load_approval,
)

from dander.qualification import BenchmarkClass
from dander.writer import WriteTransport

if TYPE_CHECKING:
    from pathlib import Path


def test_crossover_config_is_deterministic_and_bounded() -> None:
    config = PostgreSQLCrossoverConfig()

    assert len(config.configuration_sha256()) == 64
    assert config.workload_payload() == {
        "schema": "io.dander.phase8.postgresql-crossover/v1",
        "benchmark_class": "crossover",
        "row_counts": [1, 10, 100, 1_000, 5_000],
        "payload_bytes": 128,
        "repetitions": 5,
        "direct_max_rows": 5_000,
        "direct_max_logical_bytes": 1_024 * 1_024,
        "write_mode": "scd1",
        "transports": ["copy", "direct"],
    }
    with pytest.raises(ValueError, match="ascending"):
        PostgreSQLCrossoverConfig(row_counts=(10, 1))
    with pytest.raises(ValueError, match="odd integer"):
        PostgreSQLCrossoverConfig(repetitions=4)
    with pytest.raises(ValueError, match="does not fit"):
        PostgreSQLCrossoverConfig(row_counts=(10_000,), payload_bytes=128)


def test_crossover_approval_rejects_workload_and_objective_drift(tmp_path: Path) -> None:
    config = PostgreSQLCrossoverConfig()
    path = tmp_path / "objectives.json"
    payload = _manifest(config)
    path.write_text(json.dumps(payload), encoding="utf-8")

    approval = load_approval(path, config=config)

    assert approval.objectives.benchmark_class is BenchmarkClass.CROSSOVER
    workload = cast("dict[str, object]", payload["workload"])
    workload["payload_bytes"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="workload"):
        load_approval(path, config=config)

    payload = _manifest(config)
    objectives = cast("dict[str, object]", payload["approved_objectives"])
    names = cast("list[str]", objectives["names"])
    names.pop()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="objective set"):
        load_approval(path, config=config)


def test_crossover_medians_are_transport_and_size_specific() -> None:
    samples = tuple(
        _Sample(transport, rows, repetition, duration, "a" * 64)
        for transport, rows, durations in (
            (WriteTransport.COPY, 1, (5, 3, 4)),
            (WriteTransport.DIRECT, 1, (2, 1, 3)),
            (WriteTransport.COPY, 10, (6, 7, 8)),
            (WriteTransport.DIRECT, 10, (9, 8, 10)),
        )
        for repetition, duration in enumerate(durations)
    )

    medians = _median_durations(samples, (1, 10))

    assert medians == {
        WriteTransport.COPY: {1: 4, 10: 7},
        WriteTransport.DIRECT: {1: 2, 10: 9},
    }


def _manifest(config: PostgreSQLCrossoverConfig) -> dict[str, object]:
    approval = "codex-thread-phase8-crossover-2026-08-14"
    return {
        "schema": "io.dander.qualification.objective-approval/v1",
        "cost_ceiling": {"amount_usd": "0.00", "approval_reference": approval},
        "workload": config.workload_payload(),
        "approved_objectives": {
            "names": [
                "canonical_equality",
                "cleanup",
                "copy_transport_observed",
                "cost_ceiling",
                "crossover_measured",
                "direct_transport_observed",
                "threshold_recorded",
            ],
            "benchmark_class": "crossover",
            "profile_id": "postgresql_local_scale",
            "release_version": "0.9.0rc23",
            "git_commit": "a" * 40,
            "image_digest": f"sha256:{'b' * 64}",
            "configuration_sha256": config.configuration_sha256(),
            "approval_reference": approval,
        },
    }
