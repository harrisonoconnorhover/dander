"""Credential-free checks for the Phase 8 BigQuery bulk harness."""

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
from scripts.benchmarks import bigquery_bulk_phase8 as bulk

from dander import __version__
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-thread-phase8-bigquery-bulk"


class _FakeJob:
    def __init__(
        self,
        job_type: str,
        index: int,
        *,
        result: object | None = None,
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
        self.num_dml_affected_rows: int | None = None
        self._result = self if result is None else result

    def result(self) -> object:
        return self._result


class _FakeClient:
    def __init__(self, *, fail_verification: bool = False) -> None:
        self.datasets: set[str] = set()
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
        self.rows = {name: rows for name, rows in self.rows.items() if not name.startswith(prefix)}

    def create_table(self, table: bigquery.Table, *, exists_ok: bool = False) -> bigquery.Table:
        table_id = str(table.reference)
        if not exists_ok:
            assert table_id not in self.rows
        self.rows.setdefault(table_id, [])
        return table

    def get_table(self, table: str) -> bigquery.Table:
        return bigquery.Table(table)

    def update_table(self, table: bigquery.Table, fields: Sequence[str]) -> bigquery.Table:
        del fields
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

    def copy_table(
        self,
        sources: str,
        destination: str,
        *,
        job_config: bigquery.CopyJobConfig,
    ) -> _FakeJob:
        assert job_config.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE
        self.rows[destination] = list(self.rows[sources])
        return self._job("copy")

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _FakeJob:
        assert job_config is not None
        self.queries.append(query)
        if self.fail_verification:
            raise RuntimeError("synthetic provider failure")
        table = query.split("FROM `", 1)[1].split("`", 1)[0]
        rows = self.rows[table]
        result = [
            {
                "row_count": len(rows),
                "payload_bytes": sum(len(row["payload"]) for row in rows),
            }
        ]
        return self._job(
            "query",
            result=result,
            bytes_processed=10 * 1_024 * 1_024,
            bytes_billed=10 * 1_024 * 1_024,
            slot_ms=5,
        )

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        assert not_found_ok
        self.rows.pop(table, None)

    def list_tables(self, dataset: str) -> list[object]:
        prefix = f"{dataset}."
        return [
            SimpleNamespace(table_id=table.removeprefix(prefix))
            for table in self.rows
            if table.startswith(prefix)
        ]

    def _job(
        self,
        job_type: str,
        *,
        result: object | None = None,
        bytes_processed: int = 0,
        bytes_billed: int = 0,
        slot_ms: int = 0,
    ) -> _FakeJob:
        job = _FakeJob(
            job_type,
            len(self.jobs) + 1,
            result=result,
            bytes_processed=bytes_processed,
            bytes_billed=bytes_billed,
            slot_ms=slot_ms,
        )
        self.jobs.append(job)
        return job


def _config(**overrides: object) -> bulk.BigQueryBulkConfig:
    values: dict[str, object] = {
        "project": "valid-project-123",
        "dataset": "dander_p8_bulk_test",
        "narrow_rows": 4,
        "wide_rows": 2,
        "batch_rows": 2,
    }
    values.update(overrides)
    return bulk.BigQueryBulkConfig(**values)  # type: ignore[arg-type]


def _identity() -> bulk.CandidateIdentity:
    return bulk.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 20),
        launcher="docker_local",
        secret_provider="ephemeral_access_token",
        service_shapes=("bigquery_on_demand", "dander_2cpu_512mib"),
    )


def _approval(config: bulk.BigQueryBulkConfig) -> bulk._Approval:
    return bulk._Approval(
        objectives=ApprovedObjectiveSet(
            names=bulk._OBJECTIVES,
            benchmark_class=BenchmarkClass.BULK_THROUGHPUT,
            profile_id="bigquery_local_scale",
            release_version=__version__,
            git_commit=_COMMIT,
            image_digest=_DIGEST,
            configuration_sha256=config.configuration_sha256(),
            approval_reference=_REFERENCE,
        ),
        cost_ceiling=ApprovedCostCeiling(Decimal("0.25"), _REFERENCE),
        project_sha256=bulk._identifier_sha256(config.project),
        dataset=config.dataset,
        location=config.location,
        on_demand_rate_usd_per_tib=Decimal("6.25"),
    )


