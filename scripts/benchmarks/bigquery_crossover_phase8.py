#!/usr/bin/env python3
"""Exact-RC30 BigQuery load-job/Storage-Write crossover qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import patch

from google.cloud import bigquery, bigquery_storage_v1
from google.oauth2.credentials import Credentials

from dander import __version__
from dander.concurrency import FencingToken
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
from dander.writer import (
    BigQueryScd1Writer,
    BigQueryStorageScd1Writer,
    SchemaEvolution,
    WriteField,
    WriteTarget,
    WriteTransport,
    storage_write,
)
from dander.writer import bigquery as bigquery_writer
from scripts.benchmarks import bigquery_incremental_phase8 as common

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.bigquery-crossover/v1"
_AUTHORITY_ID = "bigquery:phase8-crossover"
_OBJECTIVES = (
    "canonical_equality",
    "cleanup",
    "cost_ceiling",
    "crossover_measured",
    "fenced_publication",
    "load_job_transport_observed",
    "storage_write_transport_observed",
    "threshold_recorded",
)
_TIB = Decimal(1024**4)
_GIB = Decimal(1024**3)
_MAX_AUTHORIZED_COST_USD = Decimal("0.25")


class BigQueryCrossoverQualificationError(RuntimeError):
    """Raised with a credential-free crossover qualification summary."""


class _MutationJob(Protocol):
    def result(self) -> object:
        """Wait for exactly one submitted mutation."""
        ...


class _Writer(Protocol):
    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int: ...


@dataclass(frozen=True, slots=True)
class BigQueryCrossoverConfig:
    """Owned provider coordinates and the accepted bounded crossover workload."""

    project: str
    dataset: str
    location: str = "US"
    row_counts: tuple[int, ...] = (1, 10, 100, 1_000, 5_000)
    payload_bytes: int = 128
    repetitions: int = 5
    batch_rows: int = 5_000
    verification_maximum_bytes_billed: int = 256 * 1_024 * 1_024
    on_demand_rate_usd_per_tib: Decimal = Decimal("6.25")
    storage_write_rate_usd_per_gib: Decimal = Decimal("0.025")
    token_env: str = "DANDER_GCP_ACCESS_TOKEN"

    def __post_init__(self) -> None:
        if not common._valid_project(self.project):
            raise ValueError("project must be a valid Google Cloud project id")
        if not common._valid_identifier(self.dataset):
            raise ValueError("dataset must be a valid BigQuery dataset id")
        if not self.location or any(character.isspace() for character in self.location):
            raise ValueError("location must be a non-empty BigQuery location")
        if not self.row_counts or tuple(sorted(set(self.row_counts))) != self.row_counts:
            raise ValueError("row_counts must be unique positive values in ascending order")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in self.row_counts
        ):
            raise ValueError("row_counts must be unique positive values in ascending order")
        for name in (
            "payload_bytes",
            "repetitions",
            "batch_rows",
            "verification_maximum_bytes_billed",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.repetitions < 3 or self.repetitions % 2 == 0:
            raise ValueError("repetitions must be an odd integer of at least three")
        if self.row_counts != (1, 10, 100, 1_000, 5_000):
            raise ValueError("row_counts must preserve the accepted crossover sizes")
        if self.payload_bytes != 128 or self.repetitions != 5:
            raise ValueError("payload_bytes and repetitions must preserve the accepted workload")
        if self.batch_rows < self.row_counts[-1]:
            raise ValueError("batch_rows must contain each accepted crossover sample")
        for name in ("on_demand_rate_usd_per_tib", "storage_write_rate_usd_per_gib"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a positive Decimal")
        if not common._valid_identifier(self.token_env):
            raise ValueError("token_env must be a valid environment variable name")

    @property
    def row_width_bytes(self) -> int:
        return self.payload_bytes + 21

    def workload_payload(self) -> dict[str, object]:
        """Return the exact configuration covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.CROSSOVER.value,
            "row_counts": list(self.row_counts),
            "payload_bytes": self.payload_bytes,
            "repetitions": self.repetitions,
            "batch_rows": self.batch_rows,
            "write_mode": "scd1",
            "transports": [
                WriteTransport.LOAD_JOB.value,
                WriteTransport.STORAGE_WRITE.value,
            ],
            "verification_maximum_bytes_billed": self.verification_maximum_bytes_billed,
        }

    def configuration_sha256(self) -> str:
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
    storage_write_rate_usd_per_gib: Decimal


