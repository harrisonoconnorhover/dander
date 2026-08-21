"""Credential-free checks for the Phase 8 BigQuery transform harness."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from scripts.benchmarks import bigquery_incremental_phase8 as common
from scripts.benchmarks import bigquery_transform_phase8 as transform

from dander import __version__
from dander.concurrency import FencingToken
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass
from dander.transform import BigQueryTransformRunner, TransformProject

if TYPE_CHECKING:
    from collections.abc import Iterable


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-user-message-2026-08-21-dander-204-bigquery-transform-rc30-usd-0.25"


class _FakeJob:
    def __init__(self, rows: Iterable[object] = ()) -> None:
        self._rows = tuple(rows)

    def result(self) -> tuple[object, ...]:
        return self._rows


class _AssertionClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _FakeJob:
        del job_config
        self.queries.append(query)
        if " AS failures" in query:
            return _FakeJob(({"failures": 0},))
        return _FakeJob()


class _DatasetClient:
    def __init__(self) -> None:
        self.datasets: set[str] = set()
        self.jobs: list[object] = []
        self.queries: list[str] = []

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


def _config() -> transform.BigQueryTransformConfig:
    return transform.BigQueryTransformConfig(
        project="unit-project",
        dataset="dander_p8_rc30_bigquery_transform_unit",
    )


def _identity() -> transform.CandidateIdentity:
    return transform.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 21),
        launcher="docker_local",
        secret_provider="ephemeral_access_token",
        service_shapes=("bigquery_on_demand", "dander_2cpu_512mib"),
    )


def _manifest(config: transform.BigQueryTransformConfig) -> dict[str, object]:
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
                "harness_sha256": common._file_sha256(Path(transform.__file__)),
                "manual_candidate_executions": 1,
                "automatic_candidate_retry": False,
                "provider_operation_retries": 0,
            },
        },
        "approved_objectives": {
            "names": list(transform._OBJECTIVES),
            "benchmark_class": "transform",
            "profile_id": "bigquery_local_scale",
            "release_version": __version__,
            "git_commit": _COMMIT,
            "image_digest": _DIGEST,
            "configuration_sha256": config.configuration_sha256(),
            "approval_reference": _REFERENCE,
        },
    }


def _approval(config: transform.BigQueryTransformConfig) -> transform._Approval:
    return transform._Approval(
        objectives=ApprovedObjectiveSet(
            names=transform._OBJECTIVES,
            benchmark_class=BenchmarkClass.TRANSFORM,
            profile_id="bigquery_local_scale",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(
            amount_usd=Decimal("0.25"),
            approval_reference=_REFERENCE,
        ),
        project_sha256=common._identifier_sha256(config.project),
        dataset=config.dataset,
        location=config.location,
        on_demand_rate_usd_per_tib=Decimal("6.25"),
    )


def _result() -> transform._TransformResult:
    return transform._TransformResult(
        duration_ms=2_000,
        load_duration_ms=500,
        transform_duration_ms=1_500,
        peak_rss_bytes=128 * 1_024 * 1_024,
        input_rows=100_100,
        output_rows=100_001,
        logical_input_bytes=3_202_400,
        model_count=4,
        assertion_count=21,
        ownership_verifications=22,
        fenced_publications=4,
        load_jobs=11,
        query_jobs=42,
        bytes_processed=200 * 1_024 * 1_024,
        bytes_billed=250 * 1_024 * 1_024,
        slot_ms=123,
        reservation_usage_records=0,
        job_ids=("job-one",),
        provider_operation_retries=0,
        temporary_staging_relations=0,
        cleanup_verified=True,
    )


def test_config_binds_the_exact_accepted_transform_shape() -> None:
    config = _config()

    assert config.workload_payload() == {
        "schema": "io.dander.phase8.bigquery-transform/v1",
        "benchmark_class": "transform",
        "fact_rows": 100_000,
        "dimension_rows": 100,
        "delta_rows": 2,
        "models": ["scan", "join", "aggregation", "incremental_merge"],
        "generic_tests": ["accepted_values", "not_null", "unique"],
        "generic_assertions": 21,
        "batch_rows": 10_000,
        "verification_maximum_bytes_billed": 536_870_912,
    }
    with pytest.raises(ValueError, match="exactly 100000"):
        replace(config, fact_rows=99_999)
    with pytest.raises(ValueError, match="exactly 100"):
        replace(config, dimension_rows=99)


def test_transform_fixture_compiles_four_models_and_runs_exactly_21_assertions(
    tmp_path: Path,
) -> None:
    config = _config()
    transform._write_transform_models(tmp_path, target_dataset=config.dataset)
    project = TransformProject.load(
        tmp_path,
        catalog=config.project,
        raw_namespace=config.dataset,
    )
    client = _AssertionClient()
    ownership = transform._Ownership(
        FencingToken(
            lease_table=f"{config.project}.{config.dataset}._dander_leases",
            pipeline_id="phase8_bigquery_transform",
            run_id="transform-one",
            token=1,
            authority_id="bigquery:phase8-transform",
        )
    )
    runner = BigQueryTransformRunner(
        project=config.project,
        raw_namespace=config.dataset,
        client=cast("Any", client),
    )

    initial = runner.build(
        tmp_path,
        selected=("aggregate_records", "incremental_records"),
        ownership=ownership,
    )
    replay = runner.build(
        tmp_path,
        selected=("incremental_records",),
        ownership=ownership,
    )
    tested = runner.test(
        tmp_path,
        selected=("aggregate_records", "incremental_records"),
    )

    assert tuple(model.name for model in project.ordered()) == (
        "scan_records",
        "joined_records",
        "aggregate_records",
        "incremental_records",
    )
    assert initial.assertions + replay.assertions + tested.assertions == 21
    assert len({*initial.models, *replay.models, *tested.models}) == 4
    assert ownership.verifications == 22
    assert sum("Dander pipeline lease lost" in query for query in client.queries) == 2
    assert any("MERGE" in query and "incremental_records" in query for query in client.queries)


def test_approval_requires_one_execution_zero_retries_and_exact_harness(
    tmp_path: Path,
) -> None:
    config = _config()
    payload = _manifest(config)
    path = tmp_path / "objectives.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    approval = transform._load_approval(path, config=config, identity=_identity())

    assert approval.cost_ceiling.amount_usd == Decimal("0.25")
    execution = cast(
        "dict[str, Any]", cast("dict[str, Any]", payload["configuration"])["execution"]
    )
    execution["provider_operation_retries"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="disable provider-operation retries"):
        transform._load_approval(path, config=config, identity=_identity())


def test_report_requires_exact_outputs_assertions_fencing_and_cleanup() -> None:
    config = _config()

    report = transform._report(config, _identity(), _approval(config), _result())

    payload = json.loads(report.to_json())
    assert payload["status"] == "passed"
    assert payload["workload"]["benchmark_class"] == "transform"
    assert payload["performance"]["costs"] == [
        {
            "amount": "0.001490116119",
            "currency": "USD",
            "estimated": False,
            "provider": "gcp",
            "service": "bigquery_on_demand_analysis_gross",
        }
    ]
    for incomplete in (
        replace(_result(), assertion_count=20),
        replace(_result(), fenced_publications=3),
        replace(_result(), temporary_staging_relations=1),
        replace(_result(), cleanup_verified=False),
    ):
        with pytest.raises(transform.BigQueryTransformQualificationError, match="incomplete"):
            transform._report(config, _identity(), _approval(config), incomplete)


def test_provider_failure_still_deletes_the_owned_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    client = _DatasetClient()
    lease = FencingToken(
        lease_table=f"{config.project}.{config.dataset}._dander_leases",
        pipeline_id="phase8_bigquery_transform",
        run_id="transform-one",
        token=1,
        authority_id="bigquery:phase8-transform",
    )
    monkeypatch.setattr(transform, "_create_lease", lambda _config, _client: lease)

    def fail_seed(*_arguments: object) -> None:
        raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr(transform, "_seed_sources", fail_seed)

    with pytest.raises(
        transform.BigQueryTransformQualificationError,
        match="cleanup passed",
    ):
        transform._run_transform(config, cast("Any", client))

    assert client.datasets == set()
