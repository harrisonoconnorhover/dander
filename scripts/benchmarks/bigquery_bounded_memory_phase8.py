#!/usr/bin/env python3
"""Exact-candidate BigQuery bounded-memory Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

from google.cloud import bigquery

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
from dander.writer import BigQueryReplaceWriter, WriteField, WriteTarget
from scripts.benchmarks import bigquery_bulk_phase8 as bulk

_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.bigquery-bounded-memory/v1"
_OBJECTIVES = (
    "bounded_input_ratio",
    "cleanup",
    "cost_ceiling",
    "peak_rss",
    "throughput_measurement",
)
_TIB = Decimal(1024**4)


class BigQueryBoundedMemoryQualificationError(RuntimeError):
    """Raised with a credential-free BigQuery qualification summary."""


@dataclass(frozen=True, slots=True)
class BigQueryBoundedMemoryConfig:
    """Owned provider coordinates and the accepted bounded-memory workload."""

    project: str
    dataset: str
    location: str = "US"
    rows: int = 2_600_000
    payload_bytes: int = 1_024
    batch_rows: int = 10_000
    memory_limit_mib: int = 256
    verification_maximum_bytes_billed: int = 4 * 1_024 * 1_024 * 1_024
    on_demand_rate_usd_per_tib: Decimal = Decimal("6.25")
    token_env: str = "DANDER_GCP_ACCESS_TOKEN"

    def __post_init__(self) -> None:
        if not bulk._valid_project(self.project):
            raise ValueError("project must be a valid Google Cloud project id")
        if not bulk._valid_identifier(self.dataset):
            raise ValueError("dataset must be a valid BigQuery dataset id")
        if not self.location or any(character.isspace() for character in self.location):
            raise ValueError("location must be a non-empty BigQuery location")
        for name in (
            "rows",
            "payload_bytes",
            "batch_rows",
            "memory_limit_mib",
            "verification_maximum_bytes_billed",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.batch_rows > self.rows:
            raise ValueError("batch_rows must not exceed rows")
        if self.batch_rows * (self.payload_bytes + 24) > 256 * 1_024 * 1_024:
            raise ValueError("one batch must not exceed 256 MiB of logical input")
        if (
            not isinstance(self.on_demand_rate_usd_per_tib, Decimal)
            or not self.on_demand_rate_usd_per_tib.is_finite()
            or self.on_demand_rate_usd_per_tib <= 0
        ):
            raise ValueError("on_demand_rate_usd_per_tib must be a positive Decimal")
        if not bulk._valid_identifier(self.token_env):
            raise ValueError("token_env must be a valid environment variable name")

    @property
    def memory_limit_bytes(self) -> int:
        return self.memory_limit_mib * 1_024 * 1_024

    @property
    def logical_input_bytes(self) -> int:
        return self.rows * (self.payload_bytes + 24)

    def workload_payload(self) -> dict[str, object]:
        """Return the exact configuration covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.BOUNDED_MEMORY.value,
            "rows": self.rows,
            "payload_bytes": self.payload_bytes,
            "batch_rows": self.batch_rows,
            "memory_limit_mib": self.memory_limit_mib,
            "minimum_input_to_memory_ratio": 10,
            "maximum_peak_rss_fraction": "0.80",
            "verification_maximum_bytes_billed": self.verification_maximum_bytes_billed,
        }

    def configuration_sha256(self) -> str:
        encoded = json.dumps(
            self.workload_payload(), separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _BoundedMemoryResult:
    duration_ms: int
    peak_rss_bytes: int
    rows: int
    logical_input_bytes: int
    load_jobs: int
    copy_jobs: int
    query_jobs: int
    bytes_processed: int
    bytes_billed: int
    slot_ms: int
    reservation_usage_records: int
    job_ids: tuple[str, ...]
    temporary_staging_relations: int
    cleanup_verified: bool


def _load_approval(
    path: Path,
    *,
    config: BigQueryBoundedMemoryConfig,
    identity: bulk.CandidateIdentity,
) -> bulk._Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective approval schema is incompatible")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective approval workload does not match the requested run")
    configuration = bulk._mapping(payload.get("configuration"), "configuration")
    provider = bulk._mapping(configuration.get("bigquery"), "BigQuery configuration")
    if provider.get("project_sha256") != bulk._identifier_sha256(config.project):
        raise ValueError("objective approval does not match the BigQuery project")
    if provider.get("dataset") != config.dataset or provider.get("location") != config.location:
        raise ValueError("objective approval does not match the owned BigQuery dataset")
    rate = Decimal(str(provider.get("on_demand_rate_usd_per_tib")))
    if rate != config.on_demand_rate_usd_per_tib:
        raise ValueError("objective approval does not match the BigQuery pricing rate")
    execution = bulk._mapping(configuration.get("execution"), "execution configuration")
    if execution.get("harness_sha256") != bulk._file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    dependency_path = Path(bulk.__file__)
    if execution.get("bulk_harness_sha256") != bulk._file_sha256(dependency_path):
        raise ValueError("objective approval does not match the protected BigQuery bulk dependency")
    if execution.get("manual_candidate_executions") != 1:
        raise ValueError("objective approval must allow exactly one candidate execution")
    if execution.get("automatic_candidate_retry") is not False:
        raise ValueError("objective approval must disable automatic candidate retry")
    if execution.get("provider_operation_retries") != 0:
        raise ValueError("objective approval must disable provider-operation retries")
    objective_payload = bulk._mapping(payload.get("approved_objectives"), "approved objectives")
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
        raise ValueError(
            "objective approval names do not match BigQuery bounded-memory qualification"
        )
    if objectives.benchmark_class is not BenchmarkClass.BOUNDED_MEMORY:
        raise ValueError("objective approval benchmark class is not bounded memory")
    if (
        objectives.release_version != identity.release_version
        or objectives.git_commit != identity.git_commit
        or objectives.image_digest != identity.image_digest
        or objectives.approval_reference != identity.approval_reference
        or objectives.configuration_sha256 != config.configuration_sha256()
    ):
        raise ValueError("objective approval does not match the exact candidate")
    cost_payload = bulk._mapping(payload.get("cost_ceiling"), "cost ceiling")
    cost_ceiling = ApprovedCostCeiling(
        amount_usd=Decimal(str(cost_payload.get("amount_usd"))),
        approval_reference=str(cost_payload.get("approval_reference")),
    )
    if cost_ceiling.amount_usd != Decimal("0.25"):
        raise ValueError("cost ceiling must match the established BigQuery per-cell ceiling")
    if cost_ceiling.approval_reference != identity.approval_reference:
        raise ValueError("cost ceiling approval does not match the candidate approval")
    return bulk._Approval(
        objectives=objectives,
        cost_ceiling=cost_ceiling,
        project_sha256=bulk._identifier_sha256(config.project),
        dataset=config.dataset,
        location=config.location,
        on_demand_rate_usd_per_tib=rate,
    )


