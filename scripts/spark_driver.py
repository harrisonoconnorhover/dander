#!/usr/bin/env python3
"""Content-addressed fixed-plan Spark/BigQuery qualification driver."""

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
QUALIFICATION_CONTRACT = "io.dander.spark-qualification/v1"
PIPELINE_ID = "spark_bigquery_qualification"
_DRIVER_PATH = Path("/opt/dander/spark_driver.py")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}$")
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])"
    r"\.iam\.gserviceaccount\.com$"
)
_EXECUTION = re.compile(
    r"^projects/(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])/locations/"
    r"[a-z]+(?:-[a-z0-9]+)+[0-9]/batches/dander-[0-9a-f]{40}$"
)
_EXPECTED_ROWS = 4
_EXPECTED_VALUE_SUM = 17
_EXPECTED_DOUBLED_SUM = 34


class SparkDriverError(RuntimeError):
    """The immutable driver contract or live Spark result is invalid."""


@dataclass(frozen=True, slots=True)
class Invocation:
    """Validated arguments covered by the immutable execution plan."""

    project: str
    dataset: str
    staging_bucket: str
    platform: str
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
class SparkResult:
    """Bounded aggregates retained from the live Spark and BigQuery round-trip."""

    output_table: str
    exchange_uri: str
    row_count: int
    value_sum: int
    doubled_sum: int
    executor_instances: int
    exchange_partitions: int


