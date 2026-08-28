#!/usr/bin/env python3
"""Content-addressed Spark runtime for Dander's bounded linear graph contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

RUNTIME_CONTRACT = "io.dander.runtime/v1"
PHYSICAL_PLAN_SCHEMA = "io.dander.physical-plan/v1"
CONFIGURATION_SCHEMA = "io.dander.spark-linear-configuration/v1"
SPARK_RUNTIME_CONTRACT = "io.dander.spark-linear-runtime/v1"
_DRIVER_PATH = Path("/opt/dander/spark_driver.py")
_MAX_CONFIGURATION_BYTES = 1_048_576
_MAX_PHYSICAL_PLAN_BYTES = 262_144
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_TABLE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,1023}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}$")
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])"
    r"\.iam\.gserviceaccount\.com$"
)
_EXECUTION = re.compile(
    r"^projects/(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])/locations/"
    r"[a-z]+(?:-[a-z0-9]+)+[0-9]/batches/dander-[0-9a-f]{40}$"
)
_RELATION = re.compile(
    r"^(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])\."
    r"(?P<dataset>[A-Za-z_][A-Za-z0-9_]{0,1023})\."
    r"(?P<table>[A-Za-z0-9_][A-Za-z0-9_-]{0,1023})$"
)
_SUPPORTED_TYPES = frozenset(
    {
        "BOOL",
        "BOOLEAN",
        "BYTES",
        "DATE",
        "DATETIME",
        "FLOAT",
        "FLOAT64",
        "INT64",
        "INTEGER",
        "NUMERIC",
        "STRING",
        "TIME",
        "TIMESTAMP",
    }
)


class SparkDriverError(RuntimeError):
    """The immutable driver contract or managed Spark result is invalid."""

    def __init__(
        self,
        message: str,
        *,
        failure_code: str = "spark_linear_runtime_failed",
    ) -> None:
        super().__init__(message)
        self.failure_code = failure_code


@dataclass(frozen=True, slots=True)
class Invocation:
    """Validated arguments covered by the immutable execution-plan revision."""

    pipeline_id: str
    project: str
    staging_bucket: str
    platform: str
    graph_content_sha256: str
    physical_plan: dict[str, object]
    physical_plan_revision: str
    driver_sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Validated non-secret Control correlation projected into the Spark driver."""

    run_id: str
    launcher_execution_id: str
    attempt: int
    principal: str
    configuration_reference: str


@dataclass(frozen=True, slots=True)
class DirectMapping:
    """One validated direct field projection or rename."""

    source: str
    target: str


@dataclass(frozen=True, slots=True)
class LinearGraph:
    """The executable subset derived from one canonical graph configuration."""

    source_id: str
    transform_id: str
    target_id: str
    source_fields: tuple[str, ...]
    transform_fields: tuple[str, ...]
    target_fields: tuple[str, ...]
    source_to_transform: tuple[DirectMapping, ...]
    transform_to_target: tuple[DirectMapping, ...]
    source_table: str
    output_table: str


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Content-addressed graph and bindings loaded from the plan's GCS reference."""

    graph_content_sha256: str
    graph: LinearGraph
    exchange_partitions: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class SparkResult:
    """Bounded result metadata retained from the managed Spark round-trip."""

    source_id: str
    target_id: str
    output_table: str
    exchange_uri: str
    source_rows: int
    affected_rows: int
    executor_instances: int
    exchange_partitions: int


