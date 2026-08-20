#!/usr/bin/env python3
"""Exact-candidate BigQuery concurrency Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import patch

from google.api_core.exceptions import BadRequest, NotFound
from google.cloud import bigquery
from google.oauth2.credentials import Credentials

from dander import __version__
from dander.concurrency import FencingToken
from dander.providers.bigquery import fence as bigquery_fence
from dander.providers.bigquery.fence import BigQueryTargetFence
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
from dander.warehouse import RelationRef
from dander.writer import BigQueryScd1Writer, WriteField, WriteTarget
from scripts.benchmarks import bigquery_incremental_phase8 as common

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.bigquery-concurrency/v1"
_AUTHORITY_ID = "bigquery:phase8-concurrency"
_OBJECTIVES = (
    "cleanup",
    "concurrent_pipeline_completion",
    "controlled_contention",
    "cost_ceiling",
    "stale_fence_rejection",
    "throughput_measurement",
)
_TIB = Decimal(1024**4)
_MAX_AUTHORIZED_COST_USD = Decimal("0.25")


class BigQueryConcurrencyQualificationError(RuntimeError):
    """Raised with a credential-free concurrency qualification summary."""


class _MutationJob(Protocol):
    def result(self) -> object:
        """Wait for exactly one submitted mutation."""
        ...


class _ConcurrencyClient(Protocol):
    @property
    def jobs(self) -> Sequence[object]:
        """Return every provider job submitted by this harness."""
        ...

    def create_dataset(self, dataset: bigquery.Dataset) -> bigquery.Dataset: ...

    def get_dataset(self, dataset: str) -> bigquery.Dataset: ...

    def delete_dataset(self, dataset: str, *, not_found_ok: bool) -> None: ...

    def create_table(self, table: bigquery.Table, *, exists_ok: bool = False) -> bigquery.Table: ...

    def get_table(self, table: str) -> bigquery.Table: ...

    def update_table(self, table: bigquery.Table, fields: Sequence[str]) -> bigquery.Table: ...

    def load_table_from_json(
        self,
        json_rows: Sequence[Mapping[str, Any]],
        destination: str,
        *,
        job_config: bigquery.LoadJobConfig,
    ) -> _MutationJob: ...

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> _MutationJob: ...

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None: ...

    def list_tables(self, dataset: str) -> Iterable[object]: ...


@dataclass(frozen=True, slots=True)
class BigQueryConcurrencyConfig:
    """Owned provider coordinates and the accepted concurrent workload."""

    project: str
    dataset: str
    location: str = "US"
    concurrent_pipelines: int = 4
    rows_per_pipeline: int = 5_000
    payload_bytes: int = 128
    batch_rows: int = 5_000
    verification_maximum_bytes_billed: int = 256 * 1_024 * 1_024
    on_demand_rate_usd_per_tib: Decimal = Decimal("6.25")
    token_env: str = "DANDER_GCP_ACCESS_TOKEN"

    def __post_init__(self) -> None:
        if not common._valid_project(self.project):
            raise ValueError("project must be a valid Google Cloud project id")
        if not common._valid_identifier(self.dataset):
            raise ValueError("dataset must be a valid BigQuery dataset id")
        if not self.location or any(character.isspace() for character in self.location):
            raise ValueError("location must be a non-empty BigQuery location")
        for name in (
            "concurrent_pipelines",
            "rows_per_pipeline",
            "payload_bytes",
            "batch_rows",
            "verification_maximum_bytes_billed",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.concurrent_pipelines != 4:
            raise ValueError("concurrent_pipelines must be exactly 4")
        if self.rows_per_pipeline != 5_000:
            raise ValueError("rows_per_pipeline must be exactly 5000")
        if self.batch_rows > self.rows_per_pipeline:
            raise ValueError("batch_rows must not exceed rows_per_pipeline")
        if self.batch_rows * (self.payload_bytes + 24) > 256 * 1_024 * 1_024:
            raise ValueError("one batch must not exceed 256 MiB of logical input")
        if (
            not isinstance(self.on_demand_rate_usd_per_tib, Decimal)
            or not self.on_demand_rate_usd_per_tib.is_finite()
            or self.on_demand_rate_usd_per_tib <= 0
        ):
            raise ValueError("on_demand_rate_usd_per_tib must be a positive Decimal")
        if not common._valid_identifier(self.token_env):
            raise ValueError("token_env must be a valid environment variable name")

    def workload_payload(self) -> dict[str, object]:
        """Return the exact configuration covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.CONCURRENT_PIPELINES.value,
            "concurrent_pipelines": self.concurrent_pipelines,
            "rows_per_pipeline": self.rows_per_pipeline,
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
class _ConcurrencyResult:
    duration_ms: int
    pipeline_duration_ms: int
    peak_rss_bytes: int
    pipeline_count: int
    rows_per_pipeline: int
    total_rows: int
    logical_input_bytes: int
    concurrent_claim_attempts: int
    stale_publications_rejected: int
    load_jobs: int
    query_jobs: int
    bytes_processed: int
    bytes_billed: int
    slot_ms: int
    reservation_usage_records: int
    job_ids: tuple[str, ...]
    provider_operation_retries: int
    temporary_staging_relations: int
    cleanup_verified: bool