@dataclass(frozen=True, slots=True)
class _Sample:
    transport: WriteTransport
    rows: int
    repetition: int
    duration_ms: int
    canonical_sha256: str


@dataclass(frozen=True, slots=True)
class _CrossoverResult:
    duration_ms: int
    peak_rss_bytes: int
    samples: tuple[_Sample, ...]
    medians: Mapping[WriteTransport, Mapping[int, int]]
    recommended_storage_write_max_rows: int
    recommended_storage_write_max_logical_bytes: int
    fenced_publications: int
    load_jobs: int
    query_jobs: int
    bytes_processed: int
    bytes_billed: int
    slot_ms: int
    reservation_usage_records: int
    storage_write_append_requests: int
    storage_write_serialized_bytes: int
    provider_operation_retries: int
    job_ids: tuple[str, ...]
    temporary_staging_relations: int
    cleanup_verified: bool


class _NoRetryStorageClient:
    """Disable unary Storage Write retries and expose stream reconnections."""

    def __init__(self, client: bigquery_storage_v1.BigQueryWriteClient) -> None:
        self._client = client
        self.append_stream_connections = 0

    def create_write_stream(self, *args: object, **kwargs: object) -> object:
        kwargs["retry"] = None
        return self._client.create_write_stream(*args, **kwargs)  # type: ignore[arg-type]

    def finalize_write_stream(self, *args: object, **kwargs: object) -> object:
        kwargs["retry"] = None
        return self._client.finalize_write_stream(*args, **kwargs)  # type: ignore[arg-type]

    def batch_commit_write_streams(self, *args: object, **kwargs: object) -> object:
        kwargs["retry"] = None
        return self._client.batch_commit_write_streams(*args, **kwargs)  # type: ignore[arg-type]

    def append_rows(self, *args: object, **kwargs: object) -> object:
        self.append_stream_connections += 1
        return self._client.append_rows(*args, **kwargs)  # type: ignore[arg-type]

    def close(self) -> None:
        self._client.transport.close()  # type: ignore[no-untyped-call]


class _MeasuredStorageBackend:
    """Measure acknowledged protobuf payloads while reusing the product backend."""

    def __init__(self, client: _NoRetryStorageClient) -> None:
        self.client = client
        self._backend = storage_write.BigQueryPendingStreamBackend(client=cast("Any", client))
        self.append_requests = 0
        self.serialized_bytes = 0

    def append(
        self,
        rows: Sequence[Mapping[str, Any]],
        target: WriteTarget,
        *,
        max_batch_rows: int,
    ) -> None:
        message_class, _ = storage_write._message_type(target)
        encoded_bytes = sum(
            len(storage_write._serialize_row(message_class, row, target)) for row in rows
        )
        self._backend.append(rows, target, max_batch_rows=max_batch_rows)
        self.append_requests += math.ceil(len(rows) / max_batch_rows)
        self.serialized_bytes += encoded_bytes

    @property
    def provider_operation_retries(self) -> int:
        return max(self.client.append_stream_connections - self.append_requests, 0)