def parse_invocation(arguments: list[str]) -> Invocation:
    """Parse the Dander runtime command plus the bounded Spark contract."""
    if arguments[:2] != ["runtime", "execute"]:
        raise SparkDriverError("the driver requires the Dander runtime execute command")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--physical-plan", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--graph-content-sha256", required=True)
    parser.add_argument("--driver-sha256", required=True)
    try:
        values, unknown = parser.parse_known_args(arguments[2:])
    except SystemExit as error:
        raise SparkDriverError("the driver arguments are incomplete") from error
    if unknown:
        raise SparkDriverError("the driver arguments contain unsupported options")
    if values.contract != RUNTIME_CONTRACT:
        raise SparkDriverError("the driver runtime contract is invalid")
    if _SAFE_IDENTIFIER.fullmatch(values.pipeline) is None:
        raise SparkDriverError("the driver pipeline identity is invalid")
    if values.platform != "gcp":
        raise SparkDriverError("the driver requires the GCP profile")
    if _PROJECT.fullmatch(values.project) is None:
        raise SparkDriverError("the driver GCP project is invalid")
    if _BUCKET.fullmatch(values.staging_bucket) is None:
        raise SparkDriverError("the driver staging bucket is invalid")
    if _SHA256.fullmatch(values.graph_content_sha256) is None:
        raise SparkDriverError("the driver graph content identity is invalid")
    if _SHA256.fullmatch(values.driver_sha256) is None:
        raise SparkDriverError("the driver content identity is invalid")
    plan, revision = _physical_plan(values.physical_plan)
    return Invocation(
        pipeline_id=values.pipeline,
        project=values.project,
        staging_bucket=values.staging_bucket,
        platform=values.platform,
        graph_content_sha256=values.graph_content_sha256,
        physical_plan=plan,
        physical_plan_revision=revision,
        driver_sha256=values.driver_sha256,
    )


def runtime_context(
    invocation: Invocation,
    environment: dict[str, str] | None = None,
) -> RuntimeContext:
    """Validate the exact Control-to-driver correlation and configuration handoff."""
    values = dict(os.environ if environment is None else environment)
    run_id = values.get("DANDER_RUN_ID", "")
    launcher = values.get("DANDER_LAUNCHER", "")
    execution_id = values.get("DANDER_LAUNCHER_EXECUTION_ID", "")
    attempt = values.get("DANDER_ATTEMPT", "")
    shard_index = values.get("DANDER_SHARD_INDEX", "")
    shard_count = values.get("DANDER_SHARD_COUNT", "")
    principal = values.get("DANDER_PRINCIPAL", "")
    configuration = values.get("DANDER_CONFIGURATION_REFERENCE", "")
    secret_bindings = values.get("DANDER_SECRET_BINDINGS_JSON")
    execution = _EXECUTION.fullmatch(execution_id)
    service_account = _SERVICE_ACCOUNT.fullmatch(principal)
    if _SAFE_IDENTIFIER.fullmatch(run_id) is None or launcher != "dataproc_serverless":
        raise SparkDriverError("the driver runtime correlation is invalid")
    if execution is None or execution.group("project") != invocation.project:
        raise SparkDriverError("the driver execution identity is invalid")
    if not attempt.isdigit() or not 1 <= int(attempt) <= 1000:
        raise SparkDriverError("the driver attempt identity is invalid")
    if shard_index != "0" or shard_count != "1":
        raise SparkDriverError("the Spark driver must receive one unsharded runtime context")
    if service_account is None or service_account.group("project") != invocation.project:
        raise SparkDriverError("the driver service account is invalid")
    configuration_pattern = re.compile(
        rf"^gs://{re.escape(invocation.staging_bucket)}/config/[0-9a-f]{{64}}\.json$"
    )
    if configuration_pattern.fullmatch(configuration) is None or len(configuration) > 1024:
        raise SparkDriverError("the driver configuration reference is invalid")
    if secret_bindings not in {None, "{}"}:
        raise SparkDriverError("the bounded Spark runtime does not accept secret bindings")
    return RuntimeContext(
        run_id=run_id,
        launcher_execution_id=execution_id,
        attempt=int(attempt),
        principal=principal,
        configuration_reference=configuration,
    )


def validate_driver_pair(
    expected_sha256: str,
    *,
    embedded_driver: Path = _DRIVER_PATH,
    submitted_driver: Path | None = None,
) -> None:
    """Require byte-identical submitted and image-embedded driver artifacts."""
    submitted = Path(__file__).resolve() if submitted_driver is None else submitted_driver
    try:
        submitted_digest = hashlib.sha256(submitted.read_bytes()).hexdigest()
        embedded_digest = hashlib.sha256(embedded_driver.read_bytes()).hexdigest()
    except OSError as error:
        raise SparkDriverError("the immutable driver pair is unavailable") from error
    if submitted_digest != expected_sha256 or embedded_digest != expected_sha256:
        raise SparkDriverError("the immutable driver pair does not match its execution plan")


