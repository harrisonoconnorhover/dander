#!/usr/bin/env python3
"""Exact-candidate Redshift transform Phase 8 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast

from dander import __version__
from dander.concurrency import FencingToken
from dander.providers.redshift.session import execute, open_connection
from dander.providers.redshift.transform import RedshiftTransformRunner
from dander.providers.redshift.writer import RedshiftStagedWriter
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
from dander.telemetry import (
    CostAttribution,
    OperationTelemetry,
    PerformanceMeasurement,
    RunPerformance,
    TelemetryOperation,
)
from dander.warehouse import RelationRef, WarehouseRuntime
from dander.writer import SchemaEvolution, WriteField, WriteTarget, WriteTransport
from scripts.benchmarks import redshift as shared
from scripts.benchmarks import redshift_bulk_phase8 as bulk

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


_APPROVAL_SCHEMA = "io.dander.qualification.objective-approval/v1"
_CONFIG_SCHEMA = "io.dander.phase8.redshift-transform/v1"
_AUTHORITY_ID = "redshift:phase8-transform"
_OBJECTIVES = (
    "aggregation_exact",
    "cleanup",
    "cost_ceiling",
    "generic_tests",
    "incremental_merge",
    "join_exact",
    "scan_exact",
)
_TASK_ROLE_REQUIREMENTS: dict[str, object] = {
    "redshift_db_roles_tag": {"key": "RedshiftDbRoles", "value": "dander_runtime"},
    "required_global_actions": ["tag:GetResources", "tag:GetTagKeys"],
    "required_global_resource": "*",
    "redshift_auth_action": "redshift-serverless:GetCredentials",
    "redshift_auth_resource_binding": "exact_owned_workgroup_arn_after_apply",
}
_FARGATE_LAUNCHER_REQUIREMENTS: dict[str, object] = {
    "runtime_cpu_architecture": "ARM64",
    "candidate_image_architecture": "arm64",
    "task_entrypoint": ["/bin/sh", "-c"],
    "candidate_python_executable": "python",
    "candidate_cli_executable": "dander",
    "forbidden_candidate_executable_prefix": "/app/.venv/bin/",
}
_CANDIDATE_COMMAND = (
    "dander qualification-run /tmp/harness/scripts/benchmarks/redshift_transform_phase8.py"
)


class RedshiftTransformQualificationError(RuntimeError):
    """Raised with a credential-free Redshift transform summary."""


@dataclass(frozen=True, slots=True)
class RedshiftTransformConfig:
    """Non-secret Redshift coordinates and the accepted transform workload."""

    account_id: str
    host: str
    database: str
    region: str
    workgroup_name: str
    copy_role_arn: str
    staging_bucket: str
    staging_prefix: str
    port: int = 5439
    connect_timeout_seconds: int = 300
    statement_timeout_ms: int = 900_000
    fact_rows: int = 100_000
    dimension_rows: int = 100
    copy_part_rows: int = 50_000
    copy_part_logical_bytes: int = 64 * 1_024 * 1_024
    cost_observation_delay_seconds: int = 70
    on_demand_rate_usd_per_rpu_hour: Decimal = Decimal("0.375")

    def __post_init__(self) -> None:
        if len(self.account_id) != 12 or not self.account_id.isdigit():
            raise ValueError("account_id must be a 12-digit AWS account id")
        for name in (
            "port",
            "connect_timeout_seconds",
            "statement_timeout_ms",
            "fact_rows",
            "dimension_rows",
            "copy_part_rows",
            "copy_part_logical_bytes",
            "cost_observation_delay_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.fact_rows != 100_000:
            raise ValueError("fact_rows must be exactly 100000")
        if self.dimension_rows != 100:
            raise ValueError("dimension_rows must be exactly 100")
        if self.copy_part_rows > self.fact_rows:
            raise ValueError("copy_part_rows must not exceed fact_rows")
        if self.cost_observation_delay_seconds < 60:
            raise ValueError("cost observation must wait for one complete provider interval")
        if (
            not self.on_demand_rate_usd_per_rpu_hour.is_finite()
            or self.on_demand_rate_usd_per_rpu_hour <= 0
        ):
            raise ValueError("Redshift on-demand rate must be a positive Decimal")
        _bulk_config(self)

    def workload_payload(self) -> dict[str, object]:
        """Return the exact workload covered by objective approval."""
        return {
            "schema": _CONFIG_SCHEMA,
            "benchmark_class": BenchmarkClass.TRANSFORM.value,
            "fact_rows": self.fact_rows,
            "dimension_rows": self.dimension_rows,
            "delta_rows": 2,
            "models": ["scan", "join", "aggregation", "incremental_merge"],
            "generic_tests": ["accepted_values", "not_null", "unique"],
            "generic_assertions": 21,
            "copy_part_rows": self.copy_part_rows,
            "copy_part_logical_bytes": self.copy_part_logical_bytes,
        }

    def configuration_sha256(self) -> str:
        """Hash the canonical workload used by objective approval."""
        encoded = json.dumps(
            self.workload_payload(), separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


CandidateIdentity = bulk.CandidateIdentity


@dataclass(frozen=True, slots=True)
class _Approval:
    objectives: ApprovedObjectiveSet
    cost_ceiling: ApprovedCostCeiling
    account_id: str
    region: str
    workgroup_name: str
    copy_role_arn: str
    staging_bucket: str
    staging_prefix: str
    cost_observation_delay_seconds: int
    on_demand_rate_usd_per_rpu_hour: Decimal


@dataclass(slots=True)
class _Ownership:
    fence: FencingToken
    verifications: int = 0

    def verify(self) -> None:
        self.verifications += 1


@dataclass(frozen=True, slots=True)
class _TransformResult:
    duration_ms: int
    peak_rss_bytes: int
    load_duration_ms: int
    transform_duration_ms: int
    input_rows: int
    logical_input_bytes: int
    output_rows: int
    model_count: int
    assertion_count: int
    ownership_verifications: int
    copy_operations: int
    fenced_publications: int
    query_ids: tuple[str, ...]
    queue_duration_ms: int
    bytes_processed: int
    spill_bytes: int
    charged_seconds: Decimal
    compute_seconds: Decimal
    maximum_compute_capacity_rpu: Decimal
    provider_cost_usd: Decimal
    provider_operation_retries: int
    staging_tables: int
    staging_objects: int
    cleanup_verified: bool


def _load_approval(
    path: Path,
    *,
    config: RedshiftTransformConfig,
    identity: CandidateIdentity,
) -> _Approval:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != _APPROVAL_SCHEMA:
        raise ValueError("objective approval schema is incompatible")
    if payload.get("workload") != config.workload_payload():
        raise ValueError("objective approval workload does not match the requested run")
    configuration = _mapping(payload.get("configuration"), "configuration")
    provider = _mapping(configuration.get("redshift"), "Redshift configuration")
    expected_provider = {
        "account_id": config.account_id,
        "region": config.region,
        "workgroup_name": config.workgroup_name,
        "copy_role_arn": config.copy_role_arn,
        "staging_bucket": config.staging_bucket,
        "staging_prefix": config.staging_prefix,
        "on_demand_rate_usd_per_rpu_hour": str(config.on_demand_rate_usd_per_rpu_hour),
    }
    if provider != expected_provider:
        raise ValueError("objective approval does not match the Redshift data plane")
    if _mapping(configuration.get("task_role"), "task role") != _TASK_ROLE_REQUIREMENTS:
        raise ValueError("objective approval does not bind the required Redshift task role")
    execution = _mapping(configuration.get("execution"), "execution configuration")
    if execution.get("harness_sha256") != _file_sha256(Path(__file__)):
        raise ValueError("objective approval does not match the protected harness")
    if execution.get("shared_harness_sha256") != _file_sha256(Path(shared.__file__)):
        raise ValueError("objective approval does not match the shared Redshift harness")
    if execution.get("bulk_harness_sha256") != _file_sha256(Path(bulk.__file__)):
        raise ValueError("objective approval does not match the reused Redshift helpers")
    if execution.get("manual_candidate_executions") != 1:
        raise ValueError("objective approval must allow exactly one candidate execution")
    if execution.get("automatic_candidate_retry") is not False:
        raise ValueError("objective approval must disable automatic candidate retry")
    if execution.get("provider_operation_retries") != 0:
        raise ValueError("objective approval must disable provider-operation retries")
    if execution.get("cost_observation_delay_seconds") != config.cost_observation_delay_seconds:
        raise ValueError("objective approval changed the provider cost observation")
    if execution.get("candidate_command") != _CANDIDATE_COMMAND:
        raise ValueError("objective approval does not bind the candidate command")
    fargate = _mapping(configuration.get("fargate_harness"), "Fargate configuration")
    expected_fargate = {
        "task_cpu_units": 2_048,
        "task_memory_mib": 4_096,
        "task_timeout_seconds": 900,
        "cluster_executions": 1,
        "state_machine_executions": 1,
        "state_machine_retry_states": 0,
        "ecs_task_retries": 0,
        "container_restarts": 0,
        "automatic_retry": False,
        **_FARGATE_LAUNCHER_REQUIREMENTS,
    }
    if any(fargate.get(name) != value for name, value in expected_fargate.items()):
        raise ValueError("objective approval must bind the protected zero-retry Fargate shape")
    objective_payload = _mapping(payload.get("approved_objectives"), "approved objectives")
    names = objective_payload.get("names")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("objective approval names are malformed")
    objectives = ApprovedObjectiveSet(
        names=tuple(names),
        benchmark_class=BenchmarkClass(str(objective_payload.get("benchmark_class"))),
        profile_id=str(objective_payload.get("profile_id")),
        release_version=str(objective_payload.get("release_version")),
        git_commit=str(objective_payload.get("git_commit")),
        image_digest=str(objective_payload.get("image_digest")),
        configuration_sha256=str(objective_payload.get("configuration_sha256")),
        approval_reference=str(objective_payload.get("approval_reference")),
    )
    if objectives.names != _OBJECTIVES:
        raise ValueError("objective approval names do not match Redshift transform qualification")
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
        account_id=config.account_id,
        region=config.region,
        workgroup_name=config.workgroup_name,
        copy_role_arn=config.copy_role_arn,
        staging_bucket=config.staging_bucket,
        staging_prefix=config.staging_prefix,
        cost_observation_delay_seconds=config.cost_observation_delay_seconds,
        on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
    )


def run_phase8_redshift_transform(
    config: RedshiftTransformConfig,
    *,
    identity: CandidateIdentity,
    approval: _Approval,
) -> QualificationReport:
    """Run the accepted transform class in disposable Redshift schemas."""
    if __version__ != identity.release_version:
        raise ValueError(
            f"installed Dander version {__version__!r} does not match {identity.release_version!r}"
        )
    _require_no_provider_retries()
    if approval.objectives.configuration_sha256 != config.configuration_sha256():
        raise ValueError("objective approval does not match the requested workload")
    suffix = uuid.uuid4().hex[:12]
    source_schema = f"dander_p8_transform_source_{suffix}"
    target_schema = f"dander_p8_transform_target_{suffix}"
    staging_prefix = f"{config.staging_prefix}/transform/{suffix}"
    runtime = bulk._warehouse_runtime(  # noqa: SLF001
        _bulk_config(config), schema_name=source_schema, staging_prefix=staging_prefix
    )
    started = time.perf_counter()
    peak_before = _peak_rss_bytes()
    result: _TransformResult | None = None
    failure: Exception | None = None
    try:
        load_started = time.perf_counter()
        seed_operations = _seed_transform_sources(
            runtime, config=config, source_schema=source_schema
        )
        load_duration_ms = _elapsed_ms(load_started)
        runner = runtime.transforms.build_transform_runner(
            graph_plan=None, build_models=True, raw_namespace=source_schema
        )
        if not isinstance(runner, RedshiftTransformRunner):
            raise RedshiftTransformQualificationError(
                "Redshift transform qualification did not select its native runner"
            )
        first_ownership = _transform_ownership(config.database, run_id="transform-one", token=1)
        second_ownership = _transform_ownership(config.database, run_id="transform-two", token=2)
        transform_started = time.perf_counter()
        with TemporaryDirectory(prefix="dander-phase8-redshift-transform-") as temporary:
            models = Path(temporary)
            _write_transform_models(models, target_schema=target_schema)
            initial = runner.build(
                models,
                selected=("aggregate_records", "incremental_records"),
                ownership=first_ownership,
            )
            initial_query_ids = _require_transform_initial(
                runtime, target_schema=target_schema, config=config
            )
            mutation_query_ids = _mutate_transform_sources(
                runtime, source_schema=source_schema, config=config
            )
            replay = runner.build(
                models,
                selected=("incremental_records",),
                ownership=second_ownership,
            )
            incremental_query_ids = _require_transform_incremental(
                runtime,
                target_schema=target_schema,
                expected_rows=config.fact_rows + 1,
            )
            tested = runner.test(models, selected=("aggregate_records", "incremental_records"))
            assertion_count = initial.assertions + replay.assertions + tested.assertions
            model_count = len(initial.models)
        transform_duration_ms = _elapsed_ms(transform_started)
        if assertion_count != 21 or model_count != 4:
            raise RedshiftTransformQualificationError(
                "Redshift transform qualification changed the accepted model or assertion count"
            )
        staging_tables = bulk._staging_table_count(runtime, source_schema) + (  # noqa: SLF001
            bulk._staging_table_count(runtime, target_schema)  # noqa: SLF001
        )
        staging_objects = shared._prefix_object_count(  # noqa: SLF001
            _shared_config(config), staging_prefix
        )
        if staging_tables or staging_objects:
            raise RedshiftTransformQualificationError(
                "Redshift transform qualification left run-scoped staging objects"
            )
        time.sleep(config.cost_observation_delay_seconds)
        charged, compute, capacity = bulk._serverless_usage(runtime)  # noqa: SLF001
        if charged <= 0:
            raise RedshiftTransformQualificationError(
                "Redshift Serverless did not report charged provider usage"
            )
        provider_cost = (charged * config.on_demand_rate_usd_per_rpu_hour / Decimal(3600)).quantize(
            Decimal("0.000000000001"), rounding=ROUND_HALF_UP
        )
        transform_operations = (*initial.telemetry, *replay.telemetry, *tested.telemetry)
        operations = (*seed_operations, *transform_operations)
        query_ids = tuple(
            dict.fromkeys(
                (
                    *bulk._operation_query_ids(operations),  # noqa: SLF001
                    *initial_query_ids,
                    *mutation_query_ids,
                    *incremental_query_ids,
                )
            )
        )[:100]
        result = _TransformResult(
            duration_ms=_elapsed_ms(started),
            peak_rss_bytes=max(peak_before, _peak_rss_bytes()),
            load_duration_ms=load_duration_ms,
            transform_duration_ms=transform_duration_ms,
            input_rows=config.fact_rows + config.dimension_rows,
            logical_input_bytes=(config.fact_rows * 32) + (config.dimension_rows * 24),
            output_rows=config.fact_rows + 1,
            model_count=model_count,
            assertion_count=assertion_count,
            ownership_verifications=(
                first_ownership.verifications + second_ownership.verifications
            ),
            copy_operations=sum(
                operation.operation is TelemetryOperation.LOAD for operation in seed_operations
            ),
            fenced_publications=len(initial.models) + len(replay.models),
            query_ids=query_ids,
            queue_duration_ms=sum(operation.queue_duration_ms for operation in operations),
            bytes_processed=sum(operation.bytes_processed for operation in operations),
            spill_bytes=sum(operation.spill_bytes for operation in operations),
            charged_seconds=charged,
            compute_seconds=compute,
            maximum_compute_capacity_rpu=capacity,
            provider_cost_usd=provider_cost,
            provider_operation_retries=sum(operation.retry_count for operation in operations),
            staging_tables=staging_tables,
            staging_objects=staging_objects,
            cleanup_verified=False,
        )
    except Exception as error:
        failure = error
    finally:
        cleanup_errors: list[Exception] = []
        for schema in (target_schema, source_schema):
            try:
                shared._drop_schema(runtime, schema)  # noqa: SLF001
            except Exception as error:
                cleanup_errors.append(error)
        try:
            shared._delete_prefix(_shared_config(config), staging_prefix)  # noqa: SLF001
        except Exception as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            raise RedshiftTransformQualificationError(
                "Redshift transform qualification could not remove all owned resources"
            ) from ExceptionGroup("Redshift transform cleanup failures", cleanup_errors)
    cleanup = (
        not shared._schema_exists(runtime, source_schema)  # noqa: SLF001
        and not shared._schema_exists(runtime, target_schema)  # noqa: SLF001
        and shared._prefix_object_count(_shared_config(config), staging_prefix) == 0  # noqa: SLF001
    )
    if not cleanup:
        raise RedshiftTransformQualificationError(
            "Redshift transform qualification cleanup could not be verified"
        )
    if failure is not None:
        if isinstance(failure, (RedshiftTransformQualificationError, ValueError)):
            raise failure
        raise RedshiftTransformQualificationError(
            "Redshift transform qualification failed before report completion; cleanup passed"
        ) from None
    assert result is not None
    return _report(config, identity, approval, replace(result, cleanup_verified=True))


def _seed_transform_sources(
    runtime: WarehouseRuntime,
    *,
    config: RedshiftTransformConfig,
    source_schema: str,
) -> tuple[OperationTelemetry, ...]:
    dimensions = _write_transform_source(
        runtime,
        config=config,
        source_schema=source_schema,
        table="dimensions",
        pipeline_id="phase8_redshift_transform_dimensions",
        business_key=("dimension_id",),
        fields=(
            WriteField(name="dimension_id", data_type="INT64", mode="REQUIRED"),
            WriteField(name="category", data_type="STRING", mode="REQUIRED"),
        ),
        records=_transform_dimension_records(config),
        expected_rows=config.dimension_rows,
    )
    facts = _write_transform_source(
        runtime,
        config=config,
        source_schema=source_schema,
        table="facts",
        pipeline_id="phase8_redshift_transform_facts",
        business_key=("id",),
        fields=(
            WriteField(name="id", data_type="INT64", mode="REQUIRED"),
            WriteField(name="dimension_id", data_type="INT64", mode="REQUIRED"),
            WriteField(name="amount", data_type="INT64", mode="REQUIRED"),
            WriteField(name="updated_at", data_type="INT64", mode="REQUIRED"),
        ),
        records=_transform_fact_records(config),
        expected_rows=config.fact_rows,
    )
    return (*dimensions, *facts)


def _write_transform_source(
    runtime: WarehouseRuntime,
    *,
    config: RedshiftTransformConfig,
    source_schema: str,
    table: str,
    pipeline_id: str,
    business_key: tuple[str, ...],
    fields: tuple[WriteField, ...],
    records: Iterator[dict[str, object]],
    expected_rows: int,
) -> tuple[OperationTelemetry, ...]:
    relation = RelationRef(catalog=config.database, namespace=source_schema, name=table)
    publication = runtime.target_fence.claim(
        relation,
        FencingToken(
            lease_table=None,
            pipeline_id=pipeline_id,
            run_id=f"{pipeline_id}-one",
            token=1,
            authority_id=_AUTHORITY_ID,
        ),
    )
    writer = runtime.writers.build_ingestion_writer(
        sandbox=False,
        batch_rows=config.copy_part_rows,
        schema_evolution=SchemaEvolution.STRICT,
    )
    if not isinstance(writer, RedshiftStagedWriter):
        raise RedshiftTransformQualificationError(
            "Redshift transform source seed did not select the staged writer"
        )
    target = WriteTarget(
        relation=relation,
        business_key=business_key,
        schema=fields,
        publication_fence=publication,
    )
    affected = writer.write(records, target)
    operations = writer.drain_telemetry()
    if not operations or any(
        operation.transport is not WriteTransport.COPY for operation in operations
    ):
        raise RedshiftTransformQualificationError(
            "Redshift transform source seed did not use COPY for the complete workload"
        )
    if any(operation.retry_count != 0 for operation in operations):
        raise RedshiftTransformQualificationError(
            "Redshift transform source seed observed a provider-operation retry"
        )
    if affected != expected_rows:
        raise RedshiftTransformQualificationError(
            "Redshift transform source seed affected an unexpected row count"
        )
    return operations


def _transform_dimension_records(
    config: RedshiftTransformConfig,
) -> Iterator[dict[str, object]]:
    for index in range(config.dimension_rows):
        yield {"dimension_id": index, "category": f"category_{index % 10}"}


def _transform_fact_records(
    config: RedshiftTransformConfig,
) -> Iterator[dict[str, object]]:
    for index in range(1, config.fact_rows + 1):
        yield {
            "id": index,
            "dimension_id": index % config.dimension_rows,
            "amount": index % 17,
            "updated_at": 1,
        }


def _write_transform_models(root: Path, *, target_schema: str) -> None:
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
            "description: Phase 8 Redshift transform qualification.\n"
            "owner: data-eng\n"
            "dialect: portable\n"
            f"materialization: {materialization}\n"
            f"dataset: {target_schema}\n"
            "source_system: phase8_fixture\n"
            "sensitivity: public\n"
            f"{incremental}"
            f"columns:\n{column_yaml}"
            f"tests:\n{tests}",
            encoding="utf-8",
        )


def _transform_ownership(database: str, *, run_id: str, token: int) -> _Ownership:
    return _Ownership(
        FencingToken(
            lease_table=None,
            pipeline_id="phase8_redshift_transform",
            run_id=run_id,
            token=token,
            authority_id=f"{_AUTHORITY_ID}:{database}",
        )
    )


def _mutate_transform_sources(
    runtime: WarehouseRuntime,
    *,
    source_schema: str,
    config: RedshiftTransformConfig,
) -> tuple[str, ...]:
    with open_connection(bulk._connection_factory(runtime)) as connection:  # noqa: SLF001
        updated = execute(
            connection,
            f"UPDATE {_qualified(source_schema, 'facts')} "
            'SET "amount" = 999, "updated_at" = 2 WHERE "id" = 1',
        )
        inserted = execute(
            connection,
            f"INSERT INTO {_qualified(source_schema, 'facts')} "
            '("id", "dimension_id", "amount", "updated_at") VALUES (%s, 1, 5, 2)',
            (config.fact_rows + 1,),
        )
        connection.commit()
    if updated.rowcount != 1 or inserted.rowcount != 1:
        raise RedshiftTransformQualificationError(
            "Redshift transform delta affected an unexpected row count"
        )
    return _statement_query_ids(updated.query_id, inserted.query_id)


def _require_transform_initial(
    runtime: WarehouseRuntime,
    *,
    target_schema: str,
    config: RedshiftTransformConfig,
) -> tuple[str, ...]:
    with open_connection(bulk._connection_factory(runtime)) as connection:  # noqa: SLF001
        scan = execute(
            connection,
            f"SELECT COUNT(*) FROM {_qualified(target_schema, 'scan_records')}",
            fetch="one",
        )
        joined = execute(
            connection,
            'SELECT COUNT(*), COUNT(DISTINCT "category") FROM '
            f"{_qualified(target_schema, 'joined_records')}",
            fetch="one",
        )
        aggregate = execute(
            connection,
            'SELECT COUNT(*), SUM("row_count"), SUM("total_amount") FROM '
            f"{_qualified(target_schema, 'aggregate_records')}",
            fetch="one",
        )
        incremental = execute(
            connection,
            f"SELECT COUNT(*) FROM {_qualified(target_schema, 'incremental_records')}",
            fetch="one",
        )
    expected_amount = sum(value % 17 for value in range(1, config.fact_rows + 1))
    if _integer_row(scan.row, 1) != (config.fact_rows,):
        raise RedshiftTransformQualificationError(
            "Redshift transform scan produced unexpected rows"
        )
    if _integer_row(joined.row, 2) != (config.fact_rows, 10):
        raise RedshiftTransformQualificationError(
            "Redshift transform join produced unexpected rows"
        )
    if _integer_row(aggregate.row, 3) != (10, config.fact_rows, expected_amount):
        raise RedshiftTransformQualificationError(
            "Redshift transform aggregation produced unexpected rows"
        )
    if _integer_row(incremental.row, 1) != (config.fact_rows,):
        raise RedshiftTransformQualificationError(
            "Redshift transform incremental seed produced unexpected rows"
        )
    return _statement_query_ids(
        scan.query_id, joined.query_id, aggregate.query_id, incremental.query_id
    )


def _require_transform_incremental(
    runtime: WarehouseRuntime,
    *,
    target_schema: str,
    expected_rows: int,
) -> tuple[str, ...]:
    with open_connection(bulk._connection_factory(runtime)) as connection:  # noqa: SLF001
        result = execute(
            connection,
            "SELECT COUNT(*), "
            'SUM(CASE WHEN "id" = 1 AND "amount" = 999 AND "updated_at" = 2 THEN 1 ELSE 0 END), '
            'SUM(CASE WHEN "id" = %s AND "amount" = 5 AND "updated_at" = 2 THEN 1 ELSE 0 END) '
            f"FROM {_qualified(target_schema, 'incremental_records')}",
            (expected_rows,),
            fetch="one",
        )
    if _integer_row(result.row, 3) != (expected_rows, 1, 1):
        raise RedshiftTransformQualificationError(
            "Redshift transform incremental merge produced unexpected rows"
        )
    return _statement_query_ids(result.query_id)


def _report(
    config: RedshiftTransformConfig,
    identity: CandidateIdentity,
    approval: _Approval,
    result: _TransformResult,
) -> QualificationReport:
    if (
        not result.cleanup_verified
        or result.staging_tables
        or result.staging_objects
        or result.provider_operation_retries
        or result.assertion_count != 21
        or result.model_count != 4
    ):
        raise ValueError("Redshift transform result does not satisfy the accepted contract")
    cost_status = (
        ObjectiveStatus.PASSED
        if result.provider_cost_usd <= approval.cost_ceiling.amount_usd
        else ObjectiveStatus.FAILED
    )
    objectives = tuple(
        ObjectiveResult(
            name,
            cost_status if name == "cost_ceiling" else ObjectiveStatus.PASSED,
            f"phase8/aws/redshift/transform/{name}",
        )
        for name in approval.objectives.names
    )
    status = (
        QualificationStatus.PASSED
        if cost_status is ObjectiveStatus.PASSED
        else QualificationStatus.FAILED
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
            warehouse="redshift",
            state_backend="none",
            catalog="none",
            secret_provider=identity.secret_provider,
            regions=(f"aws:{config.region}",),
            service_shapes=tuple(sorted(set(identity.service_shapes))),
            provider_job_ids=tuple(sorted(set((*identity.provider_job_ids, *result.query_ids)))),
            cost_ceiling=approval.cost_ceiling,
        ),
        workload=BenchmarkWorkload(
            benchmark_class=BenchmarkClass.TRANSFORM,
            input_rows=result.input_rows,
            logical_input_bytes=result.logical_input_bytes,
            row_width_bytes=32,
            schema_depth=4,
            source_rate_limit="unlimited_container_generator",
            transform_complexity="scan_join_aggregate_incremental_tests",
            concurrency=1,
            batch_rows=config.copy_part_rows,
            batch_bytes=config.copy_part_logical_bytes,
            configuration_sha256=config.configuration_sha256(),
        ),
        performance=RunPerformance(
            rows=measured("rows", "rows", result.output_rows),
            logical_bytes=measured("logical_bytes", "bytes", result.logical_input_bytes),
            duration_ms=measured("duration_ms", "milliseconds", result.duration_ms),
            throughput_rows_per_second=measured(
                "throughput_rows_per_second",
                "rows_per_second",
                _throughput(result.output_rows, result.duration_ms),
            ),
            peak_rss_bytes=measured("peak_rss_bytes", "bytes", result.peak_rss_bytes),
            retries=measured("retries", "count", 0),
            queue_duration_ms=measured(
                "queue_duration_ms", "milliseconds", result.queue_duration_ms
            ),
            load_duration_ms=measured("load_duration_ms", "milliseconds", result.load_duration_ms),
            transform_duration_ms=measured(
                "transform_duration_ms", "milliseconds", result.transform_duration_ms
            ),
            catalog_duration_ms=measured("catalog_duration_ms", "milliseconds", 0),
            provider_metrics=(
                measured("assertion_count", "count", result.assertion_count),
                measured("bytes_processed", "bytes", result.bytes_processed),
                measured("charged_seconds", "rpu_seconds", result.charged_seconds),
                measured("compute_seconds", "rpu_seconds", result.compute_seconds),
                measured("copy_operations", "count", result.copy_operations),
                measured("fenced_publications", "count", result.fenced_publications),
                measured("maximum_compute_capacity", "rpu", result.maximum_compute_capacity_rpu),
                measured("model_count", "count", result.model_count),
                measured("ownership_verifications", "count", result.ownership_verifications),
                measured("provider_operation_retries", "count", 0),
                measured("spill_bytes", "bytes", result.spill_bytes),
                measured("staging_objects", "count", result.staging_objects),
                measured("staging_tables", "count", result.staging_tables),
            ),
            costs=(
                CostAttribution(
                    provider="aws",
                    service="redshift_serverless",
                    amount=result.provider_cost_usd,
                    estimated=False,
                ),
            ),
        ),
        objectives=objectives,
        approved_objectives=approval.objectives,
        status=status,
    )


def _bulk_config(config: RedshiftTransformConfig) -> bulk.RedshiftBulkConfig:
    return bulk.RedshiftBulkConfig(
        account_id=config.account_id,
        host=config.host,
        database=config.database,
        region=config.region,
        workgroup_name=config.workgroup_name,
        copy_role_arn=config.copy_role_arn,
        staging_bucket=config.staging_bucket,
        staging_prefix=config.staging_prefix,
        port=config.port,
        connect_timeout_seconds=config.connect_timeout_seconds,
        statement_timeout_ms=config.statement_timeout_ms,
        narrow_rows=config.fact_rows,
        narrow_payload_bytes=32,
        wide_rows=config.fact_rows,
        wide_payload_bytes=32,
        copy_part_rows=config.copy_part_rows,
        copy_part_logical_bytes=config.copy_part_logical_bytes,
        cost_observation_delay_seconds=config.cost_observation_delay_seconds,
        on_demand_rate_usd_per_rpu_hour=config.on_demand_rate_usd_per_rpu_hour,
    )


def _shared_config(config: RedshiftTransformConfig) -> shared.RedshiftQualificationConfig:
    return bulk._shared_config(_bulk_config(config))  # noqa: SLF001


def _integer_row(row: object, length: int) -> tuple[int, ...]:
    if not isinstance(row, (tuple, list)) or len(row) != length:
        raise RedshiftTransformQualificationError("Redshift transform readback was malformed")
    try:
        return tuple(int(value) for value in row)
    except (TypeError, ValueError) as error:
        raise RedshiftTransformQualificationError(
            "Redshift transform readback was not numeric"
        ) from error


def _statement_query_ids(*query_ids: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in query_ids if value))


def _qualified(*parts: str) -> str:
    return ".".join(f'"{part.replace(chr(34), chr(34) * 2)}"' for part in parts)


def _require_no_provider_retries() -> None:
    if os.environ.get("AWS_MAX_ATTEMPTS") != "1":
        raise RedshiftTransformQualificationError("AWS_MAX_ATTEMPTS must be exactly 1")
    if os.environ.get("AWS_RETRY_MODE") != "standard":
        raise RedshiftTransformQualificationError("AWS_RETRY_MODE must be standard")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast("Mapping[str, object]", value)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _throughput(rows: int, duration_ms: int) -> Decimal:
    return (Decimal(rows) * 1_000 / Decimal(max(duration_ms, 1))).quantize(Decimal("0.001"))


def _elapsed_ms(started: float) -> int:
    return max(round((time.perf_counter() - started) * 1_000), 1)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1_024)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--workgroup-name", required=True)
    parser.add_argument("--copy-role-arn", required=True)
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--staging-prefix", required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--benchmark-date", type=date.fromisoformat, required=True)
    parser.add_argument("--launcher", default="aws_step_functions_fargate")
    parser.add_argument("--secret-provider", default="aws_task_role")
    parser.add_argument("--service-shape", action="append", required=True)
    parser.add_argument("--provider-job-id", action="append", required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    config = RedshiftTransformConfig(
        account_id=arguments.account_id,
        host=arguments.host,
        database=arguments.database,
        region=arguments.region,
        workgroup_name=arguments.workgroup_name,
        copy_role_arn=arguments.copy_role_arn,
        staging_bucket=arguments.staging_bucket,
        staging_prefix=arguments.staging_prefix,
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
        provider_job_ids=tuple(sorted(set(arguments.provider_job_id))),
    )
    approval = _load_approval(arguments.approval_manifest, config=config, identity=identity)
    report = run_phase8_redshift_transform(config, identity=identity, approval=approval)
    arguments.output.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


if __name__ == "__main__":
    try:
        main()
    except (RedshiftTransformQualificationError, ValueError, OSError) as error:
        print(f"Redshift transform qualification failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