def _load_approval(
    path: Path,
    *,
    config: BigQueryCrossoverConfig,
    identity: CandidateIdentity,
) -> _Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective approval schema is incompatible")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective approval workload does not match the requested run")
    configuration = common._mapping(payload.get("configuration"), "configuration")
    provider = common._mapping(configuration.get("bigquery"), "BigQuery configuration")
    if provider.get("project_sha256") != common._identifier_sha256(config.project):
        raise ValueError("objective approval does not match the BigQuery project")
    if provider.get("dataset") != config.dataset or provider.get("location") != config.location:
        raise ValueError("objective approval does not match the owned BigQuery dataset")
    query_rate = Decimal(str(provider.get("on_demand_rate_usd_per_tib")))
    storage_write_rate = Decimal(str(provider.get("storage_write_rate_usd_per_gib")))
    if query_rate != config.on_demand_rate_usd_per_tib:
        raise ValueError("objective approval does not match the BigQuery pricing rate")
    if storage_write_rate != config.storage_write_rate_usd_per_gib:
        raise ValueError("objective approval does not match the Storage Write pricing rate")
    execution = common._mapping(configuration.get("execution"), "execution configuration")
    if execution.get("harness_sha256") != common._file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    if execution.get("manual_candidate_executions") != 1:
        raise ValueError("objective approval must allow exactly one candidate execution")
    if execution.get("automatic_candidate_retry") is not False:
        raise ValueError("objective approval must disable automatic candidate retry")
    if execution.get("provider_operation_retries") != 0:
        raise ValueError("objective approval must disable provider-operation retries")
    objective_payload = common._mapping(payload.get("approved_objectives"), "approved objectives")
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
        raise ValueError("objective approval names do not match BigQuery crossover qualification")
    if objectives.benchmark_class is not BenchmarkClass.CROSSOVER:
        raise ValueError("objective approval benchmark class is not crossover")
    if (
        objectives.release_version != identity.release_version
        or objectives.git_commit != identity.git_commit
        or objectives.image_digest != identity.image_digest
        or objectives.approval_reference != identity.approval_reference
        or objectives.configuration_sha256 != config.configuration_sha256()
    ):
        raise ValueError("objective approval does not match the exact candidate")
    cost_payload = common._mapping(payload.get("cost_ceiling"), "cost ceiling")
    cost_ceiling = ApprovedCostCeiling(
        amount_usd=Decimal(str(cost_payload.get("amount_usd"))),
        approval_reference=str(cost_payload.get("approval_reference")),
    )
    if cost_ceiling.approval_reference != identity.approval_reference:
        raise ValueError("cost ceiling approval does not match the candidate approval")
    if cost_ceiling.amount_usd > _MAX_AUTHORIZED_COST_USD:
        raise ValueError("cost ceiling exceeds the authorized maximum")
    return _Approval(
        objectives=objectives,
        cost_ceiling=cost_ceiling,
        project_sha256=common._identifier_sha256(config.project),
        dataset=config.dataset,
        location=config.location,
        on_demand_rate_usd_per_tib=query_rate,
        storage_write_rate_usd_per_gib=storage_write_rate,
    )