def parse_invocation(arguments: list[str]) -> Invocation:
    """Parse the unchanged Dander command prefix plus qualification-only arguments."""
    if arguments[:2] != ["runtime", "execute"]:
        raise SparkDriverError("the driver requires the Dander runtime execute command")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--physical-plan", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--driver-sha256", required=True)
    try:
        values, unknown = parser.parse_known_args(arguments[2:])
    except SystemExit as error:
        raise SparkDriverError("the driver arguments are incomplete") from error
    if unknown:
        raise SparkDriverError("the driver arguments contain unsupported options")
    if values.contract != RUNTIME_CONTRACT or values.pipeline != PIPELINE_ID:
        raise SparkDriverError("the driver runtime identity is invalid")
    if values.platform != "gcp":
        raise SparkDriverError("the driver requires the GCP profile")
    if _PROJECT.fullmatch(values.project) is None:
        raise SparkDriverError("the driver GCP project is invalid")
    if _DATASET.fullmatch(values.dataset) is None:
        raise SparkDriverError("the driver BigQuery dataset is invalid")
    if _BUCKET.fullmatch(values.staging_bucket) is None:
        raise SparkDriverError("the driver staging bucket is invalid")
    if _SHA256.fullmatch(values.driver_sha256) is None:
        raise SparkDriverError("the driver content identity is invalid")
    plan, revision = _physical_plan(values.physical_plan)
    return Invocation(
        project=values.project,
        dataset=values.dataset,
        staging_bucket=values.staging_bucket,
        platform=values.platform,
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
    configuration_prefix = f"gs://{invocation.staging_bucket}/"
    if not configuration.startswith(configuration_prefix) or len(configuration) > 1024:
        raise SparkDriverError("the driver configuration reference is invalid")
    if secret_bindings not in {None, "{}"}:
        raise SparkDriverError("the qualification driver does not accept secret bindings")
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


def _physical_plan(raw: str) -> tuple[dict[str, object], str]:
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
    _validate_qualification_plan(plan)
    return cast("dict[str, object]", plan), derived


def _validate_qualification_plan(plan: dict[str, object]) -> None:
    expected = {
        "pipeline_id": PIPELINE_ID,
        "execution_mode": "distributed",
        "stages": [
            {
                "stage_id": "extract",
                "operators": ["spark_seed"],
                "partition_count": 2,
                "depends_on": [],
            },
            {
                "stage_id": "publish",
                "operators": ["bigquery_publish"],
                "partition_count": 2,
                "depends_on": ["extract"],
            },
        ],
        "exchanges": [
            {
                "exchange_id": "extract-publish",
                "producer_stage_id": "extract",
                "consumer_stage_id": "publish",
                "transport": "object_store",
                "partitioning": "round_robin",
                "partition_count": 2,
                "partition_keys": [],
            }
        ],
        "maximum_parallelism": 2,
    }
    if plan != expected:
        raise SparkDriverError("the driver supports only its fixed qualification physical plan")


def run_spark(invocation: Invocation, context: RuntimeContext) -> SparkResult:
    """Execute the fixed two-stage Spark plan and verify its BigQuery output."""
    try:
        from pyspark.sql import SparkSession, functions  # type: ignore[import-not-found]
        from pyspark.sql.types import (  # type: ignore[import-not-found]
            IntegerType,
            StringType,
            StructField,
            StructType,
        )
    except ImportError as error:
        raise SparkDriverError("the managed Spark runtime is unavailable") from error

    try:
        spark = SparkSession.builder.appName(PIPELINE_ID).getOrCreate()
    except Exception as error:
        raise SparkDriverError("the managed Spark session could not start") from error
    dynamic_spark = cast("Any", spark)
    exchange_uri = _exchange_uri(invocation, context)
    output_table = _output_table(invocation, context)
    exchange_created = False
    try:
        dynamic_spark.conf.set("temporaryGcsBucket", invocation.staging_bucket)
        dynamic_allocation = dynamic_spark.conf.get("spark.dynamicAllocation.enabled")
        executor_instances = int(dynamic_spark.conf.get("spark.executor.instances"))
        if dynamic_allocation.casefold() != "false" or executor_instances != 2:
            raise SparkDriverError("the managed Spark runtime changed the fixed executor plan")
        schema = StructType(
            [
                StructField("id", IntegerType(), nullable=False),
                StructField("label", StringType(), nullable=False),
                StructField("value", IntegerType(), nullable=False),
            ]
        )
        rows = ((1, "alpha", 2), (2, "beta", 3), (3, "gamma", 5), (4, "delta", 7))
        source = dynamic_spark.createDataFrame(
            dynamic_spark.sparkContext.parallelize(rows, 2),
            schema=schema,
        )
        source.repartition(2).write.mode("errorifexists").parquet(exchange_uri)
        exchange_created = True
        published = (
            dynamic_spark.read.parquet(exchange_uri)
            .withColumn("doubled_value", functions.col("value") * 2)
            .withColumn("dander_run_id", functions.lit(context.run_id))
            .select("id", "label", "value", "doubled_value", "dander_run_id")
        )
        (
            published.write.format("bigquery")
            .option("table", output_table)
            .option("parentProject", invocation.project)
            .mode("overwrite")
            .save()
        )
        observed = (
            dynamic_spark.read.format("bigquery")
            .option("table", output_table)
            .option("parentProject", invocation.project)
            .load()
        )
        aggregate = observed.agg(
            functions.count("*").alias("row_count"),
            functions.sum("value").alias("value_sum"),
            functions.sum("doubled_value").alias("doubled_sum"),
        ).collect()[0]
        result = SparkResult(
            output_table=output_table,
            exchange_uri=exchange_uri,
            row_count=int(aggregate["row_count"]),
            value_sum=int(aggregate["value_sum"]),
            doubled_sum=int(aggregate["doubled_sum"]),
            executor_instances=executor_instances,
            exchange_partitions=2,
        )
        if (
            result.row_count != _EXPECTED_ROWS
            or result.value_sum != _EXPECTED_VALUE_SUM
            or result.doubled_sum != _EXPECTED_DOUBLED_SUM
        ):
            raise SparkDriverError("the BigQuery round-trip returned unexpected aggregates")
        return result
    except SparkDriverError:
        raise
    except Exception as error:
        raise SparkDriverError("the Spark and BigQuery qualification failed") from error
    finally:
        cleanup_error: SparkDriverError | None = None
        if exchange_created:
            try:
                _delete_exchange(dynamic_spark, exchange_uri)
            except SparkDriverError as error:
                cleanup_error = error
        try:
            dynamic_spark.stop()
        except Exception:
            if cleanup_error is None:
                cleanup_error = SparkDriverError("the managed Spark session did not stop cleanly")
        if cleanup_error is not None:
            raise cleanup_error


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
        "pipeline_id": PIPELINE_ID,
        "platform": invocation.platform,
        "stage": "complete",
        "dimensions": _dimensions(invocation, context),
        "status": "succeeded",
        "outputs": {
            "source": "spark_qualification",
            "endpoints": [
                {
                    "name": "spark_seed",
                    "extracted_rows": result.row_count,
                    "affected_rows": result.row_count,
                    "cursor_committed": False,
                }
            ],
            "models": ["bigquery_publish"],
            "telemetry": {
                "duration_ms": duration_ms,
                "retry_count": 0,
                "rows_read": result.row_count * 2,
                "rows_written": result.row_count * 2,
                "rows_affected": result.row_count,
                "bytes_read": 0,
                "bytes_written": 0,
                "bytes_processed": 0,
                "bytes_billed": 0,
                "queue_duration_ms": 0,
                "execution_duration_ms": duration_ms,
                "spill_bytes": 0,
                "operations": [
                    {"operation": "spark_seed"},
                    {"operation": "object_store_exchange"},
                    {"operation": "bigquery_publish"},
                    {"operation": "bigquery_readback"},
                ],
            },
            "metrics": {
                "endpoints": 1,
                "extracted_rows": result.row_count,
                "affected_rows": result.row_count,
                "models": 1,
                "assertions": 3,
                "assets": 1,
            },
        },
        "retryable": False,
    }


