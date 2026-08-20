"""Credential-free checks for the Phase 8 BigQuery concurrency harness."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import BadRequest, NotFound
from google.cloud import bigquery
from scripts.benchmarks import bigquery_concurrency_phase8 as concurrency
from scripts.benchmarks import bigquery_incremental_phase8 as common

from dander import __version__
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-user-message-2026-08-20-dander-204-bigquery-concurrency-usd-0.25"


class _FakeJob:
    def __init__(
        self,
        job_type: str,
        index: int,
        *,
        result: object | None = None,
        affected_rows: int | None = None,
        error: Exception | None = None,
        callback: Callable[[], None] | None = None,
        bytes_processed: int = 0,
        bytes_billed: int = 0,
        slot_ms: int = 0,
    ) -> None:
        self.job_type = job_type
        self.job_id = f"{job_type}-job-{index}"
        self.total_bytes_processed = bytes_processed
        self.total_bytes_billed = bytes_billed
        self.slot_millis = slot_ms
        self.reservation_usage: list[object] = []
        self.num_dml_affected_rows = affected_rows
        self._result = self if result is None else result
        self._error = error
        self._callback = callback
        self.result_calls = 0

    def result(self) -> object:
        self.result_calls += 1
        if self._callback is not None:
            self._callback()
        if self._error is not None:
            raise self._error
        return self._result


class _FakeClient:
    def __init__(self, *, fail_readback: bool = False) -> None:
        self.datasets: set[str] = set()
        self.tables: dict[str, bigquery.Table] = {}
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.fences: dict[tuple[str, str], dict[str, object]] = {}
        self.jobs: list[_FakeJob] = []
        self.queries: list[str] = []
        self.fail_readback = fail_readback

    def create_dataset(self, dataset: bigquery.Dataset) -> bigquery.Dataset:
        dataset_id = str(dataset.reference)
        assert dataset_id not in self.datasets
        self.datasets.add(dataset_id)
        return dataset

    def get_dataset(self, dataset: str) -> bigquery.Dataset:
        if dataset not in self.datasets:
            raise NotFound("synthetic absent dataset")  # type: ignore[no-untyped-call]
        return bigquery.Dataset(dataset)

    def delete_dataset(self, dataset: str, *, not_found_ok: bool) -> None:
        assert not_found_ok
        self.datasets.discard(dataset)
        prefix = f"{dataset}."
        self.tables = {
            name: table for name, table in self.tables.items() if not name.startswith(prefix)
        }
        self.rows = {name: rows for name, rows in self.rows.items() if not name.startswith(prefix)}

    def create_table(self, table: bigquery.Table, *, exists_ok: bool = False) -> bigquery.Table:
        table_id = str(table.reference)
        if not exists_ok:
            assert table_id not in self.tables
        self.tables.setdefault(table_id, table)
        self.rows.setdefault(table_id, [])
        return self.tables[table_id]

    def get_table(self, table: str) -> bigquery.Table:
        if table not in self.tables:
            raise NotFound("synthetic absent table")  # type: ignore[no-untyped-call]
        return self.tables[table]

    def update_table(self, table: bigquery.Table, fields: Sequence[str]) -> bigquery.Table:
        del fields
        self.tables[str(table.reference)] = table
        return table

    def load_table_from_json(
        self,
        json_rows: Sequence[Mapping[str, Any]],
        destination: str,
        *,
        job_config: bigquery.LoadJobConfig,
    ) -> _FakeJob:
        rows = [dict(row) for row in json_rows]
        if job_config.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE:
            self.rows[destination] = rows
        else:
            self.rows.setdefault(destination, []).extend(rows)
        return self._job("load")

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _FakeJob:
        self.queries.append(query)
        if query.startswith("SELECT pipeline_index"):
            if self.fail_readback:
                raise RuntimeError("synthetic provider failure")
            results: list[dict[str, int]] = []
            for index in range(4):
                table = next(
                    name for name in self.rows if name.endswith(f"pipeline_{index:02d}_records")
                )
                rows = self.rows[table]
                results.append(
                    {
                        "pipeline_index": index,
                        "row_count": len(rows),
                        "distinct_row_count": len({row["id"] for row in rows}),
                        "payload_row_count": sum(len(str(row["payload"])) == 128 for row in rows),
                    }
                )
            return self._job(
                "query",
                result=results,
                bytes_processed=10 * 1_024 * 1_024,
                bytes_billed=10 * 1_024 * 1_024,
                slot_ms=5,
            )
        if query.startswith("SELECT COUNT(*) AS row_count FROM"):
            table = query.split("FROM `", 1)[1].split("`", 1)[0]
            return self._job(
                "query",
                result=[{"row_count": len(self.rows[table])}],
                bytes_processed=10 * 1_024 * 1_024,
                bytes_billed=10 * 1_024 * 1_024,
                slot_ms=5,
            )
        if query.startswith("CREATE TABLE IF NOT EXISTS"):
            return self._job("query")
        if "USING (SELECT @dander_target_id AS target_id" in query:
            parameters = _parameters(job_config)
            key = (str(parameters["dander_target_id"]), str(parameters["dander_pipeline_id"]))
            incoming = {
                "authority_id": parameters["dander_authority_id"],
                "authority_epoch": parameters["dander_authority_epoch"],
                "run_id": parameters["dander_run_id"],
                "token": parameters["dander_fencing_token"],
            }
            current = self.fences.get(key)
            accepted = current is None or (
                current["authority_id"] == incoming["authority_id"]
                and current["authority_epoch"] == incoming["authority_epoch"]
                and (
                    int(cast("int", incoming["token"])) > int(cast("int", current["token"]))
                    or (
                        incoming["token"] == current["token"]
                        and incoming["run_id"] == current["run_id"]
                    )
                )
            )
            if accepted:
                self.fences[key] = incoming
            return self._job("query", affected_rows=1 if accepted else 0)
        if query.startswith("MERGE `"):
            target = query.split("MERGE `", 1)[1].split("`", 1)[0]
            source = query.split("USING `", 1)[1].split("`", 1)[0]
            target_by_id = {row["id"]: row for row in self.rows[target]}
            for row in self.rows[source]:
                target_by_id[row["id"]] = dict(row)
            self.rows[target] = list(target_by_id.values())
            return self._job("query", affected_rows=len(self.rows[source]))
        if query.startswith("BEGIN TRANSACTION;"):
            parameters = _parameters(job_config)
            key = (str(parameters["dander_target_id"]), str(parameters["dander_pipeline_id"]))
            current = self.fences[key]
            stale = (
                current["authority_id"] != parameters["dander_authority_id"]
                or current["authority_epoch"] != parameters["dander_authority_epoch"]
                or current["run_id"] != parameters["dander_run_id"]
                or current["token"] != parameters["dander_fencing_token"]
            )
            return self._job(
                "query",
                error=BadRequest("Dander destination fence lost") if stale else None,  # type: ignore[no-untyped-call]
            )
        raise AssertionError(f"unexpected query: {query}")

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        assert not_found_ok
        self.tables.pop(table, None)
        self.rows.pop(table, None)

    def list_tables(self, dataset: str) -> list[object]:
        prefix = f"{dataset}."
        return [
            SimpleNamespace(table_id=table.removeprefix(prefix))
            for table in self.tables
            if table.startswith(prefix)
        ]

    def _job(
        self,
        job_type: str,
        *,
        result: object | None = None,
        affected_rows: int | None = None,
        error: Exception | None = None,
        bytes_processed: int = 0,
        bytes_billed: int = 0,
        slot_ms: int = 0,
    ) -> _FakeJob:
        job = _FakeJob(
            job_type,
            len(self.jobs) + 1,
            result=result,
            affected_rows=affected_rows,
            error=error,
            bytes_processed=bytes_processed,
            bytes_billed=bytes_billed,
            slot_ms=slot_ms,
        )
        self.jobs.append(job)
        return job


def _parameters(config: bigquery.QueryJobConfig | None) -> dict[str, object]:
    assert config is not None
    return {parameter.name: parameter.value for parameter in config.query_parameters}


def _config(**overrides: object) -> concurrency.BigQueryConcurrencyConfig:
    values: dict[str, object] = {
        "project": "valid-project-123",
        "dataset": "dander_p8_concurrency_test",
    }
    values.update(overrides)
    return concurrency.BigQueryConcurrencyConfig(**values)  # type: ignore[arg-type]


def _identity() -> concurrency.CandidateIdentity:
    return concurrency.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 20),
        launcher="docker_local",
        secret_provider="ephemeral_access_token",
        service_shapes=("bigquery_on_demand", "dander_2cpu_512mib"),
    )


def _approval(config: concurrency.BigQueryConcurrencyConfig) -> concurrency._Approval:
    return concurrency._Approval(
        objectives=ApprovedObjectiveSet(
            names=concurrency._OBJECTIVES,
            benchmark_class=BenchmarkClass.CONCURRENT_PIPELINES,
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


def test_config_binds_exact_four_pipeline_workload() -> None:
    config = _config()

    assert config.workload_payload() == {
        "schema": "io.dander.phase8.bigquery-concurrency/v1",
        "benchmark_class": "concurrent_pipelines",
        "concurrent_pipelines": 4,
        "rows_per_pipeline": 5_000,
        "payload_bytes": 128,
        "batch_rows": 5_000,
        "verification_maximum_bytes_billed": 256 * 1_024 * 1_024,
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"concurrent_pipelines": 3}, "exactly 4"),
        ({"rows_per_pipeline": 4_999}, "exactly 5000"),
        ({"batch_rows": 5_001}, "must not exceed"),
    ],
)
def test_config_rejects_workload_drift(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _config(**overrides)


def test_concurrency_run_verifies_readback_contention_cost_retries_and_cleanup() -> None:
    config = _config()
    client = _FakeClient()

    result = concurrency._run_concurrency(config, client)
    report = concurrency._report(config, _identity(), _approval(config), result)
    payload = report.to_payload()
    performance = cast("dict[str, Any]", payload["performance"])
    measurements = {
        item["name"]: item for item in cast("list[dict[str, Any]]", performance["measurements"])
    }

    assert result.pipeline_count == 4
    assert result.rows_per_pipeline == 5_000
    assert result.total_rows == 20_000
    assert result.concurrent_claim_attempts == 2
    assert result.stale_publications_rejected == 1
    assert result.provider_operation_retries == 0
    assert result.temporary_staging_relations == 0
    assert result.cleanup_verified
    assert not client.datasets
    assert not client.tables
    assert not client.rows
    assert payload["status"] == "passed"
    assert measurements["retries"]["value"] == "0"
    assert measurements["provider_operation_retries"]["value"] == "0"
    assert performance["costs"] == [
        {
            "provider": "gcp",
            "service": "bigquery_on_demand_analysis_gross",
            "amount": "0.000119209290",
            "currency": "USD",
            "estimated": False,
        }
    ]
    objectives = cast("list[dict[str, Any]]", payload["objectives"])
    assert all(item["status"] == "passed" for item in objectives)


def test_controlled_contention_uses_exactly_two_claims_and_rejects_stale_publication() -> None:
    config = _config()
    client = _FakeClient()
    client.create_dataset(bigquery.Dataset(f"{config.project}.{config.dataset}"))

    with concurrency._zero_provider_operation_retries():
        stale_rejected, claims = concurrency._exercise_controlled_contention(config, client)

    assert stale_rejected
    assert claims == 2
    assert len(client.fences) == 1
    current = next(iter(client.fences.values()))
    assert current["token"] == 21
    assert current["run_id"] == "contention-new"
    stale_jobs = [job for job in client.jobs if isinstance(job._error, BadRequest)]
    assert len(stale_jobs) == 1
    assert stale_jobs[0].result_calls == 1
    assert not client.rows[f"{config.project}.{config.dataset}.contention_records"]


def test_concurrency_run_cleans_owned_dataset_after_provider_failure() -> None:
    config = _config()
    client = _FakeClient(fail_readback=True)

    with pytest.raises(
        concurrency.BigQueryConcurrencyQualificationError,
        match="failed before report completion; cleanup passed",
    ):
        concurrency._run_concurrency(config, client)

    assert not client.datasets
    assert not client.tables
    assert not client.rows


def test_no_retry_client_disables_provider_operation_retries() -> None:
    inner = Mock()
    inner.load_table_from_json.return_value = Mock()
    inner.query.return_value = Mock()
    client = common._NoRetryClient(inner, location="US")
    load_config = bigquery.LoadJobConfig()
    query_config = bigquery.QueryJobConfig()

    client.load_table_from_json([], "project.dataset.table", job_config=load_config)
    client.query("SELECT 1", job_config=query_config)

    inner.load_table_from_json.assert_called_once_with(
        [],
        "project.dataset.table",
        num_retries=0,
        location="US",
        job_config=load_config,
    )
    inner.query.assert_called_once_with(
        "SELECT 1",
        location="US",
        job_config=query_config,
        retry=None,
        job_retry=None,
    )


def test_objective_loader_rejects_harness_or_workload_drift(tmp_path: Path) -> None:
    source = Path("docs/evidence/phase8/2026-08-20/bigquery-rc29-concurrency-objectives.json")
    config = concurrency.BigQueryConcurrencyConfig(
        project="dander-proof-harrison-20260801",
        dataset="dander_p8_rc29_bigquery_concurrency_c8f7a91e",
    )
    identity = replace(
        _identity(),
        git_commit="7a6d138a5df19ab81df202b6cb6121e134e59991",
        image_digest=("sha256:e016419fda113a5288d82fdf37d23785d39d943750cb9e19be047edab6eaad54"),
    )
    concurrency._load_approval(source, config=config, identity=identity)

    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["workload"]["rows_per_pipeline"] = 4_999
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="workload"):
        concurrency._load_approval(manifest, config=config, identity=identity)