def _run_crossover(
    config: BigQueryCrossoverConfig,
    client: common._NoRetryClient,
    storage_backend: _MeasuredStorageBackend,
) -> _CrossoverResult:
    dataset_id = f"{config.project}.{config.dataset}"
    if common._dataset_exists(client, dataset_id):
        raise BigQueryCrossoverQualificationError("owned BigQuery dataset already exists")
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = config.location
    dataset.default_table_expiration_ms = 60 * 60 * 1_000
    dataset.labels = {"dander-owned": "true", "phase8": "bigquery-crossover"}
    started = time.perf_counter()
    peak_before = common._peak_rss_bytes()
    result: _CrossoverResult | None = None
    failure: Exception | None = None
    try:
        client.create_dataset(dataset)
        lease = _create_lease(config, client)
        writers: Mapping[WriteTransport, _Writer] = {
            WriteTransport.LOAD_JOB: BigQueryScd1Writer(
                project=config.project,
                client=cast("Any", client),
                max_batch_rows=config.batch_rows,
                schema_evolution=SchemaEvolution.STRICT,
            ),
            WriteTransport.STORAGE_WRITE: BigQueryStorageScd1Writer(
                project=config.project,
                client=cast("Any", client),
                backend=storage_backend,
                max_batch_rows=config.batch_rows,
                schema_evolution=SchemaEvolution.STRICT,
            ),
        }
        samples: list[_Sample] = []
        with _zero_provider_operation_retries():
            for row_count in config.row_counts:
                expected_hash = _expected_sha256(row_count, config.payload_bytes)
                for repetition in range(config.repetitions):
                    pair: dict[WriteTransport, str] = {}
                    for transport in (WriteTransport.LOAD_JOB, WriteTransport.STORAGE_WRITE):
                        sample = _run_sample(
                            writers[transport],
                            client,
                            config=config,
                            lease=lease,
                            transport=transport,
                            rows=row_count,
                            repetition=repetition,
                        )
                        if sample.canonical_sha256 != expected_hash:
                            raise BigQueryCrossoverQualificationError(
                                "BigQuery crossover output differs from the accepted fixture"
                            )
                        pair[transport] = sample.canonical_sha256
                        samples.append(sample)
                    if pair[WriteTransport.LOAD_JOB] != pair[WriteTransport.STORAGE_WRITE]:
                        raise BigQueryCrossoverQualificationError(
                            "BigQuery crossover transports produced unequal output"
                        )
        medians = _median_durations(tuple(samples), config.row_counts)
        threshold = _recommended_storage_write_max_rows(medians, config.row_counts)
        staging = sum(
            1
            for table in client.list_tables(dataset_id)
            if str(getattr(table, "table_id", "")).startswith("_dander_stage_")
        )
        if staging:
            raise BigQueryCrossoverQualificationError(
                "BigQuery crossover qualification left temporary staging relations"
            )
        jobs = tuple(client.jobs)
        job_ids = tuple(
            sorted({str(job_id) for job in jobs if (job_id := getattr(job, "job_id", None))})
        )
        fenced_publications = sum(
            "ASSERT @@row_count = 1 AS 'Dander pipeline lease lost'"
            in str(getattr(job, "query", ""))
            for job in jobs
        )
        result = _CrossoverResult(
            duration_ms=common._elapsed_ms(started),
            peak_rss_bytes=max(peak_before, common._peak_rss_bytes()),
            samples=tuple(samples),
            medians=medians,
            recommended_storage_write_max_rows=threshold,
            recommended_storage_write_max_logical_bytes=threshold * config.row_width_bytes,
            fenced_publications=fenced_publications,
            load_jobs=common._job_count(jobs, "load"),
            query_jobs=common._job_count(jobs, "query"),
            bytes_processed=common._job_total(jobs, "total_bytes_processed"),
            bytes_billed=common._job_total(jobs, "total_bytes_billed"),
            slot_ms=common._job_total(jobs, "slot_millis"),
            reservation_usage_records=sum(
                len(usage)
                for job in jobs
                if isinstance((usage := getattr(job, "reservation_usage", None)), list)
            ),
            storage_write_append_requests=storage_backend.append_requests,
            storage_write_serialized_bytes=storage_backend.serialized_bytes,
            provider_operation_retries=storage_backend.provider_operation_retries,
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
            raise BigQueryCrossoverQualificationError("BigQuery dataset cleanup failed") from error
    cleanup = not common._dataset_exists(client, dataset_id)
    if not cleanup:
        raise BigQueryCrossoverQualificationError("BigQuery dataset cleanup could not be verified")
    if failure is not None:
        if isinstance(failure, BigQueryCrossoverQualificationError):
            raise failure
        raise BigQueryCrossoverQualificationError(
            "BigQuery crossover qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return replace(result, cleanup_verified=True)


def _create_lease(
    config: BigQueryCrossoverConfig,
    client: common._NoRetryClient,
) -> FencingToken:
    table = f"{config.project}.{config.dataset}._dander_leases"
    client.query(
        f"CREATE TABLE `{table}` ("
        "pipeline_id STRING NOT NULL, run_id STRING NOT NULL, "
        "fencing_token INT64 NOT NULL, heartbeat_at TIMESTAMP NOT NULL, "
        "lease_expires_at TIMESTAMP NOT NULL) CLUSTER BY pipeline_id"
    ).result()
    client.query(
        f"INSERT INTO `{table}` "
        "(pipeline_id, run_id, fencing_token, heartbeat_at, lease_expires_at) "
        "VALUES ('phase8_bigquery_crossover', 'crossover-one', 1, "
        "CURRENT_TIMESTAMP(), TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE))"
    ).result()
    return FencingToken(
        lease_table=table,
        pipeline_id="phase8_bigquery_crossover",
        run_id="crossover-one",
        token=1,
        authority_id=_AUTHORITY_ID,
    )


def _run_sample(
    writer: _Writer,
    client: common._NoRetryClient,
    *,
    config: BigQueryCrossoverConfig,
    lease: FencingToken,
    transport: WriteTransport,
    rows: int,
    repetition: int,
) -> _Sample:
    table = f"{transport.value}_{rows:05d}_{repetition}"
    target = WriteTarget(
        project=config.project,
        dataset=config.dataset,
        table=table,
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="payload", data_type="STRING", mode="REQUIRED"),
        ),
        fence=lease,
    )
    started = time.perf_counter()
    affected = writer.write(_records(rows, config.payload_bytes), target)
    duration_ms = common._elapsed_ms(started)
    if affected != rows:
        raise BigQueryCrossoverQualificationError(
            "BigQuery crossover write returned an unexpected row count"
        )
    canonical_sha256 = _require_table_shape(
        client,
        config=config,
        table=table,
        rows=rows,
    )
    return _Sample(
        transport=transport,
        rows=rows,
        repetition=repetition,
        duration_ms=duration_ms,
        canonical_sha256=canonical_sha256,
    )