def main(arguments: list[str] | None = None) -> int:
    """Run one qualification while exposing only sanitized contract failures."""
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
    except SparkDriverError:
        duration_ms = _elapsed_ms(started_ns)
        print(_json(_failed_event(invocation, context, duration_ms=duration_ms)), flush=True)
        return 1
    duration_ms = _elapsed_ms(started_ns)
    print(_json(_qualification_event(invocation, context, result)), flush=True)
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
        "pipeline_id": PIPELINE_ID,
        "platform": invocation.platform,
        "stage": "starting",
        "dimensions": _dimensions(invocation, context),
    }


def _failed_event(
    invocation: Invocation,
    context: RuntimeContext,
    *,
    duration_ms: int,
) -> dict[str, object]:
    return {
        "contract": RUNTIME_CONTRACT,
        "event": "runtime.completed",
        "timestamp": _timestamp(),
        "run_id": context.run_id,
        "pipeline_id": PIPELINE_ID,
        "platform": invocation.platform,
        "stage": "runtime",
        "dimensions": _dimensions(invocation, context),
        "status": "failed",
        "outputs": {"telemetry": {"duration_ms": duration_ms}},
        "failure_code": "spark_qualification_failed",
        "retryable": False,
    }


def _qualification_event(
    invocation: Invocation,
    context: RuntimeContext,
    result: SparkResult,
) -> dict[str, object]:
    return {
        "contract": QUALIFICATION_CONTRACT,
        "event": "spark.qualification.completed",
        "run_id": context.run_id,
        "physical_plan_revision": invocation.physical_plan_revision,
        "driver_sha256": invocation.driver_sha256,
        "output_table": result.output_table,
        "row_count": result.row_count,
        "value_sum": result.value_sum,
        "doubled_sum": result.doubled_sum,
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
        "physical_plan_revision": invocation.physical_plan_revision,
    }


def _exchange_uri(invocation: Invocation, context: RuntimeContext) -> str:
    return (
        f"gs://{invocation.staging_bucket}/dander-spark-qualification/exchanges/"
        f"{context.run_id}/attempt-{context.attempt}/extract-publish"
    )


def _output_table(invocation: Invocation, context: RuntimeContext) -> str:
    suffix = hashlib.sha256(context.run_id.encode()).hexdigest()[:16]
    return f"{invocation.project}.{invocation.dataset}.dander_spark_qual_{suffix}"


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
            raise SparkDriverError("the object-store exchange cleanup did not converge")
    except SparkDriverError:
        raise
    except Exception as error:
        if filesystem is not None and path is not None:
            try:
                if not filesystem.exists(path):
                    return
            except Exception:
                pass
        raise SparkDriverError("the object-store exchange cleanup failed") from error


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
