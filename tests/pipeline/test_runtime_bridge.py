"""Executable PipelineGraph-to-connector runtime bridge tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dander.concurrency import FencingToken
from dander.ingestion import Endpoint, RawField, SourceConfig, load_source_config
from dander.pipeline.graph import NodeField, PipelineGraph, load_graph_from_yaml
from dander.pipeline.runtime import GraphRuntimeError, plan_graph_execution
from dander.providers.bigquery.graph import BigQueryGraphRunner
from dander.warehouse import RelationRef

if TYPE_CHECKING:
    from collections.abc import Callable


class _Job:
    def result(self) -> object:
        return object()


class _Client:
    def __init__(self) -> None:
        self.queries: list[tuple[str, object | None]] = []
        self.deleted: list[str] = []

    def query(self, query: str, *, job_config: object | None = None) -> _Job:
        self.queries.append((query, job_config))
        return _Job()

    def delete_table(self, table: str, *, not_found_ok: bool = False) -> None:
        assert not_found_ok
        self.deleted.append(table)


class _Ownership:
    fence = FencingToken(
        lease_table="unit-project.dander_meta.pipeline_leases",
        pipeline_id="graph_pipeline",
        run_id="run-one",
        token=7,
    )

    def __init__(self) -> None:
        self.verifications = 0

    def verify(self) -> None:
        self.verifications += 1


def test_neutral_graph_planning_does_not_import_bigquery_runtime() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import dander.pipeline.runtime, dander.pipeline.compiler; "
                "assert 'google.cloud.bigquery' not in sys.modules; "
                "assert 'dander.providers.bigquery.graph' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _source_config() -> SourceConfig:
    return SourceConfig(
        name="greenhouse_job_board",
        base_url="https://example.test",
        auth_strategy="none",
        endpoints=[
            Endpoint(
                name="jobs",
                path="/jobs",
                primary_key=["id"],
                raw_schema=[
                    RawField(name="id", data_type="INT64"),
                    RawField(name="title", data_type="STRING"),
                    RawField(name="company_name", data_type="STRING"),
                    RawField(name="updated_at", data_type="TIMESTAMP"),
                ],
            ),
            Endpoint(
                name="offices",
                path="/offices",
                primary_key=["id"],
                raw_schema=[RawField(name="id", data_type="INT64")],
            ),
        ],
    )


def _graph(*, write_mode: str = "replace") -> PipelineGraph:
    return PipelineGraph.model_validate(
        {
            "name": "jobs",
            "nodes": [
                {
                    "id": "jobs",
                    "type": "source",
                    "name": "Jobs",
                    "config": {
                        "connector": "greenhouse_job_board",
                        "endpoint": "jobs",
                    },
                    "fields": [
                        {"name": "id", "type": "INT64"},
                        {"name": "title", "type": "STRING"},
                    ],
                },
                {
                    "id": "target",
                    "type": "target",
                    "name": "Target",
                    "config": {
                        "writer": {
                            "write_mode": write_mode,
                            "destination": {
                                "dataset": "staging",
                                "table": "graph_jobs",
                                "business_key": ["id"] if write_mode == "scd1" else [],
                            },
                        }
                    },
                    "fields": [
                        {"name": "id", "type": "INT64"},
                        {"name": "title", "type": "STRING"},
                    ],
                },
            ],
            "edges": [
                {
                    "from": "jobs",
                    "to": "target",
                    "mappings": [
                        {"source": "id", "target": "id"},
                        {"source": "title", "target": "title"},
                    ],
                }
            ],
        }
    )


def test_plan_binds_only_declared_connector_endpoint() -> None:
    plan = plan_graph_execution(
        _graph(),
        _source_config(),
        project="unit-project",
        dataset="raw",
    )

    assert plan.bindings.endpoint_names == ("jobs",)
    assert plan.bindings.source_relations == {
        "jobs": RelationRef(
            catalog="unit-project",
            namespace="raw",
            name="greenhouse_job_board_jobs",
        )
    }
    assert plan.targets[0].target.table == "graph_jobs"
    assert "`unit-project`.`raw`.`greenhouse_job_board_jobs`" in plan.targets[0].query


def test_plan_compiles_operations_for_the_post_ingestion_transform_stage() -> None:
    graph = PipelineGraph.model_validate(
        {
            "name": "jobs",
            "nodes": [
                {
                    "id": "jobs",
                    "type": "source",
                    "name": "Jobs",
                    "config": {
                        "connector": "greenhouse_job_board",
                        "endpoint": "jobs",
                    },
                    "fields": [
                        {"name": "id", "type": "INT64"},
                        {"name": "title", "type": "STRING"},
                    ],
                },
                {
                    "id": "clean",
                    "type": "transform",
                    "name": "Clean",
                    "config": {
                        "operations": [
                            {"kind": "trim_whitespace", "params": {"field": "title"}},
                            {
                                "kind": "filter_rows",
                                "params": {
                                    "conditions": [
                                        {"field": "title", "op": "is_not_null"},
                                    ]
                                },
                            },
                        ]
                    },
                    "fields": [
                        {"name": "id", "type": "INT64"},
                        {"name": "title", "type": "STRING"},
                    ],
                },
                {
                    "id": "target",
                    "type": "target",
                    "name": "Target",
                    "config": {
                        "writer": {
                            "write_mode": "replace",
                            "destination": {
                                "dataset": "staging",
                                "table": "graph_jobs",
                                "business_key": [],
                            },
                        }
                    },
                    "fields": [
                        {"name": "id", "type": "INT64"},
                        {"name": "title", "type": "STRING"},
                    ],
                },
            ],
            "edges": [
                {
                    "from": "jobs",
                    "to": "clean",
                    "mappings": [
                        {"source": "id", "target": "id"},
                        {"source": "title", "target": "title"},
                    ],
                },
                {
                    "from": "clean",
                    "to": "target",
                    "mappings": [
                        {"source": "id", "target": "id"},
                        {"source": "title", "target": "title"},
                    ],
                },
            ],
        }
    )

    plan = plan_graph_execution(
        graph,
        _source_config(),
        project="unit-project",
        dataset="raw",
    )

    query = plan.targets[0].query
    assert "FROM `unit-project`.`raw`.`greenhouse_job_board_jobs`" in query
    assert "TRIM(source.`title`) AS `title`" in query
    assert "NOT source.`title` IS NULL" in query


def test_plan_fails_closed_for_writer_mode_not_yet_executable() -> None:
    with pytest.raises(GraphRuntimeError, match="unsupported graph write mode 'scd1'"):
        plan_graph_execution(
            _graph(write_mode="scd1"),
            _source_config(),
            project="unit-project",
            dataset="raw",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda graph: setattr(graph.nodes[0].config, "endpoint", "missing"),
            "unknown endpoint",
        ),
        (
            lambda graph: graph.nodes[0].fields.append(NodeField(name="missing", type="STRING")),
            "undeclared endpoint field",
        ),
    ],
)
def test_plan_rejects_invalid_connector_binding(
    mutation: Callable[[PipelineGraph], None],
    message: str,
) -> None:
    graph = _graph()
    mutation(graph)

    with pytest.raises(GraphRuntimeError, match=message):
        plan_graph_execution(
            graph,
            _source_config(),
            project="unit-project",
            dataset="raw",
        )


def test_runner_stages_then_uses_fenced_transactional_replacement() -> None:
    plan = plan_graph_execution(
        _graph(),
        _source_config(),
        project="unit-project",
        dataset="raw",
    )
    client = _Client()
    ownership = _Ownership()

    result = BigQueryGraphRunner(
        plan=plan,
        project="unit-project",
        client=client,
    ).build(Path("."), ownership=ownership)

    assert result.models == ("target",)
    assert ownership.verifications == 2
    assert client.queries[0][0].startswith("CREATE TABLE `unit-project.staging._dander_stage")
    assert "expiration_timestamp" in client.queries[0][0]
    finalizer, job_config = client.queries[-1]
    assert "BEGIN TRANSACTION" in finalizer
    assert "UPDATE `unit-project.dander_meta.pipeline_leases`" in finalizer
    assert "DELETE FROM `unit-project.staging.graph_jobs` WHERE TRUE" in finalizer
    assert "INSERT INTO `unit-project.staging.graph_jobs` (`id`, `title`)" in finalizer
    assert job_config is not None
    assert len(client.deleted) == 1
    assert client.deleted[0].startswith("unit-project.staging._dander_stage_graph_jobs_")


def test_greenhouse_linear_fixture_keeps_the_fused_bigquery_path() -> None:
    graph = load_graph_from_yaml(Path("graphs/greenhouse_jobs.yaml"))

    plan = plan_graph_execution(
        graph,
        _source_config(),
        project="unit-project",
        dataset="raw",
    )
    result = BigQueryGraphRunner(plan=plan, project="unit-project", client=_Client()).build(
        Path("."),
        ownership=_Ownership(),
    )

    assert plan.targets[0].node_id == "curated_jobs"
    assert plan.targets[0].target.table == "graph_greenhouse_jobs"
    assert "FROM `unit-project`.`raw`.`greenhouse_job_board_jobs`" in plan.targets[0].query
    assert "SELECT *" not in plan.targets[0].query
    assert result.models == ("curated_jobs",)


def test_keyed_join_fixture_compiles_through_the_existing_fused_bigquery_path() -> None:
    graph = load_graph_from_yaml(Path("graphs/keyed_join_qualification.yaml"))
    source = load_source_config(Path("connectors/keyed_join_fixture.yaml"))

    plan = plan_graph_execution(
        graph,
        source,
        project="unit-project",
        dataset="raw",
    )

    assert plan.bindings.endpoint_names == ("posts", "comments")
    assert plan.targets[0].node_id == "curated_post_comments"
    assert plan.targets[0].target.table == "graph_post_comments"
    assert "FROM `unit-project`.`raw`.`keyed_join_fixture_posts`" in plan.targets[0].query
    assert "FROM `unit-project`.`raw`.`keyed_join_fixture_comments`" in plan.targets[0].query
    assert "INNER JOIN `_node_1` AS rhs" in plan.targets[0].query
    assert "ON lhs.`id` = rhs.`postId`" in plan.targets[0].query
    assert "SELECT *" not in plan.targets[0].query
