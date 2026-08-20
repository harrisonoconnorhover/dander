#!/usr/bin/env python3
"""Exact-candidate BigQuery incremental Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import sys
import time
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.oauth2.credentials import Credentials

from dander import __version__
from dander.qualification import (
    ApprovedCostCeiling,
    ApprovedObjectiveSet,
    BenchmarkClass,
    BenchmarkWorkload,
    ObjectiveResult,
    ObjectiveStatus,
    QualificationContext,
    QualificationReport,
    QualificationStatus,
)
from dander.telemetry import CostAttribution, PerformanceMeasurement, RunPerformance
from dander.writer import BigQueryIncrementalWriter, WriteField, WriteTarget

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.bigquery-incremental/v1"
_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "delta_target_ratio",
    "exact_result",
    "incremental_cursor_monotonic",
    "incremental_throughput_measurement",
)
_TIB = Decimal(1024**4)
_NO_RETRY = cast("Any", None)


class BigQueryIncrementalQualificationError(RuntimeError):
    """Raised with a credential-free incremental qualification summary."""


@dataclass(frozen=True, slots=True)
class BigQueryIncrementalConfig:
    """Owned provider coordinates and the accepted incremental workload."""

    project: str
    dataset: str
    location: str = "US"
    seed_rows: int = 300_000
    delta_rows: int = 3_000
    payload_bytes: int = 128
    batch_rows: int = 10_000
    verification_maximum_bytes_billed: int = 512 * 1_024 * 1_024
    on_demand_rate_usd_per_tib: Decimal = Decimal("6.25")
    token_env: str = "DANDER_GCP_ACCESS_TOKEN"

    def __post_init__(self) -> None:
        if not _valid_project(self.project):
            raise ValueError("project must be a valid Google Cloud project id")
        if not _valid_identifier(self.dataset):
            raise ValueError("dataset must be a valid BigQuery dataset id")
        if not self.location or any(character.isspace() for character in self.location):
            raise ValueError("location must be a non-empty BigQuery location")
        for name in (
            "seed_rows",
            "delta_rows",
            "payload_bytes",
            "batch_rows",
            "verification_maximum_bytes_billed",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.delta_rows % 2:
            raise ValueError("delta_rows must be even")
        if self.delta_rows > self.seed_rows:
            raise ValueError("delta_rows must not exceed seed_rows")
        if self.batch_rows > self.seed_rows:
            raise ValueError("batch_rows must not exceed seed_rows")
        if self.batch_rows * (self.payload_bytes + 32) > 256 * 1_024 * 1_024:
            raise ValueError("one batch must not exceed 256 MiB of logical input")
        if (
            not isinstance(self.on_demand_rate_usd_per_tib, Decimal)
            or not self.on_demand_rate_usd_per_tib.is_finite()
            or self.on_demand_rate_usd_per_tib <= 0
        ):
            raise ValueError("on_demand_rate_usd_per_tib must be a positive Decimal")
        if not _valid_identifier(self.token_env):
            raise ValueError("token_env must be a valid environment variable name")

    def workload_payload(self) -> dict[str, object]:
        """Return the exact configuration covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.INCREMENTAL.value,
            "seed_rows": self.seed_rows,
            "delta_rows": self.delta_rows,
            "payload_bytes": self.payload_bytes,
            "batch_rows": self.batch_rows,
            "verification_maximum_bytes_billed": self.verification_maximum_bytes_billed,
        }

    def configuration_sha256(self) -> str:
        """Hash the canonical workload used by objective approval."""
        encoded = json.dumps(
            self.workload_payload(), separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    release_version: str
    git_commit: str
    image_digest: str
    approval_reference: str
    benchmark_date: date
    launcher: str
    secret_provider: str
    service_shapes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Approval:
    objectives: ApprovedObjectiveSet
    cost_ceiling: ApprovedCostCeiling
    project_sha256: str
    dataset: str
    location: str
    on_demand_rate_usd_per_tib: Decimal


@dataclass(frozen=True, slots=True)
class _IncrementalResult:
    duration_ms: int
    peak_rss_bytes: int
    seed_duration_ms: int
    seed_rows: int
    seed_logical_bytes: int
    delta_duration_ms: int
    delta_rows: int
    delta_logical_bytes: int
    final_rows: int
    updated_rows: int
    inserted_rows: int
    regression_rows_affected: int
    cursor_initial: int
    cursor_final: int
    cursor_regressions_rejected: int
    load_jobs: int
    query_jobs: int
    bytes_processed: int
    bytes_billed: int
    slot_ms: int
    reservation_usage_records: int
    job_ids: tuple[str, ...]
    temporary_staging_relations: int
    cleanup_verified: bool


class _NoRetryJob:
    def __init__(self, job: object) -> None:
        self._job = job

    @property
    def num_dml_affected_rows(self) -> int | None:
        return cast("int | None", getattr(self._job, "num_dml_affected_rows", None))

    def result(self) -> object:
        if isinstance(self._job, bigquery.QueryJob):
            return self._job.result(retry=None, job_retry=None)
        return cast("Any", self._job).result(retry=None)


class _NoRetryClient:
    """Use the existing writer API while disabling provider-operation retries."""

    def __init__(self, client: bigquery.Client, *, location: str) -> None:
        self._client = client
        self._location = location
        self.jobs: list[object] = []

    def create_dataset(self, dataset: bigquery.Dataset) -> bigquery.Dataset:
        return self._client.create_dataset(dataset, exists_ok=False, retry=_NO_RETRY)

    def get_dataset(self, dataset: str) -> bigquery.Dataset:
        return self._client.get_dataset(dataset, retry=_NO_RETRY)

    def delete_dataset(self, dataset: str, *, not_found_ok: bool) -> None:
        self._client.delete_dataset(
            dataset,
            delete_contents=True,
            retry=_NO_RETRY,
            not_found_ok=not_found_ok,
        )

    def create_table(self, table: bigquery.Table, *, exists_ok: bool = False) -> bigquery.Table:
        return self._client.create_table(table, exists_ok=exists_ok, retry=_NO_RETRY)

    def get_table(self, table: str) -> bigquery.Table:
        return self._client.get_table(table, retry=_NO_RETRY)

    def update_table(self, table: bigquery.Table, fields: Sequence[str]) -> bigquery.Table:
        return self._client.update_table(table, fields, retry=_NO_RETRY)

    def load_table_from_json(
        self,
        json_rows: Sequence[Mapping[str, Any]],
        destination: str,
        *,
        job_config: bigquery.LoadJobConfig,
    ) -> _NoRetryJob:
        job = self._client.load_table_from_json(
            [dict(row) for row in json_rows],
            destination,
            num_retries=0,
            location=self._location,
            job_config=job_config,
        )
        self.jobs.append(job)
        return _NoRetryJob(job)

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _NoRetryJob:
        job = self._client.query(
            query,
            location=self._location,
            job_config=job_config,
            retry=_NO_RETRY,
            job_retry=_NO_RETRY,
        )
        self.jobs.append(job)
        return _NoRetryJob(job)

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        self._client.delete_table(table, not_found_ok=not_found_ok, retry=_NO_RETRY)

    def list_tables(self, dataset: str) -> Iterable[object]:
        return self._client.list_tables(dataset, retry=_NO_RETRY)


def _load_approval(
    path: Path,
    *,
    config: BigQueryIncrementalConfig,
    identity: CandidateIdentity,
) -> _Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective approval schema is incompatible")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective approval workload does not match the requested run")
    configuration = _mapping(payload.get("configuration"), "configuration")
    provider = _mapping(configuration.get("bigquery"), "BigQuery configuration")
    if provider.get("project_sha256") != _identifier_sha256(config.project):
        raise ValueError("objective approval does not match the BigQuery project")
    if provider.get("dataset") != config.dataset or provider.get("location") != config.location:
        raise ValueError("objective approval does not match the owned BigQuery dataset")
    rate = Decimal(str(provider.get("on_demand_rate_usd_per_tib")))
    if rate != config.on_demand_rate_usd_per_tib:
        raise ValueError("objective approval does not match the BigQuery pricing rate")
    execution = _mapping(configuration.get("execution"), "execution configuration")
    if execution.get("harness_sha256") != _file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    if execution.get("manual_candidate_executions") != 1:
        raise ValueError("objective approval must allow exactly one candidate execution")
    if execution.get("automatic_candidate_retry") is not False:
        raise ValueError("objective approval must disable automatic candidate retry")
    if execution.get("provider_operation_retries") != 0:
        raise ValueError("objective approval must disable provider-operation retries")
    objective_payload = _mapping(payload.get("approved_objectives"), "approved objectives")
    objectives = ApprovedObjectiveSet(
        names=tuple(cast("list[str]", objective_payload.get("names"))),
        benchmark_class=BenchmarkClass(str(objective_payload.get("benchmark_class"))),
        profile_id=str(objective_payload.get("profile_id")),
        release_version=str(objective_payload.get("release_version")),
        git_commit=str(objective_payload.get("git_commit")),
        image_digest=str(objective_payload.get("image_digest")),
        configuration_sha256=str(objective_payload.get("configuration_sha256")),
        approval_reference=str(objective_payload.get("approval_reference")),
    )
    if objectives.names != _OBJECTIVES:
        raise ValueError("objective approval names do not match BigQuery incremental qualification")
    if objectives.benchmark_class is not BenchmarkClass.INCREMENTAL:
        raise ValueError("objective approval benchmark class is not incremental")
    if (
        objectives.release_version != identity.release_version
        or objectives.git_commit != identity.git_commit
        or objectives.image_digest != identity.image_digest
        or objectives.approval_reference != identity.approval_reference
        or objectives.configuration_sha256 != config.configuration_sha256()
    ):
        raise ValueError("objective approval does not match the exact candidate")
    cost_payload = _mapping(payload.get("cost_ceiling"), "cost ceiling")
    cost_ceiling = ApprovedCostCeiling(
        amount_usd=Decimal(str(cost_payload.get("amount_usd"))),
        approval_reference=str(cost_payload.get("approval_reference")),
    )
    if cost_ceiling.approval_reference != identity.approval_reference:
        raise ValueError("cost ceiling approval does not match the candidate approval")
    return _Approval(
        objectives=objectives,
        cost_ceiling=cost_ceiling,
        project_sha256=_identifier_sha256(config.project),
        dataset=config.dataset,
        location=config.location,
        on_demand_rate_usd_per_tib=rate,
    )


