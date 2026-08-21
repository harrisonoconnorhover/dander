"""Credential-free checks for the Phase 8 BigQuery correctness harness."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from scripts.benchmarks import bigquery_correctness_phase8 as correctness
from scripts.benchmarks import bigquery_incremental_phase8 as common

from dander import __version__
from dander.concurrency import FencingToken
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass

_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-existing-bigquery-usd-0.25"
_EXPECTED_SHA256 = "82886fc4c0bc5cfb248df1196b9d29763cad4fac60cf248a91084a185d78c2ee"


class _DatasetClient:
    def __init__(self) -> None:
        self.datasets: set[str] = set()
        self.jobs: list[object] = []
        self.queries = ["Dander pipeline lease lost"] * 3

    def create_dataset(self, dataset: bigquery.Dataset) -> bigquery.Dataset:
        self.datasets.add(str(dataset.reference))
        return dataset

    def get_dataset(self, dataset: str) -> bigquery.Dataset:
        if dataset not in self.datasets:
            raise NotFound("synthetic absent dataset")  # type: ignore[no-untyped-call]
        return bigquery.Dataset(dataset)

    def delete_dataset(self, dataset: str, *, not_found_ok: bool) -> None:
        assert not_found_ok
        self.datasets.discard(dataset)

    def list_tables(self, dataset: str) -> list[object]:
        assert dataset in self.datasets
        return []


def _config(**overrides: object) -> correctness.BigQueryCorrectnessConfig:
    values: dict[str, object] = {
        "project": "valid-project-123",
        "dataset": "dander_p8_correctness_test",
    }
    values.update(overrides)
    return correctness.BigQueryCorrectnessConfig(**values)  # type: ignore[arg-type]


def _identity() -> correctness.CandidateIdentity:
    return correctness.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 21),
        launcher="docker_local",
        secret_provider="ephemeral_access_token",
        service_shapes=("bigquery_on_demand", "dander_2cpu_512mib"),
    )


def _approval(config: correctness.BigQueryCorrectnessConfig) -> correctness._Approval:
    return correctness._Approval(
        objectives=ApprovedObjectiveSet(
            names=correctness._OBJECTIVES,
            benchmark_class=BenchmarkClass.CORRECTNESS,
            profile_id="bigquery_local_scale",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(Decimal("0.25"), _REFERENCE),
        project_sha256=common._identifier_sha256(config.project),
        dataset=config.dataset,
        location=config.location,
        on_demand_rate_usd_per_tib=Decimal("6.25"),
    )


def _result() -> correctness._CorrectnessResult:
    return correctness._CorrectnessResult(
        duration_ms=1_000,
        peak_rss_bytes=128 * 1_024 * 1_024,
        input_rows=7,
        output_rows=3,
        logical_input_bytes=317,
        normalized_sha256=correctness._correctness_expected_sha256(),
        affected_rows=6,
        fenced_publications=3,
        load_jobs=3,
        query_jobs=10,
        bytes_processed=20 * 1_024 * 1_024,
        bytes_billed=20 * 1_024 * 1_024,
        slot_ms=9,
        reservation_usage_records=0,
        job_ids=("job-one",),
        provider_operation_retries=0,
        temporary_staging_relations=0,
        cleanup_verified=True,
    )


def _manifest(config: correctness.BigQueryCorrectnessConfig) -> dict[str, object]:
    return {
        "schema": "io.dander.qualification.objective-approval/v1",
        "cost_ceiling": {
            "amount_usd": "0.25",
            "approval_reference": _REFERENCE,
        },
        "workload": config.workload_payload(),
        "configuration": {
            "bigquery": {
                "project_sha256": common._identifier_sha256(config.project),
                "dataset": config.dataset,
                "location": config.location,
                "on_demand_rate_usd_per_tib": "6.25",
            },
            "execution": {
                "harness_sha256": common._file_sha256(Path(correctness.__file__)),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
            },
        },
        "approved_objectives": {
            "names": list(correctness._OBJECTIVES),
            "benchmark_class": "correctness",
            "profile_id": "bigquery_local_scale",
            "release_version": __version__,
            "git_commit": _COMMIT,
            "image_digest": _DIGEST,
            "configuration_sha256": config.configuration_sha256(),
            "approval_reference": _REFERENCE,
        },
    }


def test_config_binds_the_exact_normalized_replay_fixture() -> None:
    config = _config()

    assert config.workload_payload() == {
        "schema": "io.dander.phase8.bigquery-correctness/v1",
        "benchmark_class": "correctness",
        "initial_rows": 3,
        "update_rows": 2,
        "replay_rows": 2,
        "expected_output_rows": 3,
        "expected_normalized_sha256": _EXPECTED_SHA256,
        "write_mode": "scd1",
        "batch_rows": 3,
        "verification_maximum_bytes_billed": 64 * 1_024 * 1_024,
    }
    with pytest.raises(ValueError, match="exactly 3"):
        replace(config, batch_rows=2)


def test_correctness_exercise_uses_exact_fixture_replay_and_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    expected = correctness._correctness_fixture()[2]
    lease = FencingToken(
        lease_table=f"{config.project}.{config.dataset}._dander_leases",
        pipeline_id="phase8_bigquery_correctness",
        run_id="correctness-one",
        token=1,
        authority_id="bigquery:phase8-correctness",
    )
    writes: list[tuple[tuple[dict[str, object], ...], object]] = []

    class _Writer:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["max_batch_rows"] == 3

        def write(self, records: object, target: object) -> int:
            batch = tuple(cast("tuple[dict[str, object], ...]", records))
            writes.append((batch, target))
            return len({row["id"] for row in batch})

    monkeypatch.setattr(correctness, "BigQueryScd1Writer", _Writer)
    monkeypatch.setattr(correctness, "_create_lease", lambda _config, _client: lease)
    readbacks = iter((expected, expected))
    monkeypatch.setattr(
        correctness,
        "_read_correctness_rows",
        lambda _config, _client: next(readbacks),
    )

    affected, before, after = correctness._exercise_correctness(
        config,
        cast("Any", object()),
    )

    initial, update, _ = correctness._correctness_fixture()
    assert [batch for batch, _target in writes] == [initial, update, update]
    assert all(cast("Any", target).fence == lease for _batch, target in writes)
    assert affected == 6
    assert before == expected
    assert after == expected


def test_approval_requires_exact_harness_one_execution_and_zero_retries(
    tmp_path: Path,
) -> None:
    config = _config()
    payload = _manifest(config)
    path = tmp_path / "objectives.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    approval = correctness._load_approval(path, config=config, identity=_identity())

    assert approval.cost_ceiling.amount_usd == Decimal("0.25")
    execution = cast(
        "dict[str, Any]", cast("dict[str, Any]", payload["configuration"])["execution"]
    )
    execution["provider_operation_retries"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="disable provider-operation retries"):
        correctness._load_approval(path, config=config, identity=_identity())


def test_report_requires_exact_output_replay_fencing_cost_and_cleanup() -> None:
    config = _config()

    report = correctness._report(config, _identity(), _approval(config), _result())

    payload = json.loads(report.to_json())
    assert payload["status"] == "passed"
    assert payload["workload"]["benchmark_class"] == "correctness"
    assert payload["performance"]["costs"] == [
        {
            "amount": "0.000119209290",
            "currency": "USD",
            "estimated": False,
            "provider": "gcp",
            "service": "bigquery_on_demand_analysis_gross",
        }
    ]
    objectives = {item["name"]: item for item in payload["objectives"]}
    assert objectives["exact_normalized_output"]["evidence_reference"].endswith(
        correctness._correctness_expected_sha256()
    )
    for incomplete in (
        replace(_result(), output_rows=2),
        replace(_result(), fenced_publications=2),
        replace(_result(), provider_operation_retries=1),
        replace(_result(), temporary_staging_relations=1),
        replace(_result(), cleanup_verified=False),
    ):
        with pytest.raises(
            correctness.BigQueryCorrectnessQualificationError,
            match="incomplete",
        ):
            correctness._report(config, _identity(), _approval(config), incomplete)


def test_provider_failure_still_deletes_the_owned_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    client = _DatasetClient()

    def fail_execution(*_arguments: object) -> None:
        raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr(correctness, "_exercise_correctness", fail_execution)

    with pytest.raises(
        correctness.BigQueryCorrectnessQualificationError,
        match="failed before report completion; cleanup passed",
    ):
        correctness._run_correctness(config, cast("Any", client))

    assert not client.datasets