def _load_approval(
    path: Path,
    *,
    config: BigQueryConcurrencyConfig,
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
    rate = Decimal(str(provider.get("on_demand_rate_usd_per_tib")))
    if rate != config.on_demand_rate_usd_per_tib:
        raise ValueError("objective approval does not match the BigQuery pricing rate")
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
        raise ValueError("objective approval names do not match BigQuery concurrency qualification")
    if objectives.benchmark_class is not BenchmarkClass.CONCURRENT_PIPELINES:
        raise ValueError("objective approval benchmark class is not concurrent_pipelines")
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
        on_demand_rate_usd_per_tib=rate,
    )


def _run_concurrency(
    config: BigQueryConcurrencyConfig,
    client: _ConcurrencyClient,
) -> _ConcurrencyResult:
    dataset_id = f"{config.project}.{config.dataset}"
    if _dataset_exists(client, dataset_id):
        raise BigQueryConcurrencyQualificationError("owned BigQuery dataset already exists")
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = config.location
    dataset.default_table_expiration_ms = 60 * 60 * 1_000
    dataset.labels = {"dander-owned": "true", "phase8": "bigquery-concurrency"}
    started = time.perf_counter()
    peak_before = common._peak_rss_bytes()
    result: _ConcurrencyResult | None = None
    failure: Exception | None = None
    try:
        client.create_dataset(dataset)
        with _zero_provider_operation_retries():
            pipelines_started = time.perf_counter()
            affected = _write_independent_pipelines(config, client)
            total_rows = _require_independent_readback(config, client)
            pipeline_duration_ms = common._elapsed_ms(pipelines_started)
            if affected != total_rows or total_rows != (
                config.concurrent_pipelines * config.rows_per_pipeline
            ):
                raise BigQueryConcurrencyQualificationError(
                    "BigQuery concurrent pipeline readback differs from the accepted workload"
                )
            stale_rejected, claim_attempts = _exercise_controlled_contention(config, client)
        if not stale_rejected or claim_attempts != 2:
            raise BigQueryConcurrencyQualificationError(
                "BigQuery controlled contention evidence is incomplete"
            )
        staging = sum(
            1
            for table in client.list_tables(dataset_id)
            if str(getattr(table, "table_id", "")).startswith("_dander_stage_")
        )
        if staging:
            raise BigQueryConcurrencyQualificationError(
                "BigQuery concurrency qualification left temporary staging relations"
            )
        job_ids = tuple(
            sorted({str(job_id) for job in client.jobs if (job_id := getattr(job, "job_id", None))})
        )
        result = _ConcurrencyResult(
            duration_ms=common._elapsed_ms(started),
            pipeline_duration_ms=pipeline_duration_ms,
            peak_rss_bytes=max(peak_before, common._peak_rss_bytes()),
            pipeline_count=config.concurrent_pipelines,
            rows_per_pipeline=config.rows_per_pipeline,
            total_rows=total_rows,
            logical_input_bytes=total_rows * (config.payload_bytes + 24),
            concurrent_claim_attempts=claim_attempts,
            stale_publications_rejected=1,
            load_jobs=common._job_count(client.jobs, "load"),
            query_jobs=common._job_count(client.jobs, "query"),
            bytes_processed=common._job_total(client.jobs, "total_bytes_processed"),
            bytes_billed=common._job_total(client.jobs, "total_bytes_billed"),
            slot_ms=common._job_total(client.jobs, "slot_millis"),
            reservation_usage_records=sum(
                len(usage)
                for job in client.jobs
                if isinstance((usage := getattr(job, "reservation_usage", None)), list)
            ),
            job_ids=job_ids,
            provider_operation_retries=0,
            temporary_staging_relations=staging,
            cleanup_verified=False,
        )
    except Exception as error:
        failure = error
    finally:
        try:
            client.delete_dataset(dataset_id, not_found_ok=True)
        except Exception as error:
            raise BigQueryConcurrencyQualificationError(
                "BigQuery dataset cleanup failed"
            ) from error
    cleanup = not _dataset_exists(client, dataset_id)
    if not cleanup:
        raise BigQueryConcurrencyQualificationError(
            "BigQuery dataset cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, BigQueryConcurrencyQualificationError):
            raise failure
        raise BigQueryConcurrencyQualificationError(
            "BigQuery concurrency qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return replace(result, cleanup_verified=True)


def _write_independent_pipelines(
    config: BigQueryConcurrencyConfig,
    client: _ConcurrencyClient,
) -> int:
    with ThreadPoolExecutor(max_workers=config.concurrent_pipelines) as executor:
        futures = tuple(
            executor.submit(_write_pipeline, config, client, index)
            for index in range(config.concurrent_pipelines)
        )
        affected = tuple(future.result() for future in futures)
    if affected != (config.rows_per_pipeline,) * config.concurrent_pipelines:
        raise BigQueryConcurrencyQualificationError(
            "BigQuery concurrent pipeline affected an unexpected row count"
        )
    return sum(affected)


def _write_pipeline(
    config: BigQueryConcurrencyConfig,
    client: _ConcurrencyClient,
    index: int,
) -> int:
    writer = BigQueryScd1Writer(
        project=config.project,
        client=cast("Any", client),
        max_batch_rows=config.batch_rows,
    )
    target = WriteTarget(
        project=config.project,
        dataset=config.dataset,
        table=f"pipeline_{index:02d}_records",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="payload", data_type="STRING", mode="REQUIRED"),
        ),
    )
    return writer.write(_records(config), target)