def parse_runtime_configuration(raw: bytes, invocation: Invocation) -> RuntimeConfiguration:
    """Verify and reduce one canonical graph configuration to the executable subset."""
    if not raw or len(raw) > _MAX_CONFIGURATION_BYTES:
        raise SparkDriverError("the Spark runtime configuration size is invalid")
    try:
        payload = json.loads(raw)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SparkDriverError("the Spark runtime configuration is invalid") from error
    if raw != _canonical_json(payload) or not isinstance(payload, dict):
        raise SparkDriverError("the Spark runtime configuration is not canonical")
    if set(payload) != {"schema", "graph_content_sha256", "graph", "source_relations"}:
        raise SparkDriverError("the Spark runtime configuration fields are unsupported")
    if payload.get("schema") != CONFIGURATION_SCHEMA:
        raise SparkDriverError("the Spark runtime configuration schema is unsupported")
    graph_payload = payload.get("graph")
    graph_identity = payload.get("graph_content_sha256")
    if not isinstance(graph_payload, dict) or not isinstance(graph_identity, str):
        raise SparkDriverError("the Spark runtime graph configuration is invalid")
    derived_graph_identity = hashlib.sha256(_canonical_json(graph_payload)).hexdigest()
    if (
        graph_identity != derived_graph_identity
        or graph_identity != invocation.graph_content_sha256
    ):
        raise SparkDriverError("the Spark runtime graph identity does not match its plan")
    graph = _linear_graph(graph_payload, payload.get("source_relations"), invocation)
    exchange_partitions = _validate_linear_plan(
        invocation.physical_plan, invocation.pipeline_id, graph
    )
    return RuntimeConfiguration(
        graph_content_sha256=derived_graph_identity,
        graph=graph,
        exchange_partitions=exchange_partitions,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )


def read_runtime_configuration(
    spark: object,
    context: RuntimeContext,
    invocation: Invocation,
) -> RuntimeConfiguration:
    """Read one bounded canonical GCS object through the managed Spark filesystem."""
    dynamic_spark = cast("Any", spark)
    try:
        rows = dynamic_spark.read.text(context.configuration_reference).take(2)
        if len(rows) != 1:
            raise SparkDriverError("the Spark runtime configuration must contain one line")
        value = cast("Any", rows[0])["value"]
        if not isinstance(value, str):
            raise SparkDriverError("the Spark runtime configuration row is invalid")
        raw = value.encode("utf-8")
    except SparkDriverError:
        raise
    except Exception as error:
        raise SparkDriverError(
            "the Spark runtime configuration could not be read",
            failure_code="spark_configuration_read_failed",
        ) from error
    configuration = parse_runtime_configuration(raw, invocation)
    if configuration.content_sha256 != Path(context.configuration_reference).stem:
        raise SparkDriverError("the Spark runtime configuration object is not content-addressed")
    return configuration


def _physical_plan(raw: str) -> tuple[dict[str, object], str]:
    if not raw or len(raw.encode("utf-8")) > _MAX_PHYSICAL_PLAN_BYTES:
        raise SparkDriverError("the physical plan size is invalid")
    try:
        envelope = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise SparkDriverError("the physical plan is invalid") from error
    if not isinstance(envelope, dict) or envelope.get("schema") != PHYSICAL_PLAN_SCHEMA:
        raise SparkDriverError("the physical plan schema is unsupported")
    revision = envelope.get("revision")
    plan = envelope.get("plan")
    if not isinstance(revision, str) or _SHA256.fullmatch(revision) is None:
        raise SparkDriverError("the physical plan revision is invalid")
    if not isinstance(plan, dict) or any(not isinstance(key, str) for key in plan):
        raise SparkDriverError("the physical plan body is invalid")
    canonical_contents = _canonical_json({"schema": PHYSICAL_PLAN_SCHEMA, "plan": plan})
    derived = hashlib.sha256(canonical_contents).hexdigest()
    if revision != derived or raw.encode() != _canonical_json(envelope):
        raise SparkDriverError("the physical plan is not canonical or self-verifying")
    return cast("dict[str, object]", plan), derived