def _require_table_shape(
    client: common._NoRetryClient,
    *,
    config: BigQueryCrossoverConfig,
    table: str,
    rows: int,
) -> str:
    query = (
        "SELECT COUNT(*) AS row_count, COUNT(DISTINCT id) AS distinct_row_count, "
        "COALESCE(SUM(BYTE_LENGTH(payload)), 0) AS payload_bytes, "
        "TO_HEX(SHA256(STRING_AGG(CONCAT(id, payload), '' ORDER BY id))) "
        "AS canonical_sha256 "
        f"FROM `{config.project}.{config.dataset}.{table}`"
    )
    query_config = bigquery.QueryJobConfig(
        use_query_cache=False,
        maximum_bytes_billed=config.verification_maximum_bytes_billed,
    )
    values = list(cast("Iterable[object]", client.query(query, job_config=query_config).result()))
    if len(values) != 1:
        raise BigQueryCrossoverQualificationError(
            "BigQuery crossover verification returned an invalid result"
        )
    row = cast("Any", values[0])
    if (
        int(row["row_count"]) != rows
        or int(row["distinct_row_count"]) != rows
        or int(row["payload_bytes"]) != rows * config.payload_bytes
    ):
        raise BigQueryCrossoverQualificationError(
            "BigQuery crossover table shape differs from the accepted workload"
        )
    digest = str(row["canonical_sha256"]).lower()
    if len(digest) != 64:
        raise BigQueryCrossoverQualificationError(
            "BigQuery crossover verification returned an invalid digest"
        )
    return digest


def _records(rows: int, payload_bytes: int) -> Iterable[dict[str, object]]:
    padding = "x" * payload_bytes
    for index in range(rows):
        yield {"id": f"{index:012d}", "payload": padding}


def _expected_sha256(rows: int, payload_bytes: int) -> str:
    digest = hashlib.sha256()
    padding = ("x" * payload_bytes).encode()
    for index in range(rows):
        digest.update(f"{index:012d}".encode())
        digest.update(padding)
    return digest.hexdigest()


def _median_durations(
    samples: Sequence[_Sample],
    row_counts: Sequence[int],
) -> Mapping[WriteTransport, Mapping[int, int]]:
    return {
        transport: {
            rows: round(
                statistics.median(
                    sample.duration_ms
                    for sample in samples
                    if sample.transport is transport and sample.rows == rows
                )
            )
            for rows in row_counts
        }
        for transport in (WriteTransport.LOAD_JOB, WriteTransport.STORAGE_WRITE)
    }


def _recommended_storage_write_max_rows(
    medians: Mapping[WriteTransport, Mapping[int, int]],
    row_counts: Sequence[int],
) -> int:
    recommendation = 0
    for rows in row_counts:
        if medians[WriteTransport.STORAGE_WRITE][rows] > medians[WriteTransport.LOAD_JOB][rows]:
            break
        recommendation = rows
    return recommendation


@contextmanager
def _zero_provider_operation_retries() -> Iterator[None]:
    with (
        patch.object(bigquery_writer, "run_mutation_with_retry", _run_mutation_once),
        patch.object(storage_write, "run_mutation_with_retry", _run_mutation_once),
    ):
        yield


def _run_mutation_once[JobT: _MutationJob](submit: Callable[[], JobT]) -> JobT:
    job = submit()
    job.result()
    return job