def _require_independent_readback(
    config: BigQueryConcurrencyConfig,
    client: _ConcurrencyClient,
) -> int:
    union = " UNION ALL ".join(
        "SELECT "
        f"{index} AS pipeline_index, id, payload FROM "
        f"`{config.project}.{config.dataset}.pipeline_{index:02d}_records`"
        for index in range(config.concurrent_pipelines)
    )
    query = (
        "SELECT pipeline_index, COUNT(*) AS row_count, "
        "COUNT(DISTINCT id) AS distinct_row_count, "
        f"COUNTIF(LENGTH(payload) = {config.payload_bytes}) AS payload_row_count "
        f"FROM ({union}) GROUP BY pipeline_index ORDER BY pipeline_index"
    )
    query_config = bigquery.QueryJobConfig(
        use_query_cache=False,
        maximum_bytes_billed=config.verification_maximum_bytes_billed,
    )
    values = list(cast("Iterable[object]", client.query(query, job_config=query_config).result()))
    expected = tuple(
        (index, config.rows_per_pipeline, config.rows_per_pipeline, config.rows_per_pipeline)
        for index in range(config.concurrent_pipelines)
    )
    observed = tuple(
        (
            int(cast("Any", row)["pipeline_index"]),
            int(cast("Any", row)["row_count"]),
            int(cast("Any", row)["distinct_row_count"]),
            int(cast("Any", row)["payload_row_count"]),
        )
        for row in values
    )
    if observed != expected:
        raise BigQueryConcurrencyQualificationError(
            "BigQuery concurrent pipeline readback differs from the accepted workload"
        )
    return sum(row[1] for row in observed)


def _exercise_controlled_contention(
    config: BigQueryConcurrencyConfig,
    client: _ConcurrencyClient,
) -> tuple[bool, int]:
    table_id = f"{config.project}.{config.dataset}.contention_records"
    table = bigquery.Table(
        table_id,
        schema=(
            bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("payload", "STRING", mode="REQUIRED"),
        ),
    )
    client.create_table(table)
    relation = RelationRef(
        catalog=config.project,
        namespace=config.dataset,
        name="contention_records",
    )
    target_fence = BigQueryTargetFence(config.project, client=cast("Any", client))
    old_token = FencingToken(
        lease_table=None,
        pipeline_id="phase8_bigquery_concurrency_contention",
        run_id="contention-old",
        token=20,
        authority_id=_AUTHORITY_ID,
    )
    old_publication = target_fence.claim(relation, old_token)
    newer_token = FencingToken(
        lease_table=None,
        pipeline_id=old_token.pipeline_id,
        run_id="contention-new",
        token=21,
        authority_id=old_token.authority_id,
    )
    newer_publication = target_fence.claim(relation, newer_token)
    if newer_publication.token != newer_token.token:
        raise BigQueryConcurrencyQualificationError(
            "BigQuery controlled contention did not retain the newer claim"
        )
    prepared = target_fence.prepare_dml(
        f"INSERT INTO `{table_id}` (id, payload) VALUES ('stale', 'must-not-publish')",
        old_publication,
    )
    try:
        client.query(prepared.sql, job_config=cast("Any", prepared.options)).result()
    except BadRequest as error:
        if "dander destination fence lost" not in str(error).lower():
            raise BigQueryConcurrencyQualificationError(
                "BigQuery stale publication failed for an unexpected provider reason"
            ) from None
    else:
        raise BigQueryConcurrencyQualificationError(
            "BigQuery concurrency qualification accepted a stale publication"
        )
    query_config = bigquery.QueryJobConfig(
        use_query_cache=False,
        maximum_bytes_billed=config.verification_maximum_bytes_billed,
    )
    values = list(
        cast(
            "Iterable[object]",
            client.query(
                f"SELECT COUNT(*) AS row_count FROM `{table_id}`",
                job_config=query_config,
            ).result(),
        )
    )
    if len(values) != 1 or int(cast("Any", values[0])["row_count"]) != 0:
        raise BigQueryConcurrencyQualificationError(
            "BigQuery stale publication changed the contended target"
        )
    return True, 2


