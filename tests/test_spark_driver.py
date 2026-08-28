"""Contracts for the content-addressed bounded Spark graph runtime."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import yaml
from scripts import spark_driver as driver

from dander.control.execution_results import parse_execution_result_summary
from dander.control.graph_store import CanonicalGraphDocument, canonicalize_graph_document
from dander.control.physical_planner import StaticPhysicalPlanner
from dander.physical_plan import PhysicalExecutionMode, serialize_physical_plan

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "dander-cr-20260802-7f3a"
_PIPELINE = "greenhouse_jobs_graph"


def _canonical_graph() -> CanonicalGraphDocument:
    with open("graphs/greenhouse_jobs.yaml", encoding="utf-8") as stream:
        return canonicalize_graph_document(yaml.safe_load(stream))


def _physical_plan(
    *,
    maximum_parallelism: int | None = None,
    distributed_partitions: int = 2,
) -> str:
    canonical = _canonical_graph()
    plan = StaticPhysicalPlanner().plan(
        canonical.document,
        pipeline_id=_PIPELINE,
        execution_mode=PhysicalExecutionMode.DISTRIBUTED,
        distributed_partitions=distributed_partitions,
    )
    if maximum_parallelism is None:
        return serialize_physical_plan(plan).decode()
    envelope = json.loads(serialize_physical_plan(plan))
    envelope["plan"]["maximum_parallelism"] = maximum_parallelism
    contents = driver._canonical_json(
        {"schema": driver.PHYSICAL_PLAN_SCHEMA, "plan": envelope["plan"]}
    )
    envelope["revision"] = hashlib.sha256(contents).hexdigest()
    return driver._canonical_json(envelope).decode()


def _arguments(
    *,
    physical_plan: str | None = None,
    graph_content_sha256: str | None = None,
) -> list[str]:
    canonical = _canonical_graph()
    return [
        "runtime",
        "execute",
        "--contract",
        driver.RUNTIME_CONTRACT,
        "--pipeline",
        _PIPELINE,
        "--platform",
        "gcp",
        "--physical-plan",
        physical_plan or _physical_plan(),
        "--project",
        _PROJECT,
        "--staging-bucket",
        f"{_PROJECT}-spark-qualification",
        "--graph-content-sha256",
        graph_content_sha256 or canonical.content_sha256,
        "--driver-sha256",
        "a" * 64,
    ]


def _configuration_payload(
    *,
    graph: dict[str, object] | None = None,
) -> dict[str, object]:
    canonical = _canonical_graph()
    graph_payload = json.loads(canonical.data) if graph is None else graph
    graph_sha256 = hashlib.sha256(driver._canonical_json(graph_payload)).hexdigest()
    return {
        "schema": driver.CONFIGURATION_SCHEMA,
        "graph_content_sha256": graph_sha256,
        "graph": graph_payload,
        "source_relations": {
            "greenhouse_jobs": f"{_PROJECT}.raw.greenhouse_job_board_jobs",
        },
    }


def _configuration_bytes(*, graph: dict[str, object] | None = None) -> bytes:
    return driver._canonical_json(_configuration_payload(graph=graph))


def _environment(*, configuration_sha256: str = "b" * 64) -> dict[str, str]:
    return {
        "DANDER_RUN_ID": "run-0123456789abcdef01234567",
        "DANDER_LAUNCHER": "dataproc_serverless",
        "DANDER_LAUNCHER_EXECUTION_ID": (
            f"projects/{_PROJECT}/locations/us-central1/batches/"
            "dander-0123456789abcdef0123456789abcdef01234567"
        ),
        "DANDER_ATTEMPT": "1",
        "DANDER_SHARD_INDEX": "0",
        "DANDER_SHARD_COUNT": "1",
        "DANDER_PRINCIPAL": f"dander-spark@{_PROJECT}.iam.gserviceaccount.com",
        "DANDER_CONFIGURATION_REFERENCE": (
            f"gs://{_PROJECT}-spark-qualification/config/{configuration_sha256}.json"
        ),
    }


def test_driver_accepts_the_canonical_linear_plan_and_control_context() -> None:
    invocation = driver.parse_invocation(_arguments())
    context = driver.runtime_context(invocation, _environment())
    configuration = driver.parse_runtime_configuration(_configuration_bytes(), invocation)

    assert invocation.pipeline_id == _PIPELINE
    assert invocation.physical_plan_revision == json.loads(_physical_plan())["revision"]
    assert context.attempt == 1
    assert configuration.graph.source_id == "greenhouse_jobs"
    assert configuration.graph.transform_id == "prepared_jobs"
    assert configuration.graph.target_id == "curated_jobs"
    assert configuration.graph.output_table == f"{_PROJECT}.staging.graph_greenhouse_jobs"
    assert configuration.exchange_partitions == 2


def test_driver_accepts_a_larger_revision_covered_static_executor_shape() -> None:
    invocation = driver.parse_invocation(
        _arguments(physical_plan=_physical_plan(distributed_partitions=4))
    )

    configuration = driver.parse_runtime_configuration(_configuration_bytes(), invocation)

    assert configuration.exchange_partitions == 4


def test_driver_requires_exact_planned_executors_with_dynamic_allocation_off() -> None:
    spark = MagicMock()
    spark.conf.get.side_effect = lambda name: {
        "spark.dynamicAllocation.enabled": "false",
        "spark.executor.instances": "4",
    }[name]

    assert driver._planned_executor_instances(spark, 4) == 4

    spark.conf.get.side_effect = lambda name: {
        "spark.dynamicAllocation.enabled": "false",
        "spark.executor.instances": "2",
    }[name]
    with pytest.raises(driver.SparkDriverError, match="planned executor shape"):
        driver._planned_executor_instances(spark, 4)


def test_driver_binds_configuration_to_control_graph_and_physical_plan() -> None:
    canonical = _canonical_graph()
    invocation = driver.parse_invocation(_arguments())
    payload = _configuration_payload()
    payload["graph_content_sha256"] = "b" * 64

    with pytest.raises(driver.SparkDriverError, match="graph identity"):
        driver.parse_runtime_configuration(driver._canonical_json(payload), invocation)

    wrong_plan = driver.parse_invocation(
        _arguments(physical_plan=_physical_plan(maximum_parallelism=3))
    )
    with pytest.raises(driver.SparkDriverError, match="physical plan does not match"):
        driver.parse_runtime_configuration(_configuration_bytes(), wrong_plan)

    assert invocation.graph_content_sha256 == canonical.content_sha256


def test_driver_fails_closed_outside_direct_mapping_subset() -> None:
    graph = json.loads(_canonical_graph().data)
    graph["nodes"][1]["config"]["operations"] = [
        {"kind": "trim_whitespace", "params": {"field": "title"}}
    ]
    graph_sha256 = hashlib.sha256(driver._canonical_json(graph)).hexdigest()
    invocation = driver.parse_invocation(_arguments(graph_content_sha256=graph_sha256))

    with pytest.raises(driver.SparkDriverError, match="direct mappings without joins"):
        driver.parse_runtime_configuration(_configuration_bytes(graph=graph), invocation)


def test_driver_reads_one_content_addressed_configuration_object() -> None:
    raw = _configuration_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    invocation = driver.parse_invocation(_arguments())
    context = driver.runtime_context(invocation, _environment(configuration_sha256=digest))
    spark = MagicMock()
    spark.read.text.return_value.take.return_value = [{"value": raw.decode()}]

    configuration = driver.read_runtime_configuration(spark, context, invocation)

    assert configuration.content_sha256 == digest
    spark.read.text.assert_called_once_with(context.configuration_reference)


def test_driver_requires_byte_identical_submitted_and_embedded_pair(tmp_path: Path) -> None:
    submitted = tmp_path / "submitted.py"
    embedded = tmp_path / "embedded.py"
    submitted.write_bytes(b"driver contents\n")
    embedded.write_bytes(submitted.read_bytes())
    digest = hashlib.sha256(submitted.read_bytes()).hexdigest()

    driver.validate_driver_pair(digest, submitted_driver=submitted, embedded_driver=embedded)
    embedded.write_bytes(b"different\n")
    with pytest.raises(driver.SparkDriverError, match="does not match"):
        driver.validate_driver_pair(digest, submitted_driver=submitted, embedded_driver=embedded)


def test_driver_completion_is_consumed_by_control_without_provider_fields() -> None:
    invocation = driver.parse_invocation(_arguments())
    context = driver.runtime_context(invocation, _environment())
    result = driver.SparkResult(
        source_id="greenhouse_jobs",
        target_id="curated_jobs",
        output_table=f"{_PROJECT}.staging.graph_greenhouse_jobs",
        exchange_uri=(
            f"gs://{_PROJECT}-spark-qualification/dander-spark/exchanges/"
            "run/attempt-1/extract-transform"
        ),
        source_rows=4,
        affected_rows=4,
        executor_instances=2,
        exchange_partitions=2,
    )

    message = json.dumps(
        driver.completion_event(invocation, context, result, duration_ms=1_234),
        sort_keys=True,
        separators=(",", ":"),
    )
    summary = parse_execution_result_summary(message, pipeline_id=_PIPELINE)

    assert summary is not None
    assert summary.extracted_rows == 4
    assert summary.affected_rows == 4
    assert summary.models == 1
    assert summary.assertions == 1
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
        "contract": driver.SPARK_RUNTIME_CONTRACT,
        "event": "spark.session.stop.deferred",
        "run_id": context.run_id,
    }