def _linear_graph(
    graph: dict[str, object], raw_source_relations: object, invocation: Invocation
) -> LinearGraph:
    if graph.get("name") != invocation.pipeline_id:
        raise SparkDriverError("the configuration graph does not match the pipeline")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise SparkDriverError("the configuration graph topology is invalid")
    by_type: dict[str, list[dict[str, object]]] = {
        "source": [],
        "transform": [],
        "target": [],
    }
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") not in by_type:
            raise SparkDriverError("the configuration graph node is unsupported")
        by_type[cast("str", node["type"])].append(cast("dict[str, object]", node))
    if any(len(group) != 1 for group in by_type.values()) or len(nodes) != 3:
        raise SparkDriverError("the Spark runtime requires one source, transform, and target")
    source = by_type["source"][0]
    transform = by_type["transform"][0]
    target = by_type["target"][0]
    source_id = _node_id(source)
    transform_id = _node_id(transform)
    target_id = _node_id(target)
    source_fields, source_types = _node_fields(source)
    transform_fields, transform_types = _node_fields(transform)
    target_fields, target_types = _node_fields(target)
    transform_config = transform.get("config")
    if not isinstance(transform_config, dict):
        raise SparkDriverError("the transform configuration is invalid")
    if transform_config.get("join") is not None or transform_config.get("operations", []) != []:
        raise SparkDriverError("the Spark runtime supports direct mappings without joins")
    edge_by_pair: dict[tuple[str, str], dict[str, object]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            raise SparkDriverError("the configuration graph edge is invalid")
        pair = (edge.get("from"), edge.get("to"))
        if not all(isinstance(value, str) for value in pair) or pair in edge_by_pair:
            raise SparkDriverError("the configuration graph edge identity is invalid")
        edge_by_pair[cast("tuple[str, str]", pair)] = edge
    expected_pairs = {(source_id, transform_id), (transform_id, target_id)}
    if set(edge_by_pair) != expected_pairs:
        raise SparkDriverError("the Spark runtime requires a linear graph chain")
    first = _direct_mappings(
        edge_by_pair[(source_id, transform_id)],
        source_types,
        transform_types,
        transform_fields,
    )
    second = _direct_mappings(
        edge_by_pair[(transform_id, target_id)],
        transform_types,
        target_types,
        target_fields,
    )
    source_relations = raw_source_relations
    if not isinstance(source_relations, dict) or set(source_relations) != {source_id}:
        raise SparkDriverError("the Spark runtime source relation binding is invalid")
    source_table = source_relations[source_id]
    if not isinstance(source_table, str):
        raise SparkDriverError("the Spark runtime source table is invalid")
    _same_project_relation(source_table, invocation.project, "source")
    output_table = _target_relation(target, invocation.project)
    return LinearGraph(
        source_id=source_id,
        transform_id=transform_id,
        target_id=target_id,
        source_fields=source_fields,
        transform_fields=transform_fields,
        target_fields=target_fields,
        source_to_transform=first,
        transform_to_target=second,
        source_table=source_table,
        output_table=output_table,
    )


def _node_id(node: dict[str, object]) -> str:
    value = node.get("id")
    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SparkDriverError("the configuration graph node identity is invalid")
    return value


def _node_fields(node: dict[str, object]) -> tuple[tuple[str, ...], dict[str, str]]:
    raw_fields = node.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise SparkDriverError("the configuration graph fields are invalid")
    names: list[str] = []
    types: dict[str, str] = {}
    for field in raw_fields:
        if not isinstance(field, dict):
            raise SparkDriverError("the configuration graph field is invalid")
        name = field.get("name")
        field_type = field.get("type")
        if (
            not isinstance(name, str)
            or _SAFE_IDENTIFIER.fullmatch(name) is None
            or not isinstance(field_type, str)
            or field_type.upper() not in _SUPPORTED_TYPES
            or field.get("cast_to") is not None
            or name in types
        ):
            raise SparkDriverError("the configuration graph field contract is unsupported")
        names.append(name)
        types[name] = field_type.upper()
    return tuple(names), types


def _direct_mappings(
    edge: dict[str, object],
    source_types: dict[str, str],
    target_types: dict[str, str],
    target_fields: tuple[str, ...],
) -> tuple[DirectMapping, ...]:
    if edge.get("join") is not None:
        raise SparkDriverError("the Spark runtime does not support joins")
    raw_mappings = edge.get("mappings")
    if not isinstance(raw_mappings, list) or not raw_mappings:
        raise SparkDriverError("the Spark runtime mappings are invalid")
    by_target: dict[str, DirectMapping] = {}
    for raw_mapping in raw_mappings:
        if not isinstance(raw_mapping, dict):
            raise SparkDriverError("the Spark runtime mapping is invalid")
        source = raw_mapping.get("source")
        target = raw_mapping.get("target")
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or raw_mapping.get("transformation") is not None
            or source not in source_types
            or target not in target_types
            or source_types[source] != target_types[target]
            or target in by_target
        ):
            raise SparkDriverError(
                "the Spark runtime supports only type-preserving direct mappings"
            )
        by_target[target] = DirectMapping(source=source, target=target)
    if set(by_target) != set(target_fields):
        raise SparkDriverError("the Spark runtime mappings must cover the declared output")
    return tuple(by_target[field] for field in target_fields)