def test_bulk_run_uses_accepted_shapes_reports_measured_cost_and_cleans() -> None:
    config = _config()
    client = _FakeClient()

    result = bulk._run_bulk(config, client)  # type: ignore[arg-type]
    report = bulk._report(config, _identity(), _approval(config), result)
    payload = report.to_payload()
    performance = cast("dict[str, Any]", payload["performance"])
    objectives = cast("list[dict[str, Any]]", payload["objectives"])

    assert result.narrow_rows == 4
    assert result.wide_rows == 2
    assert result.load_jobs == 3
    assert result.copy_jobs == 2
    assert result.query_jobs == 2
    assert result.bytes_billed == 20 * 1_024 * 1_024
    assert result.reservation_usage_records == 0
    assert result.temporary_staging_relations == 0
    assert result.cleanup_verified
    assert not client.datasets
    assert not client.rows
    assert payload["status"] == "passed"
    assert performance["costs"] == [
        {
            "provider": "gcp",
            "service": "bigquery_on_demand_analysis_gross",
            "amount": "0.000119209290",
            "currency": "USD",
            "estimated": False,
        }
    ]
    assert all(item["status"] == "passed" for item in objectives)


def test_bulk_run_cleans_owned_dataset_after_provider_failure() -> None:
    config = _config()
    client = _FakeClient(fail_verification=True)

    with pytest.raises(
        bulk.BigQueryBulkQualificationError,
        match="failed before report completion; cleanup passed",
    ):
        bulk._run_bulk(config, client)  # type: ignore[arg-type]

    assert not client.datasets
    assert not client.rows


def test_verification_query_uses_non_reserved_row_count_alias() -> None:
    config = _config()
    client = _FakeClient()
    table = f"{config.project}.{config.dataset}.narrow_records"
    client.rows[table] = [{"id": "000000000000", "payload": "x" * 32}]

    bulk._require_table_shape(
        client,  # type: ignore[arg-type]
        config=config,
        table="narrow_records",
        rows=1,
        payload_bytes=32,
    )

    assert client.queries == [
        "SELECT COUNT(*) AS row_count, "
        "COALESCE(SUM(BYTE_LENGTH(payload)), 0) AS payload_bytes "
        "FROM `valid-project-123.dander_p8_bulk_test.narrow_records`"
    ]
    assert " AS rows" not in client.queries[0]


def test_no_retry_client_disables_provider_operation_retries() -> None:
    inner = Mock()
    inner.load_table_from_json.return_value = Mock()
    inner.copy_table.return_value = Mock()
    inner.query.return_value = Mock()
    client = bulk._NoRetryClient(inner, location="US")
    load_config = bigquery.LoadJobConfig()
    copy_config = bigquery.CopyJobConfig()
    query_config = bigquery.QueryJobConfig()

    client.load_table_from_json([], "project.dataset.table", job_config=load_config)
    client.copy_table("project.dataset.source", "project.dataset.target", job_config=copy_config)
    client.query("SELECT 1", job_config=query_config)

    inner.load_table_from_json.assert_called_once_with(
        [],
        "project.dataset.table",
        num_retries=0,
        location="US",
        job_config=load_config,
    )
    inner.copy_table.assert_called_once_with(
        "project.dataset.source",
        "project.dataset.target",
        location="US",
        job_config=copy_config,
        retry=None,
    )
    inner.query.assert_called_once_with(
        "SELECT 1",
        location="US",
        job_config=query_config,
        retry=None,
        job_retry=None,
    )


def test_objective_loader_rejects_owned_dataset_mismatch(tmp_path: Path) -> None:
    source = Path("docs/evidence/phase8/2026-08-20/bigquery-rc29-bulk-throughput-objectives.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["configuration"]["bigquery"]["dataset"] = "wrong_dataset"
    manifest = tmp_path / "objectives.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    config = bulk.BigQueryBulkConfig(
        project="dander-proof-harrison-20260801",
        dataset="dander_p8_rc29_bigquery_bulk_0ab464a2",
    )
    identity = replace(
        _identity(),
        git_commit="7a6d138a5df19ab81df202b6cb6121e134e59991",
        image_digest=("sha256:e016419fda113a5288d82fdf37d23785d39d943750cb9e19be047edab6eaad54"),
        approval_reference=(
            "codex-thread-01a0004d-6d65-7280-84bc-ca5368b38821-2026-08-16-additional-phase8-10"
        ),
    )

    with pytest.raises(ValueError, match="owned BigQuery dataset"):
        bulk._load_approval(manifest, config=config, identity=identity)
