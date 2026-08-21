"""Credential-free checks for the Snowflake bounded-memory harness."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from scripts.benchmarks import snowflake_bounded_memory_phase8 as bounded
from scripts.benchmarks import snowflake_bulk_phase8 as bulk

from dander import __version__
from dander.qualification import (
    ApprovedCostCeiling,
    ApprovedObjectiveSet,
    BenchmarkClass,
)

_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-snowflake-bounded"


def _config(**overrides: object) -> bounded.SnowflakeBoundedMemoryConfig:
    values: dict[str, object] = {
        "account": "org-account",
        "user": "DANDER_USER",
        "database": "DANDER_BOUNDED_TEST",
        "warehouse": "DANDER_BOUNDED_WH",
        "role": "DANDER_BOUNDED_ROLE",
        "rows": 20_000,
        "payload_bytes": 512,
        "copy_part_rows": 1_000,
        "memory_limit_mib": 1,
    }
    values.update(overrides)
    return bounded.SnowflakeBoundedMemoryConfig(**values)  # type: ignore[arg-type]


def _identity() -> bulk.CandidateIdentity:
    return bulk.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 21),
        launcher="docker_local",
        regions=("local",),
        secret_provider="environment",
        provider_job_ids=("container:test",),
        service_shapes=("dander_2cpu_256mib", "snowflake_xsmall"),
    )


def _approval(config: bounded.SnowflakeBoundedMemoryConfig) -> bulk._Approval:
    return bulk._Approval(
        objectives=ApprovedObjectiveSet(
            names=bounded._OBJECTIVES,
            benchmark_class=BenchmarkClass.BOUNDED_MEMORY,
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


def _result(config: bounded.SnowflakeBoundedMemoryConfig) -> bounded._BoundedMemoryResult:
    return bounded._BoundedMemoryResult(
        duration_ms=2_000,
        peak_rss_bytes=800_000,
        rows=config.rows,
        logical_input_bytes=config.logical_input_bytes,
        copy_operations=2,
        query_ids=("query-one", "query-two"),
        staging_tables=0,
        staging_stages=0,
        cleanup_verified=True,
    )


def test_bounded_memory_run_reuses_streaming_writer_and_cleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    identity = _identity()
    approval = _approval(config)
    runtime = object()
    dropped: list[tuple[object, str, str]] = []
    write_arguments: dict[str, object] = {}

    monkeypatch.setattr(bulk, "_warehouse_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(bulk, "_peak_rss_bytes", lambda: 800_000)

    def write_table(_runtime: object, **kwargs: object) -> tuple[int, int, tuple[object, ...]]:
        write_arguments.update(kwargs)
        return config.rows, 1_500, (SimpleNamespace(query_id="query-one"),)

    monkeypatch.setattr(bulk, "_write_table", write_table)
    monkeypatch.setattr(bulk, "_require_table_shape", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bulk, "_staging_residue", lambda *_args: (0, 0))
    monkeypatch.setattr(
        bulk,
        "_drop_schema",
        lambda used_runtime, database, schema: dropped.append((used_runtime, database, schema)),
    )
    monkeypatch.setattr(bulk, "_schema_exists", lambda *_args: False)

    result = bounded._run_bounded_memory(config, identity=identity, approval=approval)

    assert result.rows == config.rows
    assert result.query_ids == ("query-one",)
    assert result.cleanup_verified
    assert write_arguments["rows"] == config.rows
    assert write_arguments["copy_part_rows"] == config.copy_part_rows
    assert write_arguments["authority_id"] == bounded._AUTHORITY_ID
    assert dropped and dropped[0][0] is runtime


def test_bounded_memory_report_enforces_ratio_rss_cost_and_zero_retries() -> None:
    config = _config()
    result = _result(config)

    report = bounded._report(
        config,
        _identity(),
        _approval(config),
        result,
        provider_cost_usd=Decimal("0.05"),
    )
    payload = report.to_payload()
    performance = cast("dict[str, Any]", payload["performance"])
    measurements = cast("list[dict[str, Any]]", performance["measurements"])
    measured = {item["name"]: item["value"] for item in measurements}

    assert payload["status"] == "passed"
    assert measured["retries"] == "0"
    assert measured["snowflake_provider_operation_retries"] == "0"
    assert performance["costs"] == [
        {
            "provider": "snowflake",
            "service": "virtual_warehouse",
            "amount": "0.05",
            "currency": "USD",
            "estimated": False,
        }
    ]

    with pytest.raises(
        bounded.SnowflakeBoundedMemoryQualificationError,
        match="peak RSS exceeds eighty percent",
    ):
        bounded._report(
            config,
            _identity(),
            _approval(config),
            replace(result, peak_rss_bytes=900_000),
            provider_cost_usd=Decimal("0.05"),
        )


def test_bounded_memory_requires_the_approved_container_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    monkeypatch.setattr(bounded, "_container_memory_limit_bytes", lambda: config.memory_limit_bytes)
    bounded._require_container_memory_limit(config)

    monkeypatch.setattr(
        bounded, "_container_memory_limit_bytes", lambda: config.memory_limit_bytes * 2
    )
    with pytest.raises(
        bounded.SnowflakeBoundedMemoryQualificationError,
        match="container memory limit",
    ):
        bounded._require_container_memory_limit(config)


def test_bounded_memory_objective_binds_candidate_harness_and_dependency(
    tmp_path: Path,
) -> None:
    reference = "codex-goal-02043c37-096e-416a-875c-b405c4af0594-existing-snowflake-usd-0.50"
    config = bounded.SnowflakeBoundedMemoryConfig(
        account="expected-account",
        user="EXPECTED_USER",
        database="DANDER_P8_RC30_BOUNDED_21A2026M",
        warehouse="DANDER_P8_RC30_BOUNDED_21A2026M_WH",
        role="DANDER_P8_RC30_BOUNDED_21A2026M_ROLE",
    )
    identity = replace(
        _identity(),
        git_commit="d27dd880fc7676c15969bff76aaabb64c22be7c2",
        image_digest=("sha256:355c096f03cb8352b14d3afce00f5065b88d7477e9ceaaf436e79668941ad315"),
        approval_reference=reference,
    )
    source = Path("docs/evidence/phase8/2026-08-21/snowflake-rc30-bounded-memory-objectives.json")
    payload = cast("dict[str, Any]", json.loads(source.read_text()))
    payload["configuration"]["snowflake"]["account_sha256"] = bulk._identifier_sha256(
        config.account
    )
    payload["configuration"]["snowflake"]["operator_user_sha256"] = bulk._identifier_sha256(
        config.user
    )

    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    approval = bounded._load_approval(manifest, config=config, identity=identity)

    assert approval.objectives.benchmark_class is BenchmarkClass.BOUNDED_MEMORY
    assert approval.cost_ceiling.amount_usd == Decimal("0.50")
