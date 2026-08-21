#!/usr/bin/env python3
"""Exact-RC30 BigQuery transform Phase 8 qualification."""

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
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast
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
from dander.transform import BigQueryTransformRunner
from dander.transform import runner as bigquery_transform
from dander.writer import BigQueryScd1Writer, SchemaEvolution, WriteField, WriteTarget
from dander.writer import bigquery as bigquery_writer
from scripts.benchmarks import bigquery_incremental_phase8 as common

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.bigquery-transform/v1"
_AUTHORITY_ID = "bigquery:phase8-transform"
_OBJECTIVES = (
    "aggregation_exact",
    "cleanup",
    "cost_ceiling",
    "generic_tests",
    "incremental_merge",
    "join_exact",
    "scan_exact",
)
_TIB = Decimal(1024**4)
_MAX_AUTHORIZED_COST_USD = Decimal("0.25")
_EXPECTED_ASSERTIONS = 21
_EXPECTED_MODELS = 4
_EXPECTED_FENCED_PUBLICATIONS = 4


class BigQueryTransformQualificationError(RuntimeError):
    """Raised with a credential-free transform qualification summary."""


@dataclass(frozen=True, slots=True)
class BigQueryTransformConfig:
    """Owned provider coordinates and the accepted transform workload."""

    project: str
    dataset: str
    location: str = "US"
    fact_rows: int = 100_000
    dimension_rows: int = 100
    delta_rows: int = 2
    batch_rows: int = 10_000
    verification_maximum_bytes_billed: int = 512 * 1_024 * 1_024
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
            "fact_rows",
            "dimension_rows",
            "delta_rows",
            "batch_rows",
            "verification_maximum_bytes_billed",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.fact_rows != 100_000:
            raise ValueError("fact_rows must be exactly 100000")
        if self.dimension_rows != 100:
            raise ValueError("dimension_rows must be exactly 100")
        if self.delta_rows != 2:
            raise ValueError("delta_rows must be exactly 2")
        if self.batch_rows > self.fact_rows:
            raise ValueError("batch_rows must not exceed fact_rows")
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
            "benchmark_class": BenchmarkClass.TRANSFORM.value,
            "fact_rows": self.fact_rows,
            "dimension_rows": self.dimension_rows,
            "delta_rows": self.delta_rows,
            "models": ["scan", "join", "aggregation", "incremental_merge"],
            "generic_tests": ["accepted_values", "not_null", "unique"],
            "generic_assertions": _EXPECTED_ASSERTIONS,
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


@dataclass(slots=True)
class _Ownership:
    fence: FencingToken
    verifications: int = 0

    def verify(self) -> None:
        self.verifications += 1


@dataclass(frozen=True, slots=True)
class _TransformResult:
    duration_ms: int
    load_duration_ms: int
    transform_duration_ms: int
    peak_rss_bytes: int
    input_rows: int
    output_rows: int
    logical_input_bytes: int
    model_count: int
    assertion_count: int
    ownership_verifications: int
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


class _TransformClient(common._NoRetryClient):
    """Apply a per-query billing bound while retaining the shared no-retry client."""

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
    config: BigQueryTransformConfig,
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
        raise ValueError("objective approval names do not match BigQuery transform qualification")
    if objectives.benchmark_class is not BenchmarkClass.TRANSFORM:
        raise ValueError("objective approval benchmark class is not transform")
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