@contextmanager
def _zero_provider_operation_retries() -> Iterator[None]:
    with patch.object(bigquery_fence, "run_mutation_with_retry", _run_mutation_once):
        yield


def _run_mutation_once[JobT: _MutationJob](submit: Callable[[], JobT]) -> JobT:
    job = submit()
    job.result()
    return job


def _records(config: BigQueryConcurrencyConfig) -> Iterable[dict[str, object]]:
    padding = "x" * config.payload_bytes
    for index in range(config.rows_per_pipeline):
        yield {"id": f"{index:012d}", "payload": padding}


def _dataset_exists(client: _ConcurrencyClient, dataset: str) -> bool:
    try:
        client.get_dataset(dataset)
    except NotFound:
        return False
    return True


def _report(
    config: BigQueryConcurrencyConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _ConcurrencyResult,
) -> QualificationReport:
    if (
        result.total_rows != config.concurrent_pipelines * config.rows_per_pipeline
        or result.concurrent_claim_attempts != 2
        or result.stale_publications_rejected != 1
        or result.provider_operation_retries != 0
        or result.temporary_staging_relations != 0
        or not result.cleanup_verified
    ):
        raise BigQueryConcurrencyQualificationError("BigQuery concurrency evidence is incomplete")
    if result.reservation_usage_records:
        raise BigQueryConcurrencyQualificationError(
            "provider jobs used a reservation instead of approved on-demand billing"
        )
    gross_cost = (
        Decimal(result.bytes_billed) * approval.on_demand_rate_usd_per_tib / _TIB
    ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    if gross_cost > approval.cost_ceiling.amount_usd:
        raise BigQueryConcurrencyQualificationError(
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
        measured("concurrent_claim_attempts", "count", result.concurrent_claim_attempts),
        measured("pipeline_count", "count", result.pipeline_count),
        measured("pipeline_duration_ms", "milliseconds", result.pipeline_duration_ms),
        measured("provider_operation_retries", "count", result.provider_operation_retries),
        measured("readback_rows", "rows", result.total_rows),
        measured("rows_per_pipeline", "rows", result.rows_per_pipeline),
        measured(
            "stale_publications_rejected",
            "count",
            result.stale_publications_rejected,
        ),
        measured(
            "temporary_staging_relations",
            "count",
            result.temporary_staging_relations,
        ),
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
            benchmark_class=BenchmarkClass.CONCURRENT_PIPELINES,
            input_rows=result.total_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=config.payload_bytes + 24,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="independent_targets_and_contended_fence",
            concurrency=result.pipeline_count,
            batch_rows=config.batch_rows,
            batch_bytes=config.batch_rows * (config.payload_bytes + 24),
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.total_rows),
            logical_bytes=measured("logical_bytes", "bytes", result.logical_input_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                common._throughput(result.total_rows, result.pipeline_duration_ms),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured(
                "load_duration_ms", "milliseconds", result.pipeline_duration_ms
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
                evidence_reference=f"phase8/bigquery/concurrency/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _create_client(config: BigQueryConcurrencyConfig) -> common._NoRetryClient:
    token = os.environ.get(config.token_env)
    credentials = cast("Any", Credentials)(token=token) if token else None
    client = bigquery.Client(project=config.project, credentials=credentials)
    return common._NoRetryClient(client, location=config.location)


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
        raise BigQueryConcurrencyQualificationError(
            "installed release does not match objective candidate"
        )
    config = BigQueryConcurrencyConfig(
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
    result = _run_concurrency(config, _create_client(config))
    report = _report(config, identity, approval, result)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (BigQueryConcurrencyQualificationError, ValueError, OSError) as error:
        print(f"BigQuery concurrency qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
