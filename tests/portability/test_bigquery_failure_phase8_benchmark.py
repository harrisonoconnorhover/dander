"""Credential-free checks for the Phase 8 BigQuery failure harness."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from google.api_core.exceptions import BadRequest, NotFound, Unauthorized
from google.cloud import bigquery
from scripts.benchmarks import bigquery_bulk_phase8 as bulk
from scripts.benchmarks import bigquery_failure_phase8 as failure

from dander import __version__
from dander.qualification import ApprovedCostCeiling, ApprovedObjectiveSet, BenchmarkClass

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


_COMMIT = "b" * 40
_DIGEST = f"sha256:{'a' * 64}"
_REFERENCE = "codex-goal-phase8-bigquery-failure"


class _FakeJob:
    def __init__(
        self,
        job_type: str,
        index: int,
        *,
        result: object | None = None,
        affected_rows: int | None = None,
        error: Exception | None = None,
        expose_error_result: bool = True,
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
        self.error_result = (
            {"reason": "synthetic"} if error is not None and expose_error_result else None
        )
        self._result = self if result is None else result
        self._error = error

    def result(self, **_kwargs: object) -> object:
        if self._error is not None:
            raise self._error
        return self._result


class _FakeClient:
    def __init__(self) -> None:
        self.datasets: set[str] = set()
        self.tables: dict[str, bigquery.Table] = {}
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.fences: dict[tuple[str, str], dict[str, object]] = {}
        self.jobs: list[_FakeJob] = []
        self.queries: list[str] = []

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
        self.fences.clear()

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
        if any(not isinstance(row["value"], int) for row in rows):
            return self._job(
                "load",
                error=BadRequest("invalid INT64 value"),  # type: ignore[no-untyped-call]
            )
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
        self.tables[destination] = bigquery.Table(destination)
        self.rows[destination] = list(self.rows[sources])
        return self._job("copy")

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _FakeJob:
        self.queries.append(query)
        if query.startswith("SELECT COUNT(*) AS row_count"):
            table = query.split("FROM `", 1)[1].split("`", 1)[0]
            rows = self.rows[table]
            unchanged = sum(row == {"id": 1, "value": 1} for row in rows)
            return self._job(
                "query",
                result=[{"row_count": len(rows), "unchanged_rows": unchanged}],
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
        if query.startswith("BEGIN TRANSACTION;"):
            return self._job(
                "query",
                error=BadRequest("Dander destination fence lost"),  # type: ignore[no-untyped-call]
                expose_error_result=False,
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
        expose_error_result: bool = True,
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
            expose_error_result=expose_error_result,
            bytes_processed=bytes_processed,
            bytes_billed=bytes_billed,
            slot_ms=slot_ms,
        )
        self.jobs.append(job)
        return job


class _CredentialClient:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.closed = False

    def query(self, *_args: object, **_kwargs: object) -> _FakeJob:
        if self.error is not None:
            raise self.error
        return _FakeJob("query", 1)

    def close(self) -> None:
        self.closed = True


def _parameters(config: bigquery.QueryJobConfig | None) -> dict[str, object]:
    assert config is not None
    return {parameter.name: parameter.value for parameter in config.query_parameters}


def _config(**overrides: object) -> failure.BigQueryFailureConfig:
    values: dict[str, object] = {
        "project": "valid-project-123",
        "dataset": "dander_p8_failure_test",
    }
    values.update(overrides)
    return failure.BigQueryFailureConfig(**values)  # type: ignore[arg-type]


def _identity() -> bulk.CandidateIdentity:
    return bulk.CandidateIdentity(
        release_version=__version__,
        git_commit=_COMMIT,
        image_digest=_DIGEST,
        approval_reference=_REFERENCE,
        benchmark_date=date(2026, 8, 21),
        launcher="docker_local",
        secret_provider="ephemeral_access_token",
        service_shapes=("bigquery_on_demand", "dander_2cpu_512mib"),
    )


def _approval(config: failure.BigQueryFailureConfig) -> bulk._Approval:
    return bulk._Approval(
        objectives=ApprovedObjectiveSet(
            names=failure._OBJECTIVES,
            benchmark_class=BenchmarkClass.FAILURE,
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


def test_config_binds_only_the_four_provider_failure_probes() -> None:
    assert _config().workload_payload() == {
        "schema": "io.dander.phase8.bigquery-failure/v1",
        "benchmark_class": "failure",
        "probes": [
            "credential_rejection",
            "failed_load_cleanup",
            "provider_operation_recovery",
            "stale_publication_rejection",
        ],
        "verification_maximum_bytes_billed": 64 * 1_024 * 1_024,
    }


def test_failure_run_rejects_bounded_failures_recovers_and_cleans() -> None:
    config = _config()
    client = _FakeClient()

    result = failure._run_failure(config, client, credential_probe=lambda _config: 3)
    report = failure._report(config, _identity(), _approval(config), result)
    payload = report.to_payload()
    performance = cast("dict[str, Any]", payload["performance"])
    measurements = {
        item["name"]: item for item in cast("list[dict[str, Any]]", performance["measurements"])
    }

    assert result.probe_count == 4
    assert result.load_jobs == 2
    assert result.copy_jobs == 1
    assert result.query_jobs == 7
    assert result.provider_job_errors == 2
    assert sum(job.error_result is not None for job in client.jobs) == 1
    assert result.stale_publications_rejected == 1
    assert result.provider_operation_retries == 0
    assert result.temporary_staging_relations == 0
    assert result.cleanup_verified
    assert not client.datasets
    assert not client.tables
    assert not client.rows
    assert not client.fences
    assert payload["status"] == "passed"
    assert measurements["retries"]["value"] == "0"
    assert measurements["bigquery_provider_operation_retries"]["value"] == "0"
    assert performance["costs"] == [
        {
            "provider": "gcp",
            "service": "bigquery_on_demand_analysis_gross",
            "amount": "0.000119209290",
            "currency": "USD",
            "estimated": False,
        }
    ]


def test_failure_run_cleans_if_a_probe_fails_before_mutation() -> None:
    config = _config()
    client = _FakeClient()

    def fail_probe(_config: failure.BigQueryFailureConfig) -> int:
        raise RuntimeError("synthetic provider outage")

    with pytest.raises(
        failure.BigQueryFailureQualificationError,
        match="failed before report completion; cleanup passed",
    ):
        failure._run_failure(config, client, credential_probe=fail_probe)

    assert not client.datasets
    assert not client.tables


@pytest.mark.parametrize(
    "rejection",
    [
        Unauthorized("invalid token"),  # type: ignore[no-untyped-call]
    ],
)
def test_credential_probe_accepts_only_authentication_rejection(
    monkeypatch: pytest.MonkeyPatch,
    rejection: Exception,
) -> None:
    client = _CredentialClient(rejection)
    monkeypatch.setenv("DANDER_GCP_ACCESS_TOKEN", "valid-projected-token")
    monkeypatch.setattr(bigquery, "Client", lambda **_kwargs: client)

    assert failure._probe_credential_rejection(_config()) >= 0
    assert client.closed


def test_credential_probe_rejects_an_accepted_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CredentialClient(None)
    monkeypatch.setenv("DANDER_GCP_ACCESS_TOKEN", "valid-projected-token")
    monkeypatch.setattr(bigquery, "Client", lambda **_kwargs: client)

    with pytest.raises(
        failure.BigQueryFailureQualificationError,
        match="accepted a rejected credential",
    ):
        failure._probe_credential_rejection(_config())

    assert client.closed


def test_report_rejects_missing_failed_provider_job() -> None:
    config = _config()
    result = failure._FailureResult(
        duration_ms=100,
        peak_rss_bytes=1,
        probe_count=4,
        credential_rejection_duration_ms=1,
        failed_load_cleanup_duration_ms=1,
        provider_operation_recovery_duration_ms=1,
        stale_publications_rejected=1,
        load_jobs=2,
        copy_jobs=1,
        query_jobs=7,
        provider_job_errors=1,
        bytes_processed=0,
        bytes_billed=0,
        slot_ms=0,
        reservation_usage_records=0,
        job_ids=(),
        provider_operation_retries=0,
        temporary_staging_relations=0,
        cleanup_verified=True,
    )

    with pytest.raises(
        failure.BigQueryFailureQualificationError,
        match="failure job count",
    ):
        failure._report(config, _identity(), _approval(config), result)


def test_corrective_objective_binds_exact_candidate_harness_and_dependency() -> None:
    reference = "codex-goal-02043c37-096e-416a-875c-b405c4af0594-existing-bigquery-usd-0.25"
    config = failure.BigQueryFailureConfig(
        project="dander-proof-harrison-20260801",
        dataset="dander_p8_rc30_bigquery_failure_355c096f",
    )
    identity = replace(
        _identity(),
        git_commit="d27dd880fc7676c15969bff76aaabb64c22be7c2",
        image_digest=("sha256:355c096f03cb8352b14d3afce00f5065b88d7477e9ceaaf436e79668941ad315"),
        approval_reference=reference,
    )

    approval = failure._load_approval(
        Path("docs/evidence/phase8/2026-08-21/bigquery-rc30-failure-corrective-objectives.json"),
        config=config,
        identity=identity,
    )

    assert approval.objectives.benchmark_class is BenchmarkClass.FAILURE
    assert approval.cost_ceiling.amount_usd == Decimal("0.25")
