"""Credential-free checks for the Phase 8 BigQuery incremental harness."""

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
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from scripts.benchmarks import bigquery_incremental_phase8 as incremental

from dander import __version__
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-user-message-2026-08-20-dander-204-bigquery-incremental-usd-0.25"


class _FakeJob:
    def __init__(
        self,
        job_type: str,
        index: int,
        *,
        result: object | None = None,
        affected_rows: int | None = None,
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

    def result(self) -> object:
        return self._result


class _FakeClient:
    def __init__(self, *, fail_verification: bool = False) -> None:
        self.datasets: set[str] = set()
        self.tables: dict[str, bigquery.Table] = {}
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.jobs: list[_FakeJob] = []
        self.queries: list[str] = []
        self.fail_verification = fail_verification

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
        if query.startswith("SELECT COUNT(*) AS row_count"):
            if self.fail_verification:
                raise RuntimeError("synthetic provider failure")
            assert job_config is not None
            table = query.split("FROM `", 1)[1].split("`", 1)[0]
            boundary = int(query.split("id < '", 1)[1].split("'", 1)[0])
            rows = self.rows[table]
            result = [
                {
                    "row_count": len(rows),
                    "updated_rows": sum(
                        row["cursor_value"] == 2 and int(row["id"]) < boundary for row in rows
                    ),
                    "inserted_rows": sum(
                        row["cursor_value"] == 2 and int(row["id"]) >= boundary for row in rows
                    ),
                    "delta_payload_rows": sum(
                        row["cursor_value"] == 2 and row["payload"] == "d" * 128 for row in rows
                    ),
                    "unchanged_seed_rows": sum(
                        row["cursor_value"] == 1 and row["payload"] == "s" * 128 for row in rows
                    ),
                    "invalid_cursor_rows": sum(row["cursor_value"] not in (1, 2) for row in rows),
                }
            ]
            return self._job(
                "query",
                result=result,
                bytes_processed=10 * 1_024 * 1_024,
                bytes_billed=10 * 1_024 * 1_024,
                slot_ms=5,
            )
        if query.startswith("CREATE TABLE IF NOT EXISTS"):
            return self._job("query")
        if query.startswith("MERGE `"):
            target = query.split("MERGE `", 1)[1].split("`", 1)[0]
            source = query.split("USING `", 1)[1].split("`", 1)[0]
            target_by_id = {row["id"]: row for row in self.rows[target]}
            for row in self.rows[source]:
                target_by_id[row["id"]] = dict(row)
            self.rows[target] = list(target_by_id.values())
            return self._job("query", affected_rows=len(self.rows[source]))
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
        bytes_processed: int = 0,
        bytes_billed: int = 0,
        slot_ms: int = 0,
    ) -> _FakeJob:
        job = _FakeJob(
            job_type,
            len(self.jobs) + 1,
            result=result,
            affected_rows=affected_rows,
            bytes_processed=bytes_processed,
            bytes_billed=bytes_billed,
            slot_ms=slot_ms,
        )
        self.jobs.append(job)
        return job


def _config(**overrides: object) -> incremental.BigQueryIncrementalConfig:
    values: dict[str, object] = {
        "project": "valid-project-123",
        "dataset": "dander_p8_incremental_test",
        "seed_rows": 200,
        "delta_rows": 2,
        "batch_rows": 2,
    }
    values.update(overrides)
    return incremental.BigQueryIncrementalConfig(**values)  # type: ignore[arg-type]


def _identity() -> incremental.CandidateIdentity:
    return incremental.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 20),
        launcher="docker_local",
        secret_provider="ephemeral_access_token",
        service_shapes=("bigquery_on_demand", "dander_2cpu_512mib"),
    )