def _target_relation(target: dict[str, object], project: str) -> str:
    config = target.get("config")
    writer = config.get("writer") if isinstance(config, dict) else None
    destination = writer.get("destination") if isinstance(writer, dict) else None
    if not isinstance(writer, dict) or not isinstance(destination, dict):
        raise SparkDriverError("the Spark runtime target writer is invalid")
    if (
        writer.get("write_mode") != "replace"
        or writer.get("partitioning") is not None
        or writer.get("clustering", []) != []
        or writer.get("transport", "load_job") != "load_job"
        or writer.get("schema_evolution", "strict") != "strict"
        or destination.get("business_key", []) != []
    ):
        raise SparkDriverError("the Spark runtime target writer is unsupported")
    target_project = destination.get("project") or project
    dataset = destination.get("dataset")
    table = destination.get("table")
    if (
        target_project != project
        or not isinstance(dataset, str)
        or _DATASET.fullmatch(dataset) is None
        or not isinstance(table, str)
        or _TABLE.fullmatch(table) is None
    ):
        raise SparkDriverError("the Spark runtime target relation is invalid")
    return f"{project}.{dataset}.{table}"


def _same_project_relation(value: str, project: str, kind: str) -> None:
    match = _RELATION.fullmatch(value)
    if match is None or match.group("project") != project:
        raise SparkDriverError(f"the Spark runtime {kind} relation is invalid")


def _validate_linear_plan(plan: dict[str, object], pipeline_id: str, graph: LinearGraph) -> int:
    partition_count = plan.get("maximum_parallelism")
    if (
        isinstance(partition_count, bool)
        or not isinstance(partition_count, int)
        or not 2 <= partition_count <= 2_000
    ):
        raise SparkDriverError("the physical plan does not match the configured linear graph")
    expected = {
        "pipeline_id": pipeline_id,
        "execution_mode": "distributed",
        "stages": [
            {
                "stage_id": "extract",
                "operators": [graph.source_id],
                "partition_count": partition_count,
                "depends_on": [],
            },
            {
                "stage_id": "transform",
                "operators": sorted([graph.transform_id, graph.target_id]),
                "partition_count": partition_count,
                "depends_on": ["extract"],
            },
        ],
        "exchanges": [
            {
                "exchange_id": "extract-transform",
                "producer_stage_id": "extract",
                "consumer_stage_id": "transform",
                "transport": "object_store",
                "partitioning": "round_robin",
                "partition_count": partition_count,
                "partition_keys": [],
            }
        ],
        "maximum_parallelism": partition_count,
    }
    if plan != expected:
        raise SparkDriverError("the physical plan does not match the configured linear graph")
    return partition_count


