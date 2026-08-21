#!/usr/bin/env python3
"""Exact-candidate BigQuery correctness Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import patch

from google.cloud import bigquery
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
from dander.writer import BigQueryScd1Writer, WriteField, WriteTarget
from dander.writer import bigquery as bigquery_writer
from scripts.benchmarks import bigquery_incremental_phase8 as common

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.bigquery-correctness/v1"
_AUTHORITY_ID = "bigquery:phase8-correctness"
_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "exact_normalized_output",
    "fenced_publication",
    "replay_equal",
    "scd1_load_completion",
)
_TIB = Decimal(1024**4)
_MAX_AUTHORIZED_COST_USD = Decimal("0.25")


class BigQueryCorrectnessQualificationError(RuntimeError):
    """Raised with a credential-free BigQuery correctness summary."""


class _MutationJob(Protocol):
    def result(self) -> object:
        """Wait for exactly one submitted provider mutation."""
        ...


@dataclass(frozen=True, slots=True)
class BigQueryCorrectnessConfig:
    """Owned provider coordinates and the accepted correctness fixture."""

    project: str
    dataset: str
    location: str = "US"
    batch_rows: int = 3
    verification_maximum_bytes_billed: int = 64 * 1_024 * 1_024
    on_demand_rate_usd_per_tib: Decimal = Decimal("6.25")
    token_env: str = "DANDER_GCP_ACCESS_TOKEN"

    def __post_init__(self) -> None:
        if not common._valid_project(self.project):
            raise ValueError("project must be a valid Google Cloud project id")
        if not common._valid_identifier(self.dataset):
            raise ValueError("dataset must be a valid BigQuery dataset id")
        if not self.location or any(character.isspace() for character in self.location):
            raise ValueError("location must be a non-empty BigQuery location")
        if self.batch_rows != 3:
            raise ValueError("batch_rows must be exactly 3")
        if (
            isinstance(self.verification_maximum_bytes_billed, bool)
            or not isinstance(self.verification_maximum_bytes_billed, int)
            or self.verification_maximum_bytes_billed <= 0
        ):
            raise ValueError("verification_maximum_bytes_billed must be a positive integer")
        if (
            not isinstance(self.on_demand_rate_usd_per_tib, Decimal)
            or not self.on_demand_rate_usd_per_tib.is_finite()
            or self.on_demand_rate_usd_per_tib <= 0
        ):
            raise ValueError("on_demand_rate_usd_per_tib must be a positive Decimal")
        if not common._valid_identifier(self.token_env):
            raise ValueError("token_env must be a valid environment variable name")

    def workload_payload(self) -> dict[str, object]:
        """Return the exact fixture and bounds covered by objective approval."""
        initial, update, expected = _correctness_fixture()
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.CORRECTNESS.value,
            "initial_rows": len(initial),
            "update_rows": len(update),
            "replay_rows": len(update),
            "expected_output_rows": len(expected),
            "expected_normalized_sha256": _correctness_expected_sha256(),
            "write_mode": "scd1",
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
class _CorrectnessResult:
    duration_ms: int
    peak_rss_bytes: int
    input_rows: int
    output_rows: int
    logical_input_bytes: int
    normalized_sha256: str
    affected_rows: int
    fenced_publications: int
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


class _CorrectnessClient(common._NoRetryClient):
    """Bound every query and retain sanitized query telemetry."""

    def __init__(
        self,
        client: bigquery.Client,
        *,
        location: str,
        maximum_bytes_billed: int,
    ) -> None:
        super().__init__(client, location=location)
        self._maximum_bytes_billed = maximum_bytes_billed
        self.queries: list[str] = []

    def query(
        self,
        query: str,
        *,
        job_config: bigquery.QueryJobConfig | None = None,
    ) -> common._NoRetryJob:
        config = job_config or bigquery.QueryJobConfig()
        config.use_query_cache = False
        if config.maximum_bytes_billed is None:
            config.maximum_bytes_billed = self._maximum_bytes_billed
        self.queries.append(query)
        return super().query(query, job_config=config)


def _load_approval(
    path: Path,
    *,
    config: BigQueryCorrectnessConfig,
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
        raise ValueError("objective approval names do not match BigQuery correctness qualification")
    if objectives.benchmark_class is not BenchmarkClass.CORRECTNESS:
        raise ValueError("objective approval benchmark class is not correctness")
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


def _run_correctness(
    config: BigQueryCorrectnessConfig,
    client: _CorrectnessClient,
) -> _CorrectnessResult:
    dataset_id = f"{config.project}.{config.dataset}"
    if common._dataset_exists(client, dataset_id):
        raise BigQueryCorrectnessQualificationError("owned BigQuery dataset already exists")
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = config.location
    dataset.default_table_expiration_ms = 60 * 60 * 1_000
    dataset.labels = {"dander-owned": "true", "phase8": "bigquery-correctness"}
    started = time.perf_counter()
    peak_before = common._peak_rss_bytes()
    result: _CorrectnessResult | None = None
    failure: Exception | None = None
    try:
        client.create_dataset(dataset)
        with _zero_provider_operation_retries():
            affected_rows, before_replay, after_replay = _exercise_correctness(config, client)
        expected = _correctness_fixture()[2]
        if before_replay != expected or after_replay != expected:
            raise BigQueryCorrectnessQualificationError(
                "BigQuery correctness normalized rows differ from the fixture"
            )
        normalized_sha256 = _normalized_sha256(after_replay)
        if normalized_sha256 != _correctness_expected_sha256():
            raise BigQueryCorrectnessQualificationError(
                "BigQuery correctness normalized hash differs from approval"
            )
        fenced_publications = sum("Dander pipeline lease lost" in query for query in client.queries)
        staging = sum(
            1
            for table in client.list_tables(dataset_id)
            if str(getattr(table, "table_id", "")).startswith("_dander_stage_")
        )
        if staging:
            raise BigQueryCorrectnessQualificationError(
                "BigQuery correctness qualification left temporary staging relations"
            )
        initial, update, _ = _correctness_fixture()
        encoded_input = json.dumps(
            [*initial, *update, *update], separators=(",", ":"), sort_keys=True
        ).encode()
        job_ids = tuple(
            sorted({str(job_id) for job in client.jobs if (job_id := getattr(job, "job_id", None))})
        )
        result = _CorrectnessResult(
            duration_ms=common._elapsed_ms(started),
            peak_rss_bytes=max(peak_before, common._peak_rss_bytes()),
            input_rows=len(initial) + (2 * len(update)),
            output_rows=len(expected),
            logical_input_bytes=len(encoded_input),
            normalized_sha256=normalized_sha256,
            affected_rows=affected_rows,
            fenced_publications=fenced_publications,
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
            raise BigQueryCorrectnessQualificationError(
                "BigQuery dataset cleanup failed"
            ) from error
    cleanup = not common._dataset_exists(client, dataset_id)
    if not cleanup:
        raise BigQueryCorrectnessQualificationError(
            "BigQuery dataset cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, BigQueryCorrectnessQualificationError):
            raise failure
        raise BigQueryCorrectnessQualificationError(
            "BigQuery correctness qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return replace(result, cleanup_verified=True)


def _exercise_correctness(
    config: BigQueryCorrectnessConfig,
    client: _CorrectnessClient,
) -> tuple[
    int,
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
]:
    lease = _create_lease(config, client)
    writer = BigQueryScd1Writer(
        project=config.project,
        client=cast("Any", client),
        max_batch_rows=config.batch_rows,
    )
    target = WriteTarget(
        project=config.project,
        dataset=config.dataset,
        table="scd1_records",
        business_key=("id",),
        schema=(
            WriteField(name="id", data_type="STRING", mode="REQUIRED"),
            WriteField(name="label", data_type="STRING"),
            WriteField(name="sequence", data_type="INT64", mode="REQUIRED"),
        ),
        fence=lease,
    )
    initial, update, _ = _correctness_fixture()
    affected = writer.write(initial, target) + writer.write(update, target)
    before_replay = _read_correctness_rows(config, client)
    affected += writer.write(update, target)
    after_replay = _read_correctness_rows(config, client)
    return affected, before_replay, after_replay


def _create_lease(
    config: BigQueryCorrectnessConfig,
    client: _CorrectnessClient,
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
        "VALUES ('phase8_bigquery_correctness', 'correctness-one', 1, "
        "CURRENT_TIMESTAMP(), TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE))"
    ).result()
    return FencingToken(
        lease_table=table,
        pipeline_id="phase8_bigquery_correctness",
        run_id="correctness-one",
        token=1,
        authority_id=_AUTHORITY_ID,
    )


def _read_correctness_rows(
    config: BigQueryCorrectnessConfig,
    client: _CorrectnessClient,
) -> tuple[dict[str, object], ...]:
    query = (
        "SELECT id, label, sequence "
        f"FROM `{config.project}.{config.dataset}.scd1_records` ORDER BY id"
    )
    values = cast("Iterable[object]", client.query(query).result())
    return tuple(
        {
            "id": str(cast("Any", row)["id"]),
            "label": str(cast("Any", row)["label"]),
            "sequence": int(cast("Any", row)["sequence"]),
        }
        for row in values
    )


@contextmanager
def _zero_provider_operation_retries() -> Iterator[None]:
    with patch.object(bigquery_writer, "run_mutation_with_retry", _run_mutation_once):
        yield


def _run_mutation_once[JobT: _MutationJob](submit: Callable[[], JobT]) -> JobT:
    job = submit()
    job.result()
    return job


def _correctness_fixture() -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
]:
    initial = (
        {"id": "alpha", "label": "older", "sequence": 1},
        {"id": "beta", "label": "second", "sequence": 1},
        {"id": "alpha", "label": "newer", "sequence": 2},
    )
    update = (
        {"id": "alpha", "label": "café", "sequence": 3},
        {"id": "gamma", "label": "third", "sequence": 1},
    )
    expected = (
        {"id": "alpha", "label": "café", "sequence": 3},
        {"id": "beta", "label": "second", "sequence": 1},
        {"id": "gamma", "label": "third", "sequence": 1},
    )
    return initial, update, expected


def _normalized_sha256(rows: tuple[dict[str, object], ...]) -> str:
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _correctness_expected_sha256() -> str:
    return _normalized_sha256(_correctness_fixture()[2])


def _report(
    config: BigQueryCorrectnessConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _CorrectnessResult,
) -> QualificationReport:
    if (
        result.input_rows != 7
        or result.output_rows != 3
        or result.normalized_sha256 != _correctness_expected_sha256()
        or result.affected_rows != 6
        or result.fenced_publications != 3
        or result.provider_operation_retries != 0
        or result.temporary_staging_relations != 0
        or not result.cleanup_verified
    ):
        raise BigQueryCorrectnessQualificationError("BigQuery correctness evidence is incomplete")
    if result.reservation_usage_records:
        raise BigQueryCorrectnessQualificationError(
            "provider jobs used a reservation instead of approved on-demand billing"
        )
    gross_cost = (
        Decimal(result.bytes_billed) * approval.on_demand_rate_usd_per_tib / _TIB
    ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    if gross_cost > approval.cost_ceiling.amount_usd:
        raise BigQueryCorrectnessQualificationError(
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
            benchmark_class=BenchmarkClass.CORRECTNESS,
            input_rows=result.input_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=max(result.logical_input_bytes // result.input_rows, 1),
            schema_depth=1,
            source_rate_limit="unlimited_local_fixture",
            transform_complexity="scd1_replay_normalization",
            concurrency=1,
            batch_rows=config.batch_rows,
            batch_bytes=max(result.logical_input_bytes // result.input_rows, 1) * config.batch_rows,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.output_rows),
            logical_bytes=measured("logical_bytes", "bytes", result.logical_input_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                common._throughput(result.input_rows, result.duration_ms),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured("load_duration_ms", "milliseconds", result.duration_ms),
            transform_duration_ms=measured("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measured("affected_rows", "rows", result.affected_rows),
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
                measured("fenced_publications", "count", result.fenced_publications),
                measured("normalized_output_rows", "rows", result.output_rows),
                measured("provider_operation_retries", "count", result.provider_operation_retries),
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
                evidence_reference=(
                    f"phase8/bigquery/correctness/sha256:{result.normalized_sha256}"
                    if name == "exact_normalized_output"
                    else f"phase8/bigquery/correctness/{name}"
                ),
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _create_client(config: BigQueryCorrectnessConfig) -> _CorrectnessClient:
    token = os.environ.get(config.token_env)
    credentials = cast("Any", Credentials)(token=token) if token else None
    client = bigquery.Client(project=config.project, credentials=credentials)
    return _CorrectnessClient(
        client,
        location=config.location,
        maximum_bytes_billed=config.verification_maximum_bytes_billed,
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
        raise BigQueryCorrectnessQualificationError(
            "installed release does not match objective candidate"
        )
    config = BigQueryCorrectnessConfig(
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
    report = _report(config, identity, approval, _run_correctness(config, _create_client(config)))
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (BigQueryCorrectnessQualificationError, ValueError, OSError) as error:
        print(f"BigQuery correctness qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