def _report(
    config: BigQueryCrossoverConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _CrossoverResult,
) -> QualificationReport:
    expected_samples = len(config.row_counts) * config.repetitions * 2
    expected_storage_requests = len(config.row_counts) * config.repetitions
    if (
        len(result.samples) != expected_samples
        or result.fenced_publications != expected_samples
        or result.storage_write_append_requests != expected_storage_requests
        or result.provider_operation_retries != 0
        or result.temporary_staging_relations != 0
        or not result.cleanup_verified
    ):
        raise BigQueryCrossoverQualificationError("BigQuery crossover evidence is incomplete")
    if result.reservation_usage_records:
        raise BigQueryCrossoverQualificationError(
            "provider jobs used a reservation instead of approved on-demand billing"
        )
    query_cost = (
        Decimal(result.bytes_billed) * approval.on_demand_rate_usd_per_tib / _TIB
    ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    storage_write_cost = (
        Decimal(result.storage_write_serialized_bytes)
        * approval.storage_write_rate_usd_per_gib
        / _GIB
    ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    gross_cost = query_cost + storage_write_cost
    if gross_cost > approval.cost_ceiling.amount_usd:
        raise BigQueryCrossoverQualificationError(
            "provider-metered cost exceeds its approved ceiling"
        )
    rows = sum(config.row_counts) * config.repetitions * 2
    logical_bytes = rows * config.row_width_bytes
    measured = PerformanceMeasurement.measured
    metrics: list[PerformanceMeasurement] = [
        measured("bigquery_bytes_billed", "bytes", result.bytes_billed),
        measured("bigquery_bytes_processed", "bytes", result.bytes_processed),
        measured("bigquery_load_jobs", "count", result.load_jobs),
        measured("bigquery_query_jobs", "count", result.query_jobs),
        measured("bigquery_reservation_usage_records", "count", result.reservation_usage_records),
        measured("bigquery_slot_ms", "milliseconds", result.slot_ms),
        measured("fenced_publications", "count", result.fenced_publications),
        measured("provider_operation_retries", "count", result.provider_operation_retries),
        measured(
            "recommended_storage_write_max_logical_bytes",
            "bytes",
            result.recommended_storage_write_max_logical_bytes,
        ),
        measured(
            "recommended_storage_write_max_rows",
            "rows",
            result.recommended_storage_write_max_rows,
        ),
        measured("storage_write_append_requests", "count", result.storage_write_append_requests),
        measured("storage_write_serialized_bytes", "bytes", result.storage_write_serialized_bytes),
        measured("temporary_staging_relations", "count", result.temporary_staging_relations),
    ]
    for transport in (WriteTransport.LOAD_JOB, WriteTransport.STORAGE_WRITE):
        for row_count in config.row_counts:
            metrics.append(
                measured(
                    f"{transport.value}_{row_count}_median_duration_ms",
                    "milliseconds",
                    result.medians[transport][row_count],
                )
            )
    for row_count in config.row_counts:
        load_ms = result.medians[WriteTransport.LOAD_JOB][row_count]
        storage_ms = result.medians[WriteTransport.STORAGE_WRITE][row_count]
        metrics.append(
            measured(
                f"storage_write_to_load_job_{row_count}_duration_ratio",
                "ratio",
                (Decimal(storage_ms) / Decimal(max(load_ms, 1))).quantize(Decimal("0.001")),
            )
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
            benchmark_class=BenchmarkClass.CROSSOVER,
            input_rows=rows,
            logical_input_bytes=logical_bytes,
            row_width_bytes=config.row_width_bytes,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="scd1_equal_transport_comparison",
            concurrency=1,
            batch_rows=config.batch_rows,
            batch_bytes=config.batch_rows * config.row_width_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", rows),
            logical_bytes=measured("logical_bytes", "bytes", logical_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                common._throughput(rows, result.duration_ms),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured("load_duration_ms", "milliseconds", result.duration_ms),
            transform_duration_ms=measured("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=tuple(sorted(metrics, key=lambda metric: metric.name)),
            costs=(
                CostAttribution(
                    provider="gcp",
                    service="bigquery_analysis_and_storage_write_gross",
                    amount=gross_cost,
                    estimated=False,
                ),
            ),
        ),
        objectives=tuple(
            ObjectiveResult(
                name=name,
                status=ObjectiveStatus.PASSED,
                evidence_reference=f"phase8/bigquery/crossover/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _create_clients(
    config: BigQueryCrossoverConfig,
) -> tuple[common._NoRetryClient, _MeasuredStorageBackend]:
    token = os.environ.get(config.token_env)
    credentials = cast("Any", Credentials)(token=token) if token else None
    query_client = bigquery.Client(project=config.project, credentials=credentials)
    storage_client = bigquery_storage_v1.BigQueryWriteClient(  # type: ignore[no-untyped-call]
        credentials=credentials
    )
    no_retry_storage = _NoRetryStorageClient(storage_client)
    return (
        common._NoRetryClient(query_client, location=config.location),
        _MeasuredStorageBackend(no_retry_storage),
    )


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
        raise BigQueryCrossoverQualificationError(
            "installed release does not match objective candidate"
        )
    config = BigQueryCrossoverConfig(
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
    client, storage_backend = _create_clients(config)
    try:
        result = _run_crossover(config, client, storage_backend)
    finally:
        storage_backend.client.close()
    report = _report(config, identity, approval, result)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (BigQueryCrossoverQualificationError, ValueError, OSError) as error:
        print(f"BigQuery crossover qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