def _planned_executor_instances(spark: object, partition_count: int) -> int:
    dynamic_spark = cast("Any", spark)
    try:
        dynamic_allocation = dynamic_spark.conf.get("spark.dynamicAllocation.enabled")
        executor_instances = int(dynamic_spark.conf.get("spark.executor.instances"))
    except Exception as error:
        raise SparkDriverError(
            "the managed Spark runtime executor plan is unavailable",
            failure_code="spark_planned_shape_mismatch",
        ) from error
    if dynamic_allocation.casefold() != "false" or executor_instances != partition_count:
        raise SparkDriverError(
            "the managed Spark runtime changed the planned executor shape",
            failure_code="spark_planned_shape_mismatch",
        )
    return executor_instances


def run_spark(invocation: Invocation, context: RuntimeContext) -> SparkResult:
    """Execute one validated source-to-transform-to-BigQuery graph."""
    try:
        from pyspark.sql import SparkSession, functions  # type: ignore[import-not-found]
    except ImportError as error:
        raise SparkDriverError(
            "the managed Spark runtime is unavailable",
            failure_code="spark_runtime_unavailable",
        ) from error
    try:
        spark = SparkSession.builder.appName(invocation.pipeline_id).getOrCreate()
    except Exception as error:
        raise SparkDriverError(
            "the managed Spark session could not start",
            failure_code="spark_session_start_failed",
        ) from error
    dynamic_spark = cast("Any", spark)
    exchange_uri = _exchange_uri(invocation, context)
    exchange_created = False
    stage = "configuration"
    try:
        dynamic_spark.conf.set("temporaryGcsBucket", invocation.staging_bucket)
        configuration = read_runtime_configuration(dynamic_spark, context, invocation)
        executor_instances = _planned_executor_instances(
            dynamic_spark, configuration.exchange_partitions
        )
        graph = configuration.graph
        stage = "source_read"
        source = (
            dynamic_spark.read.format("bigquery")
            .option("table", graph.source_table)
            .option("parentProject", invocation.project)
            .load()
            .select(*graph.source_fields)
        )
        source_rows = int(source.count())
        stage = "exchange_write"
        source.repartition(configuration.exchange_partitions).write.mode("errorifexists").parquet(
            exchange_uri
        )
        exchange_created = True
        stage = "transform"
        exchanged = dynamic_spark.read.parquet(exchange_uri)
        transformed = _project_frame(
            exchanged,
            graph.source_to_transform,
            graph.transform_fields,
            functions,
        )
        published = cast(
            "Any",
            _project_frame(
                transformed,
                graph.transform_to_target,
                graph.target_fields,
                functions,
            ),
        )
        stage = "bigquery_write"
        (
            published.write.format("bigquery")
            .option("table", graph.output_table)
            .option("parentProject", invocation.project)
            .mode("overwrite")
            .save()
        )
        stage = "bigquery_readback"
        affected_rows = int(
            dynamic_spark.read.format("bigquery")
            .option("table", graph.output_table)
            .option("parentProject", invocation.project)
            .load()
            .count()
        )
        if affected_rows != source_rows:
            raise SparkDriverError(
                "the BigQuery round-trip row count does not match its source",
                failure_code="spark_readback_mismatch",
            )
        return SparkResult(
            source_id=graph.source_id,
            target_id=graph.target_id,
            output_table=graph.output_table,
            exchange_uri=exchange_uri,
            source_rows=source_rows,
            affected_rows=affected_rows,
            executor_instances=executor_instances,
            exchange_partitions=configuration.exchange_partitions,
        )
    except SparkDriverError:
        raise
    except Exception as error:
        raise SparkDriverError(
            "the bounded Spark and BigQuery execution failed",
            failure_code=f"spark_{stage}_failed",
        ) from error
    finally:
        cleanup_error: SparkDriverError | None = None
        if exchange_created:
            try:
                _delete_exchange(dynamic_spark, exchange_uri)
            except SparkDriverError as error:
                cleanup_error = error
        _stop_spark(dynamic_spark, context)
        if cleanup_error is not None:
            raise cleanup_error