def _run_incremental(
    config: BigQueryIncrementalConfig,
    client: _NoRetryClient,
) -> _IncrementalResult:
    dataset_id = f"{config.project}.{config.dataset}"
    if _dataset_exists(client, dataset_id):
        raise BigQueryIncrementalQualificationError("owned BigQuery dataset already exists")
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = config.location
    dataset.default_table_expiration_ms = 60 * 60 * 1_000
    dataset.labels = {"dander-owned": "true", "phase8": "bigquery-incremental"}
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    result: _IncrementalResult | None = None
    failure: Exception | None = None
    try:
        client.create_dataset(dataset)
        writer = BigQueryIncrementalWriter(
            project=config.project,
            cursor_field="cursor_value",
            client=cast("Any", client),
            max_batch_rows=config.batch_rows,
        )
        target = WriteTarget(
            project=config.project,
            dataset=config.dataset,
            table="incremental_records",
            business_key=("id",),
            schema=(
                WriteField(name="id", data_type="STRING", mode="REQUIRED"),
                WriteField(name="payload", data_type="STRING", mode="REQUIRED"),
                WriteField(name="cursor_value", data_type="INT64", mode="REQUIRED"),
            ),
        )
        cursor = _advance_cursor(None, 1)
        seed_started = time.perf_counter()
        seed_rows = writer.write(_seed_records(config), target)
        seed_ms = _elapsed_ms(seed_started)
        if seed_rows != config.seed_rows:
            raise BigQueryIncrementalQualificationError(
                "BigQuery incremental seed affected an unexpected row count"
            )

        cursor = _advance_cursor(cursor, 2)
        delta_started = time.perf_counter()
        delta_rows = writer.write(_delta_records(config), target)
        delta_ms = _elapsed_ms(delta_started)
        if delta_rows != config.delta_rows:
            raise BigQueryIncrementalQualificationError(
                "BigQuery incremental delta affected an unexpected row count"
            )

        regressions_rejected = 0
        try:
            _advance_cursor(cursor, 1)
        except BigQueryIncrementalQualificationError:
            regressions_rejected = 1
        if regressions_rejected != 1:
            raise BigQueryIncrementalQualificationError(
                "BigQuery incremental cursor regression was not rejected"
            )

        final_rows = config.seed_rows + (config.delta_rows // 2)
        updated_rows, inserted_rows = _require_incremental_result(
            client,
            config=config,
            expected_rows=final_rows,
        )
        staging = sum(
            1
            for table in client.list_tables(dataset_id)
            if str(getattr(table, "table_id", "")).startswith("_dander_stage_")
        )
        if staging:
            raise BigQueryIncrementalQualificationError(
                "BigQuery incremental qualification left temporary staging relations"
            )
        job_ids = tuple(
            sorted({str(job_id) for job in client.jobs if (job_id := getattr(job, "job_id", None))})
        )
        row_width = config.payload_bytes + 32
        result = _IncrementalResult(
            duration_ms=_elapsed_ms(started),
            peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
            seed_duration_ms=seed_ms,
            seed_rows=seed_rows,
            seed_logical_bytes=seed_rows * row_width,
            delta_duration_ms=delta_ms,
            delta_rows=delta_rows,
            delta_logical_bytes=delta_rows * row_width,
            final_rows=final_rows,
            updated_rows=updated_rows,
            inserted_rows=inserted_rows,
            regression_rows_affected=0,
            cursor_initial=1,
            cursor_final=cursor,
            cursor_regressions_rejected=regressions_rejected,
            load_jobs=_job_count(client.jobs, "load"),
            query_jobs=_job_count(client.jobs, "query"),
            bytes_processed=_job_total(client.jobs, "total_bytes_processed"),
            bytes_billed=_job_total(client.jobs, "total_bytes_billed"),
            slot_ms=_job_total(client.jobs, "slot_millis"),
            reservation_usage_records=sum(
                len(usage)
                for job in client.jobs
                if isinstance((usage := getattr(job, "reservation_usage", None)), list)
            ),
            job_ids=job_ids,
            temporary_staging_relations=staging,
            cleanup_verified=False,
        )
    except Exception as error:
        failure = error
    finally:
        try:
            client.delete_dataset(dataset_id, not_found_ok=True)
        except Exception as error:
            raise BigQueryIncrementalQualificationError(
                "BigQuery dataset cleanup failed"
            ) from error
    cleanup = not _dataset_exists(client, dataset_id)
    if not cleanup:
        raise BigQueryIncrementalQualificationError(
            "BigQuery dataset cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, BigQueryIncrementalQualificationError):
            raise failure
        raise BigQueryIncrementalQualificationError(
            "BigQuery incremental qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return replace(result, cleanup_verified=True)


def _require_incremental_result(
    client: _NoRetryClient,
    *,
    config: BigQueryIncrementalConfig,
    expected_rows: int,
) -> tuple[int, int]:
    boundary = f"{config.seed_rows:012d}"
    updated = config.delta_rows // 2
    query = (
        "SELECT COUNT(*) AS row_count, "
        f"COUNTIF(cursor_value = 2 AND id < '{boundary}') AS updated_rows, "
        f"COUNTIF(cursor_value = 2 AND id >= '{boundary}') AS inserted_rows, "
        f"COUNTIF(cursor_value = 2 AND payload = REPEAT('d', {config.payload_bytes})) "
        "AS delta_payload_rows, "
        f"COUNTIF(cursor_value = 1 AND payload = REPEAT('s', {config.payload_bytes})) "
        "AS unchanged_seed_rows, "
        "COUNTIF(cursor_value NOT IN (1, 2)) AS invalid_cursor_rows "
        f"FROM `{config.project}.{config.dataset}.incremental_records`"
    )
    query_config = bigquery.QueryJobConfig(
        use_query_cache=False,
        maximum_bytes_billed=config.verification_maximum_bytes_billed,
    )
    values = list(cast("Iterable[object]", client.query(query, job_config=query_config).result()))
    if len(values) != 1:
        raise BigQueryIncrementalQualificationError(
            "BigQuery incremental verification returned an invalid row count"
        )
    row = cast("Any", values[0])
    observed = (
        int(row["row_count"]),
        int(row["updated_rows"]),
        int(row["inserted_rows"]),
        int(row["delta_payload_rows"]),
        int(row["unchanged_seed_rows"]),
        int(row["invalid_cursor_rows"]),
    )
    expected = (
        expected_rows,
        updated,
        config.delta_rows - updated,
        config.delta_rows,
        config.seed_rows - updated,
        0,
    )
    if observed != expected:
        raise BigQueryIncrementalQualificationError(
            "BigQuery incremental readback differs from the accepted workload"
        )
    return observed[1], observed[2]


def _advance_cursor(current: int | None, proposed: int) -> int:
    if current is not None and proposed < current:
        raise BigQueryIncrementalQualificationError(
            "BigQuery incremental cursor regression rejected before provider mutation"
        )
    return proposed


def _report(
    config: BigQueryIncrementalConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _IncrementalResult,
) -> QualificationReport:
    ratio = Decimal(result.seed_rows) / Decimal(result.delta_rows)
    if ratio < 100:
        raise BigQueryIncrementalQualificationError(
            "BigQuery incremental target is less than 100 times its delta"
        )
    if result.cursor_final < result.cursor_initial or result.cursor_regressions_rejected != 1:
        raise BigQueryIncrementalQualificationError(
            "BigQuery incremental cursor evidence is incomplete"
        )
    if result.reservation_usage_records:
        raise BigQueryIncrementalQualificationError(
            "provider jobs used a reservation instead of approved on-demand billing"
        )
    gross_cost = (
        Decimal(result.bytes_billed) * approval.on_demand_rate_usd_per_tib / _TIB
    ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    if gross_cost > approval.cost_ceiling.amount_usd:
        raise BigQueryIncrementalQualificationError(
            "provider-metered cost exceeds its approved ceiling"
        )
    measured = PerformanceMeasurement.measured
    metrics = (
        measured("bigquery_bytes_billed", "bytes", result.bytes_billed),
        measured("bigquery_bytes_processed", "bytes", result.bytes_processed),
        measured("bigquery_load_jobs", "count", result.load_jobs),
        measured("bigquery_query_jobs", "count", result.query_jobs),
        measured(
            "bigquery_reservation_usage_records",
            "count",
            result.reservation_usage_records,
        ),
        measured("bigquery_slot_ms", "milliseconds", result.slot_ms),
        measured("cursor_final", "cursor", result.cursor_final),
        measured("cursor_initial", "cursor", result.cursor_initial),
        measured(
            "cursor_regressions_rejected",
            "count",
            result.cursor_regressions_rejected,
        ),
        measured("delta_duration_ms", "milliseconds", result.delta_duration_ms),
        measured("delta_target_ratio", "ratio", ratio),
        measured("final_target_rows", "rows", result.final_rows),
        measured("inserted_rows", "rows", result.inserted_rows),
        measured(
            "regression_rows_affected",
            "rows",
            result.regression_rows_affected,
        ),
        measured("seed_duration_ms", "milliseconds", result.seed_duration_ms),
        measured("seed_logical_bytes", "bytes", result.seed_logical_bytes),
        measured("seed_rows", "rows", result.seed_rows),
        measured(
            "temporary_staging_relations",
            "count",
            result.temporary_staging_relations,
        ),
        measured("updated_rows", "rows", result.updated_rows),
    )
    return QualificationReport(
        context=QualificationContext(
            release_version=identity.release_version,
            git_commit=identity.git_commit,
            image_digest=identity.image_digest,
            benchmark_date=identity.benchmark_date,
            profile_id=approval.objectives.profile_id,
            launcher=identity.launcher,
            warehouse="bigquery",
            state_backend="none",
            catalog="none",
            secret_provider=identity.secret_provider,
            regions=(config.location.lower(),),
            service_shapes=identity.service_shapes,
            provider_job_ids=result.job_ids,
            cost_ceiling=approval.cost_ceiling,
        ),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.INCREMENTAL,
            input_rows=result.delta_rows,
            logical_input_bytes=result.delta_logical_bytes,
            row_width_bytes=config.payload_bytes + 32,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="cursor_merge_small_delta",
            concurrency=1,
            batch_rows=config.batch_rows,
            batch_bytes=config.batch_rows * (config.payload_bytes + 32),
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.delta_rows),
            logical_bytes=measured(
                "logical_bytes",
                "bytes",
                result.delta_logical_bytes,
            ),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(result.delta_rows, result.delta_duration_ms),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured(
                "load_duration_ms",
                "milliseconds",
                result.seed_duration_ms + result.delta_duration_ms,
            ),
            transform_duration_ms=measured("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=metrics,
            costs=(
                CostAttribution(
                    provider="gcp",
                    service="bigquery_on_demand_analysis_gross",
                    amount=gross_cost,
                    estimated=False,
                ),
            ),
        ),
        objectives=tuple(
            ObjectiveResult(
                name=name,
                status=ObjectiveStatus.PASSED,
                evidence_reference=f"phase8/bigquery/incremental/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _create_client(config: BigQueryIncrementalConfig) -> _NoRetryClient:
    token = os.environ.get(config.token_env)
    credentials = cast("Any", Credentials)(token=token) if token else None
    client = bigquery.Client(project=config.project, credentials=credentials)
    return _NoRetryClient(client, location=config.location)


def _dataset_exists(client: _NoRetryClient, dataset: str) -> bool:
    try:
        client.get_dataset(dataset)
    except NotFound:
        return False
    return True


def _seed_records(config: BigQueryIncrementalConfig) -> Iterable[dict[str, object]]:
    padding = "s" * config.payload_bytes
    for index in range(config.seed_rows):
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 1}


def _delta_records(config: BigQueryIncrementalConfig) -> Iterable[dict[str, object]]:
    updated = config.delta_rows // 2
    padding = "d" * config.payload_bytes
    for index in range(updated):
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 2}
    for offset in range(config.delta_rows - updated):
        index = config.seed_rows + offset
        yield {"id": f"{index:012d}", "payload": padding, "cursor_value": 2}


def _job_count(jobs: Sequence[object], job_type: str) -> int:
    return sum(getattr(job, "job_type", None) == job_type for job in jobs)


def _job_total(jobs: Sequence[object], attribute: str) -> int:
    return sum(
        value
        for job in jobs
        if isinstance((value := getattr(job, attribute, None)), int) and not isinstance(value, bool)
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast("Mapping[str, object]", value)


def _identifier_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_project(value: str) -> bool:
    return (
        6 <= len(value) <= 30
        and value[0].islower()
        and value[-1].isalnum()
        and all(
            character.islower() or character.isdigit() or character == "-" for character in value
        )
    )


def _valid_identifier(value: str) -> bool:
    return (
        bool(value)
        and (value[0].isalpha() or value[0] == "_")
        and all(character.isalnum() or character == "_" for character in value)
    )


def _throughput(rows: int, duration_ms: int) -> Decimal:
    return (Decimal(rows) * 1_000 / Decimal(max(duration_ms, 1))).quantize(Decimal("0.001"))


def _elapsed_ms(started: float) -> int:
    return max(round((time.perf_counter() - started) * 1_000), 1)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1_024)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approval-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--location", default="US")
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--benchmark-date", type=date.fromisoformat, required=True)
    parser.add_argument("--launcher", default="docker_local")
    parser.add_argument("--secret-provider", default="ephemeral_access_token")
    parser.add_argument("--service-shape", action="append", required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    if arguments.release_version != __version__:
        raise BigQueryIncrementalQualificationError(
            "installed release does not match objective candidate"
        )
    config = BigQueryIncrementalConfig(
        project=arguments.project,
        dataset=arguments.dataset,
        location=arguments.location,
    )
    identity = CandidateIdentity(
        release_version=arguments.release_version,
        git_commit=arguments.git_commit,
        image_digest=arguments.image_digest,
        approval_reference=arguments.approval_reference,
        benchmark_date=arguments.benchmark_date,
        launcher=arguments.launcher,
        secret_provider=arguments.secret_provider,
        service_shapes=tuple(sorted(set(arguments.service_shape))),
    )
    approval = _load_approval(arguments.approval_manifest, config=config, identity=identity)
    result = _run_incremental(config, _create_client(config))
    report = _report(config, identity, approval, result)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (BigQueryIncrementalQualificationError, ValueError, OSError) as error:
        print(f"BigQuery incremental qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
