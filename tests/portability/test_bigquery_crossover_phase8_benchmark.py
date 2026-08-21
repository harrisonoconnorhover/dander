"""Contracts for the exact-RC30 BigQuery crossover harness."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from scripts.benchmarks.bigquery_crossover_phase8 import (
    BigQueryCrossoverConfig,
    CandidateIdentity,
    _CrossoverResult,
    _expected_sha256,
    _load_approval,
    _MeasuredStorageBackend,
    _median_durations,
    _NoRetryStorageClient,
    _recommended_storage_write_max_rows,
    _report,
    _run_sample,
    _Sample,
)

from dander.concurrency import FencingToken
from dander.qualification import BenchmarkClass
from dander.writer import WriteField, WriteTarget, WriteTransport

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


class _ResultJob:
    def __init__(self, values: list[dict[str, object]]) -> None:
        self._values = values

    def result(self) -> list[dict[str, object]]:
        return self._values


class _SampleClient:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, object]]] = {}
        self.queries: list[str] = []

    def query(self, query: str, *, job_config: object | None = None) -> _ResultJob:
        assert job_config is not None
        self.queries.append(query)
        match = re.search(r"FROM `[^`]+\.([^`.]+)`$", query)
        assert match is not None
        rows = self.tables[match.group(1)]
        canonical = hashlib.sha256(
            b"".join(str(row["id"]).encode() + str(row["payload"]).encode() for row in rows)
        ).hexdigest()
        return _ResultJob(
            [
                {
                    "row_count": len(rows),
                    "distinct_row_count": len({row["id"] for row in rows}),
                    "payload_bytes": sum(len(str(row["payload"]).encode()) for row in rows),
                    "canonical_sha256": canonical.upper(),
                }
            ]
        )


class _SampleWriter:
    def __init__(self, client: _SampleClient) -> None:
        self.client = client
        self.targets: list[WriteTarget] = []

    def write(
        self,
        records: Iterable[Mapping[str, Any]],
        target: WriteTarget,
    ) -> int:
        rows = [dict(row) for row in records]
        self.client.tables[target.table] = rows
        self.targets.append(target)
        return len(rows)


class _Backend:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, Any]], WriteTarget, int]] = []

    def append(
        self,
        rows: Sequence[Mapping[str, Any]],
        target: WriteTarget,
        *,
        max_batch_rows: int,
    ) -> None:
        self.calls.append(([dict(row) for row in rows], target, max_batch_rows))


class _Transport:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StorageClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.transport = _Transport()

    def create_write_stream(self, *args: object, **kwargs: object) -> object:
        self.calls.append(("create", dict(kwargs)))
        return object()

    def finalize_write_stream(self, *args: object, **kwargs: object) -> object:
        self.calls.append(("finalize", dict(kwargs)))
        return object()

    def batch_commit_write_streams(self, *args: object, **kwargs: object) -> object:
        self.calls.append(("commit", dict(kwargs)))
        return object()

    def append_rows(self, *args: object, **kwargs: object) -> object:
        self.calls.append(("append", dict(kwargs)))
        return object()


def test_crossover_config_preserves_the_accepted_workload() -> None:
    config = _config()

    assert config.row_width_bytes == 149
    assert len(config.configuration_sha256()) == 64
    assert config.workload_payload() == {
        "schema": "io.dander.phase8.bigquery-crossover/v1",
        "benchmark_class": "crossover",
        "row_counts": [1, 10, 100, 1_000, 5_000],
        "payload_bytes": 128,
        "repetitions": 5,
        "batch_rows": 5_000,
        "write_mode": "scd1",
        "transports": ["load_job", "storage_write"],
        "verification_maximum_bytes_billed": 256 * 1_024 * 1_024,
    }
    with pytest.raises(ValueError, match="accepted crossover sizes"):
        _config(row_counts=(1, 10, 100))
    with pytest.raises(ValueError, match="accepted workload"):
        _config(payload_bytes=64)
    with pytest.raises(ValueError, match="contain each"):
        _config(batch_rows=1_000)


def test_crossover_approval_rejects_workload_and_cost_drift(tmp_path: Path) -> None:
    config = _config()
    identity = _identity()
    path = tmp_path / "objectives.json"
    payload = _manifest(config, identity)
    path.write_text(json.dumps(payload), encoding="utf-8")

    approval = _load_approval(path, config=config, identity=identity)

    assert approval.objectives.benchmark_class is BenchmarkClass.CROSSOVER
    assert approval.cost_ceiling.amount_usd == Decimal("0.25")
    workload = cast("dict[str, object]", payload["workload"])
    workload["repetitions"] = 3
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="workload"):
        _load_approval(path, config=config, identity=identity)

    payload = _manifest(config, identity)
    cost = cast("dict[str, object]", payload["cost_ceiling"])
    cost["amount_usd"] = "0.26"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="authorized maximum"):
        _load_approval(path, config=config, identity=identity)


def test_sample_requires_exact_readback_and_retains_the_fence() -> None:
    config = _config()
    client = _SampleClient()
    writer = _SampleWriter(client)
    lease = FencingToken(
        lease_table="unit-project.phase8._dander_leases",
        pipeline_id="crossover",
        run_id="run-one",
        token=1,
    )

    sample = _run_sample(
        writer,
        cast("Any", client),
        config=config,
        lease=lease,
        transport=WriteTransport.STORAGE_WRITE,
        rows=10,
        repetition=2,
    )

    assert sample.canonical_sha256 == _expected_sha256(10, 128)
    assert sample.transport is WriteTransport.STORAGE_WRITE
    assert writer.targets[0].fence is lease
    assert "AS canonical_sha256" in client.queries[0]


def test_crossover_medians_and_threshold_use_a_contiguous_prefix() -> None:
    row_counts = (1, 10, 100)
    samples = tuple(
        _Sample(transport, rows, repetition, duration, "a" * 64)
        for transport, rows, durations in (
            (WriteTransport.LOAD_JOB, 1, (4, 5, 6)),
            (WriteTransport.STORAGE_WRITE, 1, (2, 3, 4)),
            (WriteTransport.LOAD_JOB, 10, (6, 7, 8)),
            (WriteTransport.STORAGE_WRITE, 10, (6, 7, 8)),
            (WriteTransport.LOAD_JOB, 100, (8, 9, 10)),
            (WriteTransport.STORAGE_WRITE, 100, (10, 11, 12)),
        )
        for repetition, duration in enumerate(durations)
    )

    medians = _median_durations(samples, row_counts)

    assert medians[WriteTransport.LOAD_JOB] == {1: 5, 10: 7, 100: 9}
    assert medians[WriteTransport.STORAGE_WRITE] == {1: 3, 10: 7, 100: 11}
    assert _recommended_storage_write_max_rows(medians, row_counts) == 10


def test_storage_backend_measures_acknowledged_payload_and_disables_unary_retries() -> None:
    inner = _StorageClient()
    client = _NoRetryStorageClient(cast("Any", inner))
    backend = _MeasuredStorageBackend(client)
    fake_backend = _Backend()
    backend._backend = cast("Any", fake_backend)
    target = WriteTarget(
        project="unit-project",
        dataset="phase8",
        table="storage_target",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING"),
            WriteField(name="payload", data_type="STRING"),
        ),
    )

    backend.append(
        [{"id": "000000000000", "payload": "x" * 128}],
        target,
        max_batch_rows=5_000,
    )
    client.create_write_stream(parent="p", write_stream=object())
    client.finalize_write_stream(name="stream")
    client.batch_commit_write_streams(request=object())
    client.append_rows(iter(()))
    client.close()

    assert backend.append_requests == 1
    assert backend.serialized_bytes > 0
    assert [kwargs["retry"] for _, kwargs in inner.calls[:3]] == [None, None, None]
    assert client.append_stream_connections == 1
    assert inner.transport.closed


def test_report_records_both_transport_metrics_and_gross_cost(tmp_path: Path) -> None:
    config = _config()
    identity = _identity()
    path = tmp_path / "objectives.json"
    path.write_text(json.dumps(_manifest(config, identity)), encoding="utf-8")
    approval = _load_approval(path, config=config, identity=identity)
    samples = tuple(
        _Sample(transport, rows, repetition, 2, _expected_sha256(rows, 128))
        for rows in config.row_counts
        for repetition in range(config.repetitions)
        for transport in (WriteTransport.LOAD_JOB, WriteTransport.STORAGE_WRITE)
    )
    medians = _median_durations(samples, config.row_counts)

    report = _report(
        config,
        identity,
        approval,
        _CrossoverResult(
            duration_ms=100,
            peak_rss_bytes=1,
            samples=samples,
            medians=medians,
            recommended_storage_write_max_rows=5_000,
            recommended_storage_write_max_logical_bytes=745_000,
            fenced_publications=50,
            load_jobs=25,
            query_jobs=152,
            bytes_processed=10,
            bytes_billed=20,
            slot_ms=30,
            reservation_usage_records=0,
            storage_write_append_requests=25,
            storage_write_serialized_bytes=40,
            provider_operation_retries=0,
            job_ids=("query-job",),
            temporary_staging_relations=0,
            cleanup_verified=True,
        ),
    )

    names = [metric.name for metric in report.performance.provider_metrics]
    assert names == sorted(names)
    assert "load_job_1_median_duration_ms" in names
    assert "storage_write_5000_median_duration_ms" in names
    assert report.performance.costs[0].estimated is False


def _config(**changes: object) -> BigQueryCrossoverConfig:
    values: dict[str, object] = {
        "project": "unit-project",
        "dataset": "phase8",
    }
    values.update(changes)
    return BigQueryCrossoverConfig(**cast("Any", values))


def _identity() -> CandidateIdentity:
    return CandidateIdentity(
        release_version="0.9.0rc30",
        git_commit="a" * 40,
        image_digest=f"sha256:{'b' * 64}",
        approval_reference="codex-goal-bigquery-crossover",
        benchmark_date=date(2026, 8, 21),
        launcher="docker_local",
        secret_provider="ephemeral_access_token",
        service_shapes=("dander_2cpu_512mib_read_only",),
    )


def _manifest(
    config: BigQueryCrossoverConfig,
    identity: CandidateIdentity,
) -> dict[str, object]:
    return {
        "schema": "io.dander.qualification.objective-approval/v1",
        "cost_ceiling": {
            "amount_usd": "0.25",
            "approval_reference": identity.approval_reference,
        },
        "workload": config.workload_payload(),
        "configuration": {
            "bigquery": {
                "project_sha256": hashlib.sha256(config.project.encode()).hexdigest(),
                "dataset": config.dataset,
                "location": config.location,
                "on_demand_rate_usd_per_tib": "6.25",
                "storage_write_rate_usd_per_gib": "0.025",
            },
            "execution": {
                "harness_sha256": hashlib.sha256(
                    Path("scripts/benchmarks/bigquery_crossover_phase8.py").read_bytes()
                ).hexdigest(),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
            },
        },
        "approved_objectives": {
            "names": [
                "canonical_equality",
                "cleanup",
                "cost_ceiling",
                "crossover_measured",
                "fenced_publication",
                "load_job_transport_observed",
                "storage_write_transport_observed",
                "threshold_recorded",
            ],
            "benchmark_class": "crossover",
            "profile_id": "bigquery_local_scale",
            "release_version": identity.release_version,
            "git_commit": identity.git_commit,
            "image_digest": identity.image_digest,
            "configuration_sha256": config.configuration_sha256(),
            "approval_reference": identity.approval_reference,
        },
    }