def _approval(config: incremental.BigQueryIncrementalConfig) -> incremental._Approval:
    return incremental._Approval(
        objectives=ApprovedObjectiveSet(
            names=incremental._OBJECTIVES,
            benchmark_class=BenchmarkClass.INCREMENTAL,
            profile_id="bigquery_local_scale",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(Decimal("0.25"), _REFERENCE),
        project_sha256=incremental._identifier_sha256(config.project),
        dataset=config.dataset,
        location=config.location,
        on_demand_rate_usd_per_tib=Decimal("6.25"),
    )


def test_incremental_run_verifies_delta_cursor_cost_and_cleanup() -> None:
    config = _config()
    client = _FakeClient()

    result = incremental._run_incremental(config, client)  # type: ignore[arg-type]
    report = incremental._report(config, _identity(), _approval(config), result)
    payload = report.to_payload()
    performance = cast("dict[str, Any]", payload["performance"])
    measurements = {
        item["name"]: item for item in cast("list[dict[str, Any]]", performance["measurements"])
    }
    objectives = cast("list[dict[str, Any]]", payload["objectives"])

    assert result.seed_rows == 200
    assert result.delta_rows == 2
    assert result.final_rows == 201
    assert result.updated_rows == 1
    assert result.inserted_rows == 1
    assert result.cursor_initial == 1
    assert result.cursor_final == 2
    assert result.cursor_regressions_rejected == 1
    assert result.regression_rows_affected == 0
    assert result.load_jobs == 101
    assert result.query_jobs == 5
    assert result.bytes_billed == 10 * 1_024 * 1_024
    assert result.reservation_usage_records == 0
    assert result.temporary_staging_relations == 0
    assert result.cleanup_verified
    assert not client.datasets
    assert not client.tables
    assert not client.rows
    assert payload["status"] == "passed"
    assert measurements["retries"] == {
        "name": "retries",
        "unit": "count",
        "status": "measured",
        "value": "0",
    }
    assert performance["costs"] == [
        {
            "provider": "gcp",
            "service": "bigquery_on_demand_analysis_gross",
            "amount": "0.000059604645",
            "currency": "USD",
            "estimated": False,
        }
    ]
    assert all(item["status"] == "passed" for item in objectives)


def test_cursor_regression_is_rejected_before_provider_mutation() -> None:
    client = _FakeClient()

    with pytest.raises(
        incremental.BigQueryIncrementalQualificationError,
        match="rejected before provider mutation",
    ):
        incremental._advance_cursor(2, 1)

    assert not client.jobs
    assert not client.queries


def test_incremental_run_cleans_owned_dataset_after_provider_failure() -> None:
    config = _config()
    client = _FakeClient(fail_verification=True)

    with pytest.raises(
        incremental.BigQueryIncrementalQualificationError,
        match="failed before report completion; cleanup passed",
    ):
        incremental._run_incremental(config, client)  # type: ignore[arg-type]

    assert not client.datasets
    assert not client.tables
    assert not client.rows


def test_no_retry_client_disables_provider_operation_retries() -> None:
    inner = Mock()
    inner.load_table_from_json.return_value = Mock()
    inner.query.return_value = Mock()
    client = incremental._NoRetryClient(inner, location="US")
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


def test_objective_loader_rejects_owned_dataset_mismatch(tmp_path: Path) -> None:
    source = Path("docs/evidence/phase8/2026-08-20/bigquery-rc29-incremental-objectives.json")
    config = incremental.BigQueryIncrementalConfig(
        project="dander-proof-harrison-20260801",
        dataset="dander_p8_rc29_bigquery_incremental_0ab464a2",
    )
    identity = replace(
        _identity(),
        release_version="0.9.0rc29",
        git_commit="7a6d138a5df19ab81df202b6cb6121e134e59991",
        image_digest=("sha256:e016419fda113a5288d82fdf37d23785d39d943750cb9e19be047edab6eaad54"),
    )
    incremental._load_approval(source, config=config, identity=identity)

    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["configuration"]["bigquery"]["dataset"] = "wrong_dataset"
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="owned BigQuery dataset"):
        incremental._load_approval(manifest, config=config, identity=identity)