def _project_frame(
    frame: object,
    mappings: tuple[DirectMapping, ...],
    fields: tuple[str, ...],
    functions: object,
) -> object:
    dynamic_frame = cast("Any", frame)
    dynamic_functions = cast("Any", functions)
    by_target = {mapping.target: mapping.source for mapping in mappings}
    return dynamic_frame.select(
        *(dynamic_functions.col(by_target[field]).alias(field) for field in fields)
    )


def completion_event(
    invocation: Invocation,
    context: RuntimeContext,
    result: SparkResult,
    *,
    duration_ms: int,
) -> dict[str, object]:
    """Build the canonical Dander runtime completion consumed by Control."""
    return {
        "contract": RUNTIME_CONTRACT,
        "event": "runtime.completed",
        "timestamp": _timestamp(),
        "run_id": context.run_id,
        "pipeline_id": invocation.pipeline_id,
        "platform": invocation.platform,
        "stage": "complete",
        "dimensions": _dimensions(invocation, context),
        "status": "succeeded",
        "outputs": {
            "source": result.source_id,
            "endpoints": [
                {
                    "name": result.source_id,
                    "extracted_rows": result.source_rows,
                    "affected_rows": result.affected_rows,
                    "cursor_committed": False,
                }
            ],
            "models": [result.target_id],
            "telemetry": {
                "duration_ms": duration_ms,
                "retry_count": 0,
                "rows_read": result.source_rows + result.affected_rows,
                "rows_written": result.affected_rows,
                "rows_affected": result.affected_rows,
                "bytes_read": 0,
                "bytes_written": 0,
                "bytes_processed": 0,
                "bytes_billed": 0,
                "queue_duration_ms": 0,
                "execution_duration_ms": duration_ms,
                "spill_bytes": 0,
                "operations": [
                    {"operation": result.source_id},
                    {"operation": "object_store_exchange"},
                    {"operation": result.target_id},
                    {"operation": "bigquery_readback"},
                ],
            },
            "metrics": {
                "endpoints": 1,
                "extracted_rows": result.source_rows,
                "affected_rows": result.affected_rows,
                "models": 1,
                "assertions": 1,
                "assets": 1,
            },
        },
        "retryable": False,
    }


def main(arguments: list[str] | None = None) -> int:
    """Run one bounded graph while exposing only sanitized contract failures."""
    started_ns = time.monotonic_ns()
    try:
        invocation = parse_invocation(list(sys.argv[1:] if arguments is None else arguments))
        context = runtime_context(invocation)
        validate_driver_pair(invocation.driver_sha256)
    except SparkDriverError:
        return 2
    print(_json(_started_event(invocation, context)), flush=True)
    try:
        result = run_spark(invocation, context)
    except SparkDriverError as error:
        duration_ms = _elapsed_ms(started_ns)
        print(
            _json(
                _failed_event(
                    invocation,
                    context,
                    duration_ms=duration_ms,
                    failure_code=error.failure_code,
                )
            ),
            flush=True,
        )
        return 1
    duration_ms = _elapsed_ms(started_ns)
    print(_json(_spark_event(invocation, context, result)), flush=True)
    print(
        _json(completion_event(invocation, context, result, duration_ms=duration_ms)),
        flush=True,
    )
    return 0


def _started_event(invocation: Invocation, context: RuntimeContext) -> dict[str, object]:
    return {
        "contract": RUNTIME_CONTRACT,
        "event": "runtime.started",
        "timestamp": _timestamp(),
        "run_id": context.run_id,
        "pipeline_id": invocation.pipeline_id,
        "platform": invocation.platform,
        "stage": "starting",
        "dimensions": _dimensions(invocation, context),
    }


