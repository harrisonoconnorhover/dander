"""Credential-free checks for the Phase 8 Redshift bounded-memory harness."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from scripts.benchmarks import redshift as shared
from scripts.benchmarks import redshift_bounded_memory_phase8 as bounded
from scripts.benchmarks import redshift_bulk_phase8 as bulk

from dander import __version__
from dander.qualification import (
    ApprovedCostCeiling,
    ApprovedObjectiveSet,
    BenchmarkClass,
    QualificationReport,
)

_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-redshift-bounded-memory"


def _config(**overrides: object) -> bounded.RedshiftBoundedMemoryConfig:
    values: dict[str, object] = {
        "account_id": "123456789012",
        "host": "workgroup.123456789012.us-east-1.redshift-serverless.amazonaws.com",
        "database": "analytics",
        "region": "us-east-1",
        "workgroup_name": "dander-p8q-rc31-rs-bounded",
        "copy_role_arn": "arn:aws:iam::123456789012:role/dander-p8q-rc31-rs-bounded-redshift-copy",
        "staging_bucket": "dander-p8q-rc31-rs-bounded-123456789012-staging",
        "staging_prefix": "phase8/0.9.0rc31/staging",
        "rows": 20_000,
        "payload_bytes": 512,
        "copy_part_rows": 1_000,
        "memory_limit_mib": 1,
        "cost_observation_delay_seconds": 60,
    }
    values.update(overrides)
    return bounded.RedshiftBoundedMemoryConfig(**values)  # type: ignore[arg-type]


def _identity() -> bulk.CandidateIdentity:
    return bulk.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 22),
        launcher="aws_native_fargate",
        secret_provider="aws_task_role",
        service_shapes=("dander_2cpu_256mib_hard_limit", "redshift_serverless_8_rpu"),
        provider_job_ids=("task:test", "workgroup:test"),
    )


def _approval(config: bounded.RedshiftBoundedMemoryConfig) -> bounded._Approval:
    return bounded._Approval(
        objectives=ApprovedObjectiveSet(
            names=bounded._OBJECTIVES,
            benchmark_class=BenchmarkClass.BOUNDED_MEMORY,
            profile_id="aws_native_redshift",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(Decimal("0.50"), _REFERENCE),
    )


def _result(config: bounded.RedshiftBoundedMemoryConfig) -> bounded._BoundedResult:
    return bounded._BoundedResult(
        duration_ms=2_000,
        peak_rss_bytes=800_000,
        rows=config.rows,
        logical_input_bytes=config.logical_input_bytes,
        copy_operations=2,
        query_ids=("101", "102"),
        queue_duration_ms=3,
        load_duration_ms=1_800,
        bytes_processed=config.logical_input_bytes,
        spill_bytes=0,
        charged_seconds=Decimal("480"),
        compute_seconds=Decimal("478.5"),
        maximum_compute_capacity_rpu=Decimal("8"),
        provider_cost_usd=Decimal("0.05"),
        staging_tables=0,
        staging_objects=0,
        cleanup_verified=True,
    )


def test_run_reuses_streaming_copy_and_cleans_owned_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    runtime = object()
    writes: list[dict[str, object]] = []
    dropped: list[str] = []
    deleted: list[str] = []
    operation = SimpleNamespace(
        query_id="101",
        queue_duration_ms=0,
        duration_ms=1_500,
        bytes_processed=config.logical_input_bytes,
        spill_bytes=0,
    )

    monkeypatch.setattr(bulk, "_require_no_provider_retries", lambda: None)
    monkeypatch.setattr(bounded, "_require_container_memory_limit", lambda _config: None)
    monkeypatch.setattr(bulk, "_warehouse_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(bulk, "_peak_rss_bytes", lambda: 800_000)

    def write_table(_runtime: object, **kwargs: object) -> tuple[int, int, tuple[object, ...]]:
        writes.append(kwargs)
        return config.rows, 1_500, (operation,)

    monkeypatch.setattr(bulk, "_write_table", write_table)
    monkeypatch.setattr(bulk, "_require_table_shape", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bulk, "_staging_table_count", lambda *_args: 0)
    monkeypatch.setattr(
        bulk, "_serverless_usage", lambda _runtime: (Decimal("480"), Decimal("478.5"), Decimal("8"))
    )
    monkeypatch.setattr(shared, "_prefix_object_count", lambda *_args: 0)
    monkeypatch.setattr(shared, "_schema_exists", lambda *_args: False)
    monkeypatch.setattr(shared, "_drop_schema", lambda _runtime, schema: dropped.append(schema))
    monkeypatch.setattr(shared, "_delete_prefix", lambda _config, prefix: deleted.append(prefix))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    report = bounded.run_phase8_redshift_bounded_memory(
        config, identity=_identity(), approval=_approval(config)
    )

    assert isinstance(report, QualificationReport)
    assert report.status.value == "passed"
    assert len(writes) == 1
    assert writes[0]["rows"] == config.rows
    assert writes[0]["payload_bytes"] == config.payload_bytes
    assert dropped and deleted


def test_report_enforces_ratio_rss_cost_and_zero_retries() -> None:
    config = _config()
    report = bounded._report(config, _identity(), _approval(config), _result(config))
    payload = cast("dict[str, Any]", json.loads(report.to_json()))
    measurements = {
        item["name"]: item["value"]
        for item in cast("dict[str, Any]", payload["performance"])["measurements"]
    }

    assert payload["status"] == "passed"
    assert measurements["retries"] == "0"
    assert measurements["provider_operation_retries"] == "0"
    assert measurements["memory_limit_bytes"] == str(config.memory_limit_bytes)
    assert cast("dict[str, Any]", payload["performance"])["costs"] == [
        {
            "amount": "0.05",
            "currency": "USD",
            "estimated": False,
            "provider": "aws",
            "service": "redshift_serverless",
        }
    ]

    with pytest.raises(
        bounded.RedshiftBoundedMemoryQualificationError,
        match="peak RSS exceeds eighty percent",
    ):
        bounded._report(
            config,
            _identity(),
            _approval(config),
            replace(_result(config), peak_rss_bytes=900_000),
        )


def test_requires_the_approved_container_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()
    monkeypatch.setattr(bounded, "_container_memory_limit_bytes", lambda: config.memory_limit_bytes)
    bounded._require_container_memory_limit(config)

    monkeypatch.setattr(
        bounded, "_container_memory_limit_bytes", lambda: config.memory_limit_bytes * 2
    )
    with pytest.raises(
        bounded.RedshiftBoundedMemoryQualificationError,
        match="container memory limit",
    ):
        bounded._require_container_memory_limit(config)


def test_rc31_objective_does_not_authorize_changed_harness(tmp_path: Path) -> None:
    reference = "codex-goal-02043c37-096e-416a-875c-b405c4af0594-aws-redshift-bounded-usd-0.50"
    config = bounded.RedshiftBoundedMemoryConfig(
        account_id="184463061564",
        host="private-host",
        database="dev",
        region="us-east-1",
        workgroup_name="dander-p8q-rc31-rs-bounded",
        copy_role_arn=("arn:aws:iam::184463061564:role/dander-p8q-rc31-rs-bounded-redshift-copy"),
        staging_bucket="dander-p8q-rc31-rs-bounded-184463061564-staging",
        staging_prefix="phase8/0.9.0rc31/staging",
    )
    identity = replace(
        _identity(),
        release_version="0.9.0rc31",
        git_commit="3d6a59484737bf1192f0389b8f93a3a24c780fc4",
        image_digest=("sha256:26dac10d6cd81eef15a96a26fb011c0266ed4de6e4e5b21f596185edd3c387c9"),
        approval_reference=reference,
    )
    source = Path(
        "docs/evidence/phase8/2026-08-22/aws-native-rc31-redshift-bounded-memory-objectives.json"
    )
    payload = cast("dict[str, Any]", json.loads(source.read_text()))
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="protected harness"):
        bounded._load_approval(manifest, config=config, identity=identity)
