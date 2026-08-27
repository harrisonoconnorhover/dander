"""Contracts for the content-addressed Managed Spark qualification driver."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from scripts import spark_driver as driver

from dander.control.execution_results import parse_execution_result_summary
from dander.physical_plan import (
    ExchangeTransport,
    PartitioningStrategy,
    PhysicalExchange,
    PhysicalExecutionMode,
    PhysicalPlan,
    PhysicalStage,
    serialize_physical_plan,
)

if TYPE_CHECKING:
    from pathlib import Path


def _physical_plan() -> PhysicalPlan:
    return PhysicalPlan(
        pipeline_id=driver.PIPELINE_ID,
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        stages=(
            PhysicalStage(
                stage_id="extract",
                operators=("spark_seed",),
                partition_count=2,
            ),
            PhysicalStage(
                stage_id="publish",
                operators=("bigquery_publish",),
                partition_count=2,
                depends_on=("extract",),
            ),
        ),
        exchanges=(
            PhysicalExchange(
                exchange_id="extract-publish",
                producer_stage_id="extract",
                consumer_stage_id="publish",
                transport=ExchangeTransport.OBJECT_STORE,
                partitioning=PartitioningStrategy.ROUND_ROBIN,
                partition_count=2,
            ),
        ),
        maximum_parallelism=2,
    )


def _arguments(*, physical_plan: str | None = None) -> list[str]:
    return [
        "runtime",
        "execute",
        "--contract",
        driver.RUNTIME_CONTRACT,
        "--pipeline",
        driver.PIPELINE_ID,
        "--platform",
        "gcp",
        "--physical-plan",
        physical_plan or serialize_physical_plan(_physical_plan()).decode(),
        "--project",
        "dander-cr-20260802-7f3a",
        "--dataset",
        "dander_spark_qualification",
        "--staging-bucket",
        "dander-cr-20260802-7f3a-spark-qualification",
        "--driver-sha256",
        "a" * 64,
    ]


def _environment() -> dict[str, str]:
    return {
        "DANDER_RUN_ID": "run-0123456789abcdef01234567",
        "DANDER_LAUNCHER": "dataproc_serverless",
        "DANDER_LAUNCHER_EXECUTION_ID": (
            "projects/dander-cr-20260802-7f3a/locations/us-central1/batches/"
            "dander-0123456789abcdef0123456789abcdef01234567"
        ),
        "DANDER_ATTEMPT": "1",
        "DANDER_SHARD_INDEX": "0",
        "DANDER_SHARD_COUNT": "1",
        "DANDER_PRINCIPAL": ("dander-spark@dander-cr-20260802-7f3a.iam.gserviceaccount.com"),
        "DANDER_CONFIGURATION_REFERENCE": (
            "gs://dander-cr-20260802-7f3a-spark-qualification/config/" + "b" * 64 + ".json"
        ),
    }


def test_driver_accepts_only_the_canonical_fixed_plan_and_control_context() -> None:
    invocation = driver.parse_invocation(_arguments())
    context = driver.runtime_context(invocation, _environment())

    assert invocation.physical_plan_revision == _physical_plan().revision
    assert context.attempt == 1
    assert context.principal.startswith("dander-spark@")

    tampered = json.loads(serialize_physical_plan(_physical_plan()))
    tampered["plan"]["maximum_parallelism"] = 3
    with pytest.raises(driver.SparkDriverError, match="self-verifying"):
        driver.parse_invocation(
            _arguments(physical_plan=json.dumps(tampered, sort_keys=True, separators=(",", ":")))
        )


def test_driver_requires_byte_identical_submitted_and_embedded_pair(tmp_path: Path) -> None:
    submitted = tmp_path / "submitted.py"
    embedded = tmp_path / "embedded.py"
    submitted.write_bytes(b"driver contents\n")
    embedded.write_bytes(submitted.read_bytes())
    digest = hashlib.sha256(submitted.read_bytes()).hexdigest()

    driver.validate_driver_pair(
        digest,
        submitted_driver=submitted,
        embedded_driver=embedded,
    )
    embedded.write_bytes(b"different\n")
    with pytest.raises(driver.SparkDriverError, match="does not match"):
        driver.validate_driver_pair(
            digest,
            submitted_driver=submitted,
            embedded_driver=embedded,
        )


def test_driver_completion_is_consumed_by_control_without_provider_fields() -> None:
    invocation = driver.parse_invocation(_arguments())
    context = driver.runtime_context(invocation, _environment())
    result = driver.SparkResult(
        output_table=(
            "dander-cr-20260802-7f3a.dander_spark_qualification.dander_spark_qual_deadbeef"
        ),
        exchange_uri=(
            "gs://dander-cr-20260802-7f3a-spark-qualification/"
            "dander-spark-qualification/exchanges/run/attempt-1/extract-publish"
        ),
        row_count=4,
        value_sum=17,
        doubled_sum=34,
        executor_instances=2,
        exchange_partitions=2,
    )

    message = json.dumps(
        driver.completion_event(invocation, context, result, duration_ms=1_234),
        sort_keys=True,
        separators=(",", ":"),
    )
    summary = parse_execution_result_summary(message, pipeline_id=driver.PIPELINE_ID)

    assert summary is not None
    assert summary.extracted_rows == 4
    assert summary.affected_rows == 4
    assert summary.models == 1
    assert summary.assertions == 3
    assert summary.operation_count == 4


def test_exchange_cleanup_accepts_a_verified_postcondition_after_delete_error() -> None:
    spark = MagicMock()
    path = spark._jvm.org.apache.hadoop.fs.Path.return_value
    filesystem = path.getFileSystem.return_value
    filesystem.delete.side_effect = OSError("provider detail")
    filesystem.exists.return_value = False

    driver._delete_exchange(spark, "gs://qualification/exchange")

    filesystem.exists.assert_called_once_with(path)


def test_exchange_cleanup_fails_when_the_exchange_still_exists() -> None:
    spark = MagicMock()
    path = spark._jvm.org.apache.hadoop.fs.Path.return_value
    filesystem = path.getFileSystem.return_value
    filesystem.delete.return_value = True
    filesystem.exists.return_value = True

    with pytest.raises(driver.SparkDriverError, match="did not converge"):
        driver._delete_exchange(spark, "gs://qualification/exchange")


def test_spark_stop_failure_is_visible_and_deferred_to_the_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    invocation = driver.parse_invocation(_arguments())
    context = driver.runtime_context(invocation, _environment())
    spark = MagicMock()
    spark.stop.side_effect = OSError("provider detail")

    driver._stop_spark(spark, context)

    event = json.loads(capsys.readouterr().out)
    assert event == {
        "cleanup_owner": "managed_spark",
        "contract": driver.QUALIFICATION_CONTRACT,
        "event": "spark.session.stop.deferred",
        "run_id": context.run_id,
    }


def test_driver_aggregates_only_the_bounded_bigquery_readback() -> None:
    rows = [
        {"value": 2, "doubled_value": 4},
        {"value": 3, "doubled_value": 6},
        {"value": 5, "doubled_value": 10},
        {"value": 7, "doubled_value": 14},
    ]

    assert driver._bounded_readback_aggregates(rows) == (4, 17, 34)

    with pytest.raises(driver.SparkDriverError, match="readback rows are invalid"):
        driver._bounded_readback_aggregates([{"value": 2}])