def _failed_event(
    invocation: Invocation,
    context: RuntimeContext,
    *,
    duration_ms: int,
    failure_code: str,
) -> dict[str, object]:
    return {
        "contract": RUNTIME_CONTRACT,
        "event": "runtime.completed",
        "timestamp": _timestamp(),
        "run_id": context.run_id,
        "pipeline_id": invocation.pipeline_id,
        "platform": invocation.platform,
        "stage": "runtime",
        "dimensions": _dimensions(invocation, context),
        "status": "failed",
        "outputs": {"telemetry": {"duration_ms": duration_ms}},
        "failure_code": failure_code,
        "retryable": False,
    }


def _spark_event(
    invocation: Invocation,
    context: RuntimeContext,
    result: SparkResult,
) -> dict[str, object]:
    return {
        "contract": SPARK_RUNTIME_CONTRACT,
        "event": "spark.runtime.completed",
        "run_id": context.run_id,
        "graph_content_sha256": invocation.graph_content_sha256,
        "physical_plan_revision": invocation.physical_plan_revision,
        "driver_sha256": invocation.driver_sha256,
        "source_id": result.source_id,
        "target_id": result.target_id,
        "output_table": result.output_table,
        "source_rows": result.source_rows,
        "affected_rows": result.affected_rows,
        "executor_instances": result.executor_instances,
        "exchange_partitions": result.exchange_partitions,
        "exchange_cleanup": "confirmed",
    }


def _dimensions(invocation: Invocation, context: RuntimeContext) -> dict[str, object]:
    return {
        "launcher": "dataproc_serverless",
        "launcher_execution_id": context.launcher_execution_id,
        "attempt": context.attempt,
        "shard_index": 0,
        "shard_count": 1,
        "principal": context.principal,
        "graph_content_sha256": invocation.graph_content_sha256,
        "physical_plan_revision": invocation.physical_plan_revision,
    }


def _exchange_uri(invocation: Invocation, context: RuntimeContext) -> str:
    return (
        f"gs://{invocation.staging_bucket}/dander-spark/exchanges/"
        f"{context.run_id}/attempt-{context.attempt}/extract-transform"
    )


def _delete_exchange(spark: object, uri: str) -> None:
    dynamic_spark = cast("Any", spark)
    filesystem: Any | None = None
    path: Any | None = None
    try:
        hadoop_configuration = dynamic_spark.sparkContext._jsc.hadoopConfiguration()
        path = dynamic_spark._jvm.org.apache.hadoop.fs.Path(uri)
        filesystem = path.getFileSystem(hadoop_configuration)
        filesystem.delete(path, True)
        if filesystem.exists(path):
            raise SparkDriverError(
                "the object-store exchange cleanup did not converge",
                failure_code="spark_exchange_cleanup_incomplete",
            )
    except SparkDriverError:
        raise
    except Exception as error:
        if filesystem is not None and path is not None:
            try:
                if not filesystem.exists(path):
                    return
            except Exception:
                pass
        raise SparkDriverError(
            "the object-store exchange cleanup failed",
            failure_code="spark_exchange_cleanup_failed",
        ) from error


def _stop_spark(spark: object, context: RuntimeContext) -> None:
    dynamic_spark = cast("Any", spark)
    try:
        dynamic_spark.stop()
    except Exception:
        print(
            _json(
                {
                    "contract": SPARK_RUNTIME_CONTRACT,
                    "event": "spark.session.stop.deferred",
                    "run_id": context.run_id,
                    "cleanup_owner": "managed_spark",
                }
            ),
            flush=True,
        )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _elapsed_ms(started_ns: int) -> int:
    return max(0, (time.monotonic_ns() - started_ns) // 1_000_000)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _json(value: object) -> str:
    return _canonical_json(value).decode()


if __name__ == "__main__":
    raise SystemExit(main())
