#!/usr/bin/env python3
"""Exact-RC30 BigQuery failure-path Phase 8 qualification."""

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

from google.api_core.exceptions import BadRequest, Unauthorized
from google.auth.exceptions import RefreshError
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
from dander.writer import BigQueryReplaceWriter, WriteField, WriteTarget
from scripts.benchmarks import bigquery_bulk_phase8 as bulk

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.bigquery-failure/v1"
_AUTHORITY_ID = "bigquery:phase8-failure"
_OBJECTIVES = (
    "cleanup",
    "cost_ceiling",
    "credential_rejection",
    "failed_load_cleanup",
    "provider_operation_recovery",
    "stale_publication_rejection",
)
_TIB = Decimal(1024**4)
_MAX_AUTHORIZED_COST_USD = Decimal("0.25")


class BigQueryFailureQualificationError(RuntimeError):
    """Raised with a credential-free BigQuery failure-path summary."""


class _MutationJob(Protocol):
    def result(self) -> object:
        """Wait for exactly one submitted mutation."""
        ...


class _FailureClient(Protocol):
    @property
    def jobs(self) -> Sequence[object]: ...

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

    def copy_table(
        self,
        sources: str,
        destination: str,
        *,
        job_config: bigquery.CopyJobConfig,
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
class BigQueryFailureConfig:
    """Owned provider coordinates and the accepted bounded failure probes."""

    project: str
    dataset: str
    location: str = "US"
    verification_maximum_bytes_billed: int = 64 * 1_024 * 1_024
    on_demand_rate_usd_per_tib: Decimal = Decimal("6.25")
    token_env: str = "DANDER_GCP_ACCESS_TOKEN"

    def __post_init__(self) -> None:
        if not bulk._valid_project(self.project):
            raise ValueError("project must be a valid Google Cloud project id")
        if not bulk._valid_identifier(self.dataset):
            raise ValueError("dataset must be a valid BigQuery dataset id")
        if not self.location or any(character.isspace() for character in self.location):
            raise ValueError("location must be a non-empty BigQuery location")
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
        if not bulk._valid_identifier(self.token_env):
            raise ValueError("token_env must be a valid environment variable name")

    def workload_payload(self) -> dict[str, object]:
        """Return the exact failure probes covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.FAILURE.value,
            "probes": [
                "credential_rejection",
                "failed_load_cleanup",
                "provider_operation_recovery",
                "stale_publication_rejection",
            ],
            "verification_maximum_bytes_billed": self.verification_maximum_bytes_billed,
        }

    def configuration_sha256(self) -> str:
        encoded = json.dumps(
            self.workload_payload(), separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _FailureResult:
    duration_ms: int
    peak_rss_bytes: int
    probe_count: int
    credential_rejection_duration_ms: int
    failed_load_cleanup_duration_ms: int
    provider_operation_recovery_duration_ms: int
    stale_publications_rejected: int
    load_jobs: int
    copy_jobs: int
    query_jobs: int
    provider_job_errors: int
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
    config: BigQueryFailureConfig,
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
        raise ValueError("objective approval does not match the protected BigQuery dependency")
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
        raise ValueError("objective approval names do not match BigQuery failure qualification")
    if objectives.benchmark_class is not BenchmarkClass.FAILURE:
        raise ValueError("objective approval benchmark class is not failure")
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
    if cost_ceiling.amount_usd != _MAX_AUTHORIZED_COST_USD:
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


def _run_failure(
    config: BigQueryFailureConfig,
    client: _FailureClient,
    *,
    credential_probe: Callable[[BigQueryFailureConfig], int] | None = None,
) -> _FailureResult:
    dataset_id = f"{config.project}.{config.dataset}"
    if bulk._dataset_exists(cast("bulk._NoRetryClient", client), dataset_id):
        raise BigQueryFailureQualificationError("owned BigQuery dataset already exists")
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = config.location
    dataset.default_table_expiration_ms = 60 * 60 * 1_000
    dataset.labels = {"dander-owned": "true", "phase8": "bigquery-failure"}
    started = time.perf_counter()
    peak_before = bulk._peak_rss_bytes()
    result: _FailureResult | None = None
    failure: Exception | None = None
    try:
        rejection_ms = (credential_probe or _probe_credential_rejection)(config)
        client.create_dataset(dataset)
        failed_load_ms, recovery_ms = _probe_failed_load_cleanup_and_recovery(config, client)
        with _zero_provider_operation_retries():
            stale_rejected = _probe_stale_publication_rejection(config, client)
        staging = _staging_relations(client, dataset_id)
        if staging:
            raise BigQueryFailureQualificationError(
                "BigQuery failure qualification left temporary staging relations"
            )
        job_ids = tuple(
            sorted({str(job_id) for job in client.jobs if (job_id := getattr(job, "job_id", None))})
        )
        result = _FailureResult(
            duration_ms=bulk._elapsed_ms(started),
            peak_rss_bytes=max(peak_before, bulk._peak_rss_bytes()),
            probe_count=4,
            credential_rejection_duration_ms=rejection_ms,
            failed_load_cleanup_duration_ms=failed_load_ms,
            provider_operation_recovery_duration_ms=recovery_ms,
            stale_publications_rejected=1 if stale_rejected else 0,
            load_jobs=bulk._job_count(client.jobs, "load"),
            copy_jobs=bulk._job_count(client.jobs, "copy"),
            query_jobs=bulk._job_count(client.jobs, "query"),
            provider_job_errors=1 + int(stale_rejected),
            bytes_processed=bulk._job_total(client.jobs, "total_bytes_processed"),
            bytes_billed=bulk._job_total(client.jobs, "total_bytes_billed"),
            slot_ms=bulk._job_total(client.jobs, "slot_millis"),
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
            raise BigQueryFailureQualificationError("BigQuery dataset cleanup failed") from error
    cleanup = not bulk._dataset_exists(cast("bulk._NoRetryClient", client), dataset_id)
    if not cleanup:
        raise BigQueryFailureQualificationError("BigQuery dataset cleanup could not be verified")
    if failure is not None:
        if isinstance(failure, BigQueryFailureQualificationError):
            raise failure
        raise BigQueryFailureQualificationError(
            "BigQuery failure qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return replace(result, cleanup_verified=True)


def _probe_credential_rejection(config: BigQueryFailureConfig) -> int:
    valid_token = os.environ.get(config.token_env)
    if not valid_token:
        raise BigQueryFailureQualificationError(
            "BigQuery failure qualification requires its projected access token"
        )
    rejected_token = hashlib.sha256(valid_token.encode()).hexdigest()
    started = time.perf_counter()
    client = bigquery.Client(
        project=config.project,
        credentials=Credentials(token=rejected_token),  # type: ignore[no-untyped-call]
    )
    try:
        try:
            job = client.query(
                "SELECT 1",
                location=config.location,
                retry=bulk._NO_RETRY,
                job_retry=bulk._NO_RETRY,
            )
            job.result(retry=None, job_retry=None)
        except (Unauthorized, RefreshError):
            return bulk._elapsed_ms(started)
    finally:
        client.close()  # type: ignore[no-untyped-call]
    raise BigQueryFailureQualificationError(
        "BigQuery failure qualification accepted a rejected credential"
    )


def _probe_failed_load_cleanup_and_recovery(
    config: BigQueryFailureConfig,
    client: _FailureClient,
) -> tuple[int, int]:
    writer = BigQueryReplaceWriter(
        project=config.project,
        client=cast("Any", client),
        max_batch_rows=1,
    )
    target = WriteTarget(
        project=config.project,
        dataset=config.dataset,
        table="failure_records",
        schema=(
            WriteField(name="id", data_type="INT64", mode="REQUIRED"),
            WriteField(name="value", data_type="INT64", mode="REQUIRED"),
        ),
    )
    failed_started = time.perf_counter()
    try:
        writer.write(({"id": 1, "value": "not-an-integer"},), target)
    except BadRequest:
        failed_ms = bulk._elapsed_ms(failed_started)
    else:
        raise BigQueryFailureQualificationError(
            "BigQuery failure qualification accepted an invalid load"
        )
    if _staging_relations(client, f"{config.project}.{config.dataset}"):
        raise BigQueryFailureQualificationError(
            "BigQuery failed load left a temporary staging relation"
        )
    recovery_started = time.perf_counter()
    affected = writer.write(({"id": 1, "value": 1},), target)
    _require_exact_target(config, client)
    if affected != 1:
        raise BigQueryFailureQualificationError(
            "BigQuery provider operation did not recover after the failed load"
        )
    return failed_ms, bulk._elapsed_ms(recovery_started)


def _probe_stale_publication_rejection(
    config: BigQueryFailureConfig,
    client: _FailureClient,
) -> bool:
    fence = BigQueryTargetFence(config.project, client=cast("Any", client))
    relation = RelationRef(
        catalog=config.project,
        namespace=config.dataset,
        name="failure_records",
    )
    old_token = FencingToken(
        lease_table=None,
        pipeline_id="phase8_bigquery_failure",
        run_id="failure-old",
        token=1,
        authority_id=_AUTHORITY_ID,
    )
    old_publication = fence.claim(relation, old_token)
    fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=old_token.pipeline_id,
            run_id="failure-new",
            token=2,
            authority_id=old_token.authority_id,
        ),
    )
    prepared = fence.prepare_dml(
        f"UPDATE `{config.project}.{config.dataset}.failure_records` SET value = 2 WHERE id = 1",
        old_publication,
    )
    try:
        client.query(prepared.sql, job_config=cast("Any", prepared.options)).result()
    except BadRequest as error:
        if "Dander destination fence lost" not in str(error):
            raise BigQueryFailureQualificationError(
                "BigQuery stale publication failed for an unexpected provider reason"
            ) from error
    else:
        raise BigQueryFailureQualificationError(
            "BigQuery failure qualification accepted a stale publication"
        )
    _require_exact_target(config, client)
    return True


@contextmanager
def _zero_provider_operation_retries() -> Iterator[None]:
    with patch.object(bigquery_fence, "run_mutation_with_retry", _run_mutation_once):
        yield


def _run_mutation_once[JobT: _MutationJob](submit: Callable[[], JobT]) -> JobT:
    job = submit()
    job.result()
    return job


def _require_exact_target(config: BigQueryFailureConfig, client: _FailureClient) -> None:
    query = (
        "SELECT COUNT(*) AS row_count, "
        "COUNTIF(id = 1 AND value = 1) AS unchanged_rows "
        f"FROM `{config.project}.{config.dataset}.failure_records`"
    )
    query_config = bigquery.QueryJobConfig(
        use_query_cache=False,
        maximum_bytes_billed=config.verification_maximum_bytes_billed,
    )
    rows = list(cast("Iterable[object]", client.query(query, job_config=query_config).result()))
    if len(rows) != 1:
        raise BigQueryFailureQualificationError("BigQuery failure readback was malformed")
    row = cast("Mapping[str, object]", rows[0])
    if int(cast("Any", row["row_count"])) != 1 or int(cast("Any", row["unchanged_rows"])) != 1:
        raise BigQueryFailureQualificationError(
            "BigQuery failure probe changed the protected target"
        )


def _staging_relations(client: _FailureClient, dataset_id: str) -> int:
    return sum(
        1
        for table in client.list_tables(dataset_id)
        if str(getattr(table, "table_id", "")).startswith("_dander_stage_")
    )


def _report(
    config: BigQueryFailureConfig,
    identity: bulk.CandidateIdentity,
    approval: bulk._Approval,
    result: _FailureResult,
) -> QualificationReport:
    if result.probe_count != 4 or result.stale_publications_rejected != 1:
        raise BigQueryFailureQualificationError("failure result differs from approval")
    if result.load_jobs != 2 or result.copy_jobs != 1 or result.query_jobs != 7:
        raise BigQueryFailureQualificationError("provider job counts differ from approval")
    if result.provider_job_errors != 2:
        raise BigQueryFailureQualificationError("provider failure job count differs from approval")
    if result.provider_operation_retries or result.reservation_usage_records:
        raise BigQueryFailureQualificationError(
            "provider retries or reservation usage violated the objective"
        )
    if result.temporary_staging_relations or not result.cleanup_verified:
        raise BigQueryFailureQualificationError("failure cleanup is incomplete")
    gross_cost = (
        Decimal(result.bytes_billed) * approval.on_demand_rate_usd_per_tib / _TIB
    ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    if gross_cost > approval.cost_ceiling.amount_usd:
        raise BigQueryFailureQualificationError(
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
            benchmark_class=BenchmarkClass.FAILURE,
            input_rows=result.probe_count,
            logical_input_bytes=result.probe_count,
            row_width_bytes=1,
            schema_depth=1,
            source_rate_limit="controlled_provider_failure_injection",
            transform_complexity="credential_load_recovery_and_fencing",
            concurrency=1,
            batch_rows=1,
            batch_bytes=1,
            memory_limit_bytes=None,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.probe_count),
            logical_bytes=measured("logical_bytes", "bytes", result.probe_count),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                bulk._throughput(result.probe_count, result.duration_ms),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured("load_duration_ms", "milliseconds", 0),
            transform_duration_ms=measured("transform_duration_ms", "milliseconds", 0),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measured("bigquery_bytes_billed", "bytes", result.bytes_billed),
                measured("bigquery_bytes_processed", "bytes", result.bytes_processed),
                measured("bigquery_copy_jobs", "count", result.copy_jobs),
                measured("bigquery_failed_jobs", "count", result.provider_job_errors),
                measured("bigquery_load_jobs", "count", result.load_jobs),
                measured(
                    "bigquery_provider_operation_retries",
                    "count",
                    result.provider_operation_retries,
                ),
                measured("bigquery_query_jobs", "count", result.query_jobs),
                measured(
                    "bigquery_reservation_usage_records",
                    "count",
                    result.reservation_usage_records,
                ),
                measured("bigquery_slot_ms", "milliseconds", result.slot_ms),
                measured(
                    "credential_rejection_duration_ms",
                    "milliseconds",
                    result.credential_rejection_duration_ms,
                ),
                measured(
                    "failed_load_cleanup_duration_ms",
                    "milliseconds",
                    result.failed_load_cleanup_duration_ms,
                ),
                measured("probe_count", "count", result.probe_count),
                measured(
                    "provider_operation_recovery_duration_ms",
                    "milliseconds",
                    result.provider_operation_recovery_duration_ms,
                ),
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
                evidence_reference=f"phase8/bigquery/failure/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
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
        raise BigQueryFailureQualificationError(
            "installed release does not match objective candidate"
        )
    config = BigQueryFailureConfig(
        project=arguments.project,
        dataset=arguments.dataset,
        location=arguments.location,
    )
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
    result = _run_failure(config, client)
    report = _report(config, identity, approval, result)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (BigQueryFailureQualificationError, ValueError, OSError) as error:
        print(f"BigQuery failure qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