def _run_bounded_memory(
    config: BigQueryBoundedMemoryConfig,
    client: bulk._NoRetryClient,
) -> _BoundedMemoryResult:
    dataset_id = f"{config.project}.{config.dataset}"
    if bulk._dataset_exists(client, dataset_id):
        raise BigQueryBoundedMemoryQualificationError("owned BigQuery dataset already exists")
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = config.location
    dataset.default_table_expiration_ms = 60 * 60 * 1_000
    dataset.labels = {"dander-owned": "true", "phase8": "bigquery-bounded-memory"}
    started = time.perf_counter()
    peak_before = bulk._peak_rss_bytes()
    result: _BoundedMemoryResult | None = None
    failure: Exception | None = None
    try:
        client.create_dataset(dataset)
        writer = BigQueryReplaceWriter(
            project=config.project,
            client=cast("Any", client),
            max_batch_rows=config.batch_rows,
        )
        target = WriteTarget(
            project=config.project,
            dataset=config.dataset,
            table="bounded_records",
            schema=(
                WriteField(name="id", data_type="STRING", mode="REQUIRED"),
                WriteField(name="payload", data_type="STRING", mode="REQUIRED"),
            ),
        )
        affected = writer.write(bulk._records(config.rows, config.payload_bytes), target)
        if affected != config.rows:
            raise BigQueryBoundedMemoryQualificationError(
                "BigQuery bounded-memory write returned an unexpected row count"
            )
        bulk._require_table_shape(
            client,
            config=cast("Any", config),
            table="bounded_records",
            rows=config.rows,
            payload_bytes=config.payload_bytes,
        )
        staging = sum(
            1
            for table in client.list_tables(dataset_id)
            if str(getattr(table, "table_id", "")).startswith("_dander_stage_")
        )
        if staging:
            raise BigQueryBoundedMemoryQualificationError(
                "BigQuery bounded-memory qualification left temporary staging relations"
            )
        job_ids = tuple(
            sorted({str(job_id) for job in client.jobs if (job_id := getattr(job, "job_id", None))})
        )
        result = _BoundedMemoryResult(
            duration_ms=bulk._elapsed_ms(started),
            peak_rss_bytes=max(peak_before, bulk._peak_rss_bytes()),
            rows=affected,
            logical_input_bytes=config.logical_input_bytes,
            load_jobs=bulk._job_count(client.jobs, "load"),
            copy_jobs=bulk._job_count(client.jobs, "copy"),
            query_jobs=bulk._job_count(client.jobs, "query"),
            bytes_processed=bulk._job_total(client.jobs, "total_bytes_processed"),
            bytes_billed=bulk._job_total(client.jobs, "total_bytes_billed"),
            slot_ms=bulk._job_total(client.jobs, "slot_millis"),
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
            raise BigQueryBoundedMemoryQualificationError(
                "BigQuery dataset cleanup failed"
            ) from error
    cleanup = not bulk._dataset_exists(client, dataset_id)
    if not cleanup:
        raise BigQueryBoundedMemoryQualificationError(
            "BigQuery dataset cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, BigQueryBoundedMemoryQualificationError):
            raise failure
        raise BigQueryBoundedMemoryQualificationError(
            "BigQuery bounded-memory qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return replace(result, cleanup_verified=True)


def _report(
    config: BigQueryBoundedMemoryConfig,
    identity: bulk.CandidateIdentity,
    approval: bulk._Approval,
    result: _BoundedMemoryResult,
) -> QualificationReport:
    if result.rows != config.rows or result.logical_input_bytes != config.logical_input_bytes:
        raise BigQueryBoundedMemoryQualificationError("bounded-memory result differs from approval")
    if result.logical_input_bytes < config.memory_limit_bytes * 10:
        raise BigQueryBoundedMemoryQualificationError(
            "logical input is less than ten times the container memory limit"
        )
    if result.peak_rss_bytes * 5 > config.memory_limit_bytes * 4:
        raise BigQueryBoundedMemoryQualificationError(
            "peak RSS exceeds eighty percent of the container memory limit"
        )
    expected_load_jobs = math.ceil(config.rows / config.batch_rows)
    if result.load_jobs != expected_load_jobs or result.copy_jobs != 1 or result.query_jobs != 1:
        raise BigQueryBoundedMemoryQualificationError("provider job counts differ from approval")
    if result.reservation_usage_records:
        raise BigQueryBoundedMemoryQualificationError(
            "provider jobs used a reservation instead of approved on-demand billing"
        )
    gross_cost = (
        Decimal(result.bytes_billed) * approval.on_demand_rate_usd_per_tib / _TIB
    ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    if gross_cost > approval.cost_ceiling.amount_usd:
        raise BigQueryBoundedMemoryQualificationError(
            "provider-metered cost exceeds its approved ceiling"
        )
    measured = PerformanceMeasurement.measured
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
            benchmark_class=BenchmarkClass.BOUNDED_MEMORY,
            input_rows=result.rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=config.payload_bytes + 24,
            schema_depth=1,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="none",
            concurrency=1,
            batch_rows=config.batch_rows,
            batch_bytes=config.batch_rows * (config.payload_bytes + 24),
            memory_limit_bytes=config.memory_limit_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.rows),
            logical_bytes=measured("logical_bytes", "bytes", result.logical_input_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                bulk._throughput(result.rows, result.duration_ms),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured("load_duration_ms", "milliseconds", result.duration_ms),
            transform_duration_ms=measured("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measured("bigquery_bytes_billed", "bytes", result.bytes_billed),
                measured("bigquery_bytes_processed", "bytes", result.bytes_processed),
                measured("bigquery_copy_jobs", "count", result.copy_jobs),
                measured("bigquery_load_jobs", "count", result.load_jobs),
                measured("bigquery_provider_operation_retries", "count", 0),
                measured("bigquery_query_jobs", "count", result.query_jobs),
                measured(
                    "bigquery_reservation_usage_records",
                    "count",
                    result.reservation_usage_records,
                ),
                measured("bigquery_slot_ms", "milliseconds", result.slot_ms),
                measured("memory_limit_bytes", "bytes", config.memory_limit_bytes),
                measured(
                    "temporary_staging_relations",
                    "count",
                    result.temporary_staging_relations,
                ),
            ),
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
                evidence_reference=f"phase8/bigquery/bounded-memory/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _container_memory_limit_bytes() -> int | None:
    paths = (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    )
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _require_container_memory_limit(config: BigQueryBoundedMemoryConfig) -> None:
    if _container_memory_limit_bytes() != config.memory_limit_bytes:
        raise BigQueryBoundedMemoryQualificationError(
            "container memory limit does not match the approved bounded-memory objective"
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
        raise BigQueryBoundedMemoryQualificationError(
            "installed release does not match objective candidate"
        )
    config = BigQueryBoundedMemoryConfig(
        project=arguments.project,
        dataset=arguments.dataset,
        location=arguments.location,
    )
    _require_container_memory_limit(config)
    identity = bulk.CandidateIdentity(
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
    client = bulk._create_client(cast("Any", config))
    result = _run_bounded_memory(config, client)
    report = _report(config, identity, approval, result)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (BigQueryBoundedMemoryQualificationError, ValueError, OSError) as error:
        print(f"BigQuery bounded-memory qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