def _run_transform(
    config: BigQueryTransformConfig,
    client: _TransformClient,
) -> _TransformResult:
    dataset_id = f"{config.project}.{config.dataset}"
    if common._dataset_exists(client, dataset_id):
        raise BigQueryTransformQualificationError("owned BigQuery dataset already exists")
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = config.location
    dataset.default_table_expiration_ms = 60 * 60 * 1_000
    dataset.labels = {"dander-owned": "true", "phase8": "bigquery-transform"}
    started = time.perf_counter()
    peak_before = common._peak_rss_bytes()
    result: _TransformResult | None = None
    failure: Exception | None = None
    try:
        client.create_dataset(dataset)
        lease = _create_lease(config, client)
        ownership = _Ownership(lease)
        with _zero_provider_operation_retries():
            load_started = time.perf_counter()
            _seed_sources(config, client, lease)
            load_duration_ms = common._elapsed_ms(load_started)
            transform_started = time.perf_counter()
            with TemporaryDirectory(prefix="dander-phase8-bigquery-transform-") as temporary:
                models = Path(temporary)
                _write_transform_models(models, target_dataset=config.dataset)
                runner = BigQueryTransformRunner(
                    project=config.project,
                    raw_namespace=config.dataset,
                    client=cast("Any", client),
                )
                initial = runner.build(
                    models,
                    selected=("aggregate_records", "incremental_records"),
                    ownership=ownership,
                )
                _require_initial_output(config, client)
                _mutate_sources(config, client)
                replay = runner.build(
                    models,
                    selected=("incremental_records",),
                    ownership=ownership,
                )
                _require_incremental_output(config, client)
                tested = runner.test(
                    models,
                    selected=("aggregate_records", "incremental_records"),
                )
            transform_duration_ms = common._elapsed_ms(transform_started)
        assertion_count = initial.assertions + replay.assertions + tested.assertions
        model_count = len(set((*initial.models, *replay.models, *tested.models)))
        fenced_publications = sum(
            "ASSERT @@row_count = 1 AS 'Dander pipeline lease lost'" in query
            for query in client.queries
        )
        staging = sum(
            1
            for table in client.list_tables(dataset_id)
            if str(getattr(table, "table_id", "")).startswith("_dander_stage_")
        )
        job_ids = tuple(
            sorted({str(job_id) for job in client.jobs if (job_id := getattr(job, "job_id", None))})
        )
        result = _TransformResult(
            duration_ms=common._elapsed_ms(started),
            load_duration_ms=load_duration_ms,
            transform_duration_ms=transform_duration_ms,
            peak_rss_bytes=max(peak_before, common._peak_rss_bytes()),
            input_rows=config.fact_rows + config.dimension_rows,
            output_rows=config.fact_rows + 1,
            logical_input_bytes=(config.fact_rows * 32) + (config.dimension_rows * 24),
            model_count=model_count,
            assertion_count=assertion_count,
            ownership_verifications=ownership.verifications,
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
            raise BigQueryTransformQualificationError("BigQuery dataset cleanup failed") from error
    cleanup = not common._dataset_exists(client, dataset_id)
    if not cleanup:
        raise BigQueryTransformQualificationError("BigQuery dataset cleanup could not be verified")
    if failure is not None:
        if isinstance(failure, BigQueryTransformQualificationError):
            raise failure
        raise BigQueryTransformQualificationError(
            "BigQuery transform qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return replace(result, cleanup_verified=True)


def _create_lease(
    config: BigQueryTransformConfig,
    client: _TransformClient,
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
        "VALUES ('phase8_bigquery_transform', 'transform-one', 1, "
        "CURRENT_TIMESTAMP(), TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE))"
    ).result()
    return FencingToken(
        lease_table=table,
        pipeline_id="phase8_bigquery_transform",
        run_id="transform-one",
        token=1,
        authority_id=_AUTHORITY_ID,
    )


def _seed_sources(
    config: BigQueryTransformConfig,
    client: _TransformClient,
    lease: FencingToken,
) -> None:
    writer = BigQueryScd1Writer(
        project=config.project,
        client=cast("Any", client),
        max_batch_rows=config.batch_rows,
        schema_evolution=SchemaEvolution.STRICT,
    )
    dimensions = writer.write(
        _dimension_records(config),
        WriteTarget(
            project=config.project,
            dataset=config.dataset,
            table="dimensions",
            business_key=("dimension_id",),
            schema=(
                WriteField(name="dimension_id", data_type="INT64", mode="REQUIRED"),
                WriteField(name="category", data_type="STRING", mode="REQUIRED"),
            ),
            fence=lease,
        ),
    )
    facts = writer.write(
        _fact_records(config),
        WriteTarget(
            project=config.project,
            dataset=config.dataset,
            table="facts",
            business_key=("id",),
            schema=(
                WriteField(name="id", data_type="INT64", mode="REQUIRED"),
                WriteField(name="dimension_id", data_type="INT64", mode="REQUIRED"),
                WriteField(name="amount", data_type="INT64", mode="REQUIRED"),
                WriteField(name="updated_at", data_type="INT64", mode="REQUIRED"),
            ),
            fence=lease,
        ),
    )
    if dimensions != config.dimension_rows or facts != config.fact_rows:
        raise BigQueryTransformQualificationError(
            "BigQuery transform source seed affected an unexpected row count"
        )


def _dimension_records(config: BigQueryTransformConfig) -> Iterable[dict[str, object]]:
    for index in range(config.dimension_rows):
        yield {"dimension_id": index, "category": f"category_{index % 10}"}


def _fact_records(config: BigQueryTransformConfig) -> Iterable[dict[str, object]]:
    for index in range(1, config.fact_rows + 1):
        yield {
            "id": index,
            "dimension_id": index % config.dimension_rows,
            "amount": index % 17,
            "updated_at": 1,
        }


def _write_transform_models(root: Path, *, target_dataset: str) -> None:
    models = {
        "scan_records": (
            "SELECT id, dimension_id, amount, updated_at FROM {{ ref('raw_facts') }}",
            "table",
            (
                ("id", "INT64"),
                ("dimension_id", "INT64"),
                ("amount", "INT64"),
                ("updated_at", "INT64"),
            ),
            "  - column: id\n    not_null: true\n    unique: true\n",
            "",
        ),
        "joined_records": (
            "SELECT facts.id, dimensions.category, facts.amount, facts.updated_at "
            "FROM {{ ref('scan_records') }} AS facts "
            "JOIN {{ ref('raw_dimensions') }} AS dimensions "
            "ON facts.dimension_id = dimensions.dimension_id",
            "table",
            (("id", "INT64"), ("category", "STRING"), ("amount", "INT64"), ("updated_at", "INT64")),
            "  - column: category\n"
            "    accepted_values: [category_0, category_1, category_2, category_3, "
            "category_4, category_5, category_6, category_7, category_8, category_9]\n",
            "",
        ),
        "aggregate_records": (
            "SELECT category, SUM(amount) AS total_amount, COUNT(*) AS row_count "
            "FROM {{ ref('joined_records') }} GROUP BY category",
            "table",
            (("category", "STRING"), ("total_amount", "INT64"), ("row_count", "INT64")),
            "  - column: category\n    not_null: true\n    unique: true\n"
            "  - column: row_count\n    not_null: true\n",
            "",
        ),
        "incremental_records": (
            "SELECT id, category, amount, updated_at FROM {{ ref('joined_records') }}",
            "incremental",
            (("id", "INT64"), ("category", "STRING"), ("amount", "INT64"), ("updated_at", "INT64")),
            "  - column: id\n    not_null: true\n    unique: true\n",
            "unique_key: [id]\nincremental_cursor: updated_at\n",
        ),
    }
    for name, (query, materialization, columns, tests, incremental) in models.items():
        (root / f"{name}.sql").write_text(query, encoding="utf-8")
        column_yaml = "".join(
            f"  - name: {column}\n    type: {data_type}\n    description: Phase 8 {column}.\n"
            for column, data_type in columns
        )
        (root / f"{name}.yml").write_text(
            f"model: {name}\n"
            "description: Phase 8 BigQuery transform qualification.\n"
            "owner: data-eng\n"
            "dialect: portable\n"
            f"materialization: {materialization}\n"
            f"dataset: {target_dataset}\n"
            "source_system: phase8_fixture\n"
            "sensitivity: public\n"
            f"{incremental}"
            f"columns:\n{column_yaml}"
            f"tests:\n{tests}",
            encoding="utf-8",
        )


def _require_initial_output(
    config: BigQueryTransformConfig,
    client: _TransformClient,
) -> None:
    prefix = f"`{config.project}.{config.dataset}"
    scan = _one_row(
        client,
        "SELECT COUNT(*) AS row_count, COUNT(DISTINCT id) AS distinct_ids, "
        "MIN(id) AS minimum_id, MAX(id) AS maximum_id, SUM(amount) AS total_amount "
        f"FROM {prefix}.scan_records`",
    )
    total_amount = sum(value % 17 for value in range(1, config.fact_rows + 1))
    if _integer_values(
        scan, "row_count", "distinct_ids", "minimum_id", "maximum_id", "total_amount"
    ) != (config.fact_rows, config.fact_rows, 1, config.fact_rows, total_amount):
        raise BigQueryTransformQualificationError(
            "BigQuery transform scan produced unexpected normalized output"
        )
    joined = _one_row(
        client,
        "SELECT COUNT(*) AS row_count, COUNT(DISTINCT id) AS distinct_ids, "
        "COUNT(DISTINCT category) AS category_count, SUM(amount) AS total_amount "
        f"FROM {prefix}.joined_records`",
    )
    if _integer_values(joined, "row_count", "distinct_ids", "category_count", "total_amount") != (
        config.fact_rows,
        config.fact_rows,
        10,
        total_amount,
    ):
        raise BigQueryTransformQualificationError(
            "BigQuery transform join produced unexpected normalized output"
        )
    aggregate_rows = _query_rows(
        client,
        f"SELECT category, total_amount, row_count FROM {prefix}.aggregate_records` "
        "ORDER BY category",
    )
    observed = tuple(
        (
            str(cast("Any", row)["category"]),
            int(cast("Any", row)["total_amount"]),
            int(cast("Any", row)["row_count"]),
        )
        for row in aggregate_rows
    )
    expected = _expected_aggregates(config)
    if observed != expected:
        raise BigQueryTransformQualificationError(
            "BigQuery transform aggregation produced unexpected normalized output"
        )
    incremental = _one_row(
        client,
        "SELECT COUNT(*) AS row_count, COUNT(DISTINCT id) AS distinct_ids, "
        f"SUM(amount) AS total_amount FROM {prefix}.incremental_records`",
    )
    if _integer_values(incremental, "row_count", "distinct_ids", "total_amount") != (
        config.fact_rows,
        config.fact_rows,
        total_amount,
    ):
        raise BigQueryTransformQualificationError(
            "BigQuery transform incremental seed produced unexpected normalized output"
        )


def _mutate_sources(config: BigQueryTransformConfig, client: _TransformClient) -> None:
    facts = f"`{config.project}.{config.dataset}.facts`"
    client.query(
        f"UPDATE {facts} SET amount = 999, updated_at = 2 WHERE id = 1;\n"
        f"INSERT INTO {facts} (id, dimension_id, amount, updated_at) "
        f"VALUES ({config.fact_rows + 1}, 1, 5, 2);"
    ).result()


def _require_incremental_output(
    config: BigQueryTransformConfig,
    client: _TransformClient,
) -> None:
    table = f"`{config.project}.{config.dataset}.incremental_records`"
    row = _one_row(
        client,
        "SELECT COUNT(*) AS row_count, COUNT(DISTINCT id) AS distinct_ids, "
        "COUNTIF(id = 1 AND amount = 999 AND updated_at = 2) AS updated_rows, "
        f"COUNTIF(id = {config.fact_rows + 1} AND amount = 5 AND updated_at = 2) "
        f"AS inserted_rows, SUM(amount) AS total_amount FROM {table}",
    )
    initial_amount = sum(value % 17 for value in range(1, config.fact_rows + 1))
    expected_amount = initial_amount - (1 % 17) + 999 + 5
    if _integer_values(
        row,
        "row_count",
        "distinct_ids",
        "updated_rows",
        "inserted_rows",
        "total_amount",
    ) != (config.fact_rows + 1, config.fact_rows + 1, 1, 1, expected_amount):
        raise BigQueryTransformQualificationError(
            "BigQuery transform incremental merge produced unexpected normalized output"
        )


def _expected_aggregates(
    config: BigQueryTransformConfig,
) -> tuple[tuple[str, int, int], ...]:
    totals = {f"category_{index}": [0, 0] for index in range(10)}
    for identifier in range(1, config.fact_rows + 1):
        category = f"category_{identifier % config.dimension_rows % 10}"
        totals[category][0] += identifier % 17
        totals[category][1] += 1
    return tuple(
        (category, totals[category][0], totals[category][1]) for category in sorted(totals)
    )


def _query_rows(client: _TransformClient, query: str) -> list[object]:
    return list(cast("Iterable[object]", client.query(query).result()))


def _one_row(client: _TransformClient, query: str) -> object:
    rows = _query_rows(client, query)
    if len(rows) != 1:
        raise BigQueryTransformQualificationError("BigQuery transform readback was malformed")
    return rows[0]


def _integer_values(row: object, *names: str) -> tuple[int, ...]:
    try:
        return tuple(int(cast("Any", row)[name]) for name in names)
    except (KeyError, TypeError, ValueError) as error:
        raise BigQueryTransformQualificationError(
            "BigQuery transform readback returned a non-integer"
        ) from error


@contextmanager
def _zero_provider_operation_retries() -> Iterator[None]:
    with (
        patch.object(bigquery_writer, "run_mutation_with_retry", _run_mutation_once),
        patch.object(bigquery_transform, "run_mutation_with_retry", _run_mutation_once),
    ):
        yield


def _run_mutation_once[JobT: common._NoRetryJob](submit: Callable[[], JobT]) -> JobT:
    job = submit()
    job.result()
    return job


def _report(
    config: BigQueryTransformConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _TransformResult,
) -> QualificationReport:
    if (
        result.input_rows != config.fact_rows + config.dimension_rows
        or result.output_rows != config.fact_rows + 1
        or result.model_count != _EXPECTED_MODELS
        or result.assertion_count != _EXPECTED_ASSERTIONS
        or result.fenced_publications != _EXPECTED_FENCED_PUBLICATIONS
        or result.provider_operation_retries != 0
        or result.temporary_staging_relations != 0
        or not result.cleanup_verified
    ):
        raise BigQueryTransformQualificationError("BigQuery transform evidence is incomplete")
    if result.reservation_usage_records:
        raise BigQueryTransformQualificationError(
            "provider jobs used a reservation instead of approved on-demand billing"
        )
    gross_cost = (
        Decimal(result.bytes_billed) * approval.on_demand_rate_usd_per_tib / _TIB
    ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    if gross_cost > approval.cost_ceiling.amount_usd:
        raise BigQueryTransformQualificationError(
            "provider-metered cost exceeds its approved ceiling"
        )
    measured = PerformanceMeasurement.measured
    metrics = (
        measured("assertion_count", "count", result.assertion_count),
        measured("bigquery_bytes_billed", "bytes", result.bytes_billed),
        measured("bigquery_bytes_processed", "bytes", result.bytes_processed),
        measured("bigquery_load_jobs", "count", result.load_jobs),
        measured("bigquery_query_jobs", "count", result.query_jobs),
        measured("bigquery_reservation_usage_records", "count", result.reservation_usage_records),
        measured("bigquery_slot_ms", "milliseconds", result.slot_ms),
        measured("fenced_publications", "count", result.fenced_publications),
        measured("model_count", "count", result.model_count),
        measured("ownership_verifications", "count", result.ownership_verifications),
        measured("provider_operation_retries", "count", result.provider_operation_retries),
        measured("temporary_staging_relations", "count", result.temporary_staging_relations),
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
            benchmark_class=BenchmarkClass.TRANSFORM,
            input_rows=result.input_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=32,
            schema_depth=4,
            source_rate_limit="unlimited_local_generator",
            transform_complexity="scan_join_aggregate_incremental_tests",
            concurrency=1,
            batch_rows=config.batch_rows,
            batch_bytes=config.batch_rows * 32,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.output_rows),
            logical_bytes=measured("logical_bytes", "bytes", result.logical_input_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                common._throughput(result.output_rows, result.transform_duration_ms),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured("queue_duration_ms", "milliseconds", 0),
            load_duration_ms=measured("load_duration_ms", "milliseconds", result.load_duration_ms),
            transform_duration_ms=measured(
                "transform_duration_ms", "milliseconds", result.transform_duration_ms
            ),
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
                evidence_reference=f"phase8/bigquery/transform/{name}",
            )
            for name in approval.objectives.names
        ),
        approved_objectives=approval.objectives,
        status=QualificationStatus.PASSED,
    )


def _create_client(config: BigQueryTransformConfig) -> _TransformClient:
    token = os.environ.get(config.token_env)
    credentials = cast("Any", Credentials)(token=token) if token else None
    client = bigquery.Client(project=config.project, credentials=credentials)
    return _TransformClient(
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
        raise BigQueryTransformQualificationError(
            "installed release does not match objective candidate"
        )
    config = BigQueryTransformConfig(
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
    result = _run_transform(config, _create_client(config))
    report = _report(config, identity, approval, result)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (BigQueryTransformQualificationError, ValueError, OSError) as error:
        print(f"BigQuery transform qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
