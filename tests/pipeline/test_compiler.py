"""Executable visual-pipeline compiler and target-writer dispatch tests."""

from __future__ import annotations

from typing import Any

import pytest
import sqlglot

from dander.pipeline import PipelineCompileError, compile_target, prepare_target_writer
from dander.pipeline.graph import (
    Edge,
    FieldMapping,
    Node,
    NodeField,
    PipelineGraph,
    Transformation,
    TransformationKind,
)
from dander.pipeline.node_config import (
    DestinationSpec,
    ExecutableJoinKey,
    ExecutableJoinType,
    PartitioningSpec,
    TargetNodeConfig,
    TransformJoinConfig,
    TransformNodeConfig,
    WriterConfig,
)
from dander.pipeline.operations import OperationSpec
from dander.writer import (
    BigQueryIncrementalWriter,
    BigQueryReplaceWriter,
    BigQueryScd1Writer,
    BigQueryScd2Writer,
    BigQuerySnapshotWriter,
    BigQueryStorageIncrementalWriter,
    BigQueryStorageScd1Writer,
    WriteMode,
    WriteTransport,
)


def _target_config(mode: WriteMode = WriteMode.SCD1) -> TargetNodeConfig:
    return TargetNodeConfig(
        writer=WriterConfig(
            write_mode=mode,
            destination=DestinationSpec(
                project="dander-test",
                dataset="analytics",
                table="people",
                business_key=(
                    [] if mode in (WriteMode.REPLACE, WriteMode.SNAPSHOT) else ["person_id"]
                ),
            ),
            cursor_field="updated_at" if mode is WriteMode.INCREMENTAL else None,
            partitioning=(
                PartitioningSpec(field="snapshot_at") if mode is WriteMode.SNAPSHOT else None
            ),
        )
    )


def _linear_graph() -> PipelineGraph:
    source = Node(
        id="source",
        type="source",
        name="Source",
        fields=[
            NodeField(name="id", type="STRING", cast_to="STRING"),
            NodeField(name="first_name", type="STRING"),
            NodeField(name="last_name", type="STRING"),
            NodeField(name="phone", type="STRING"),
        ],
    )
    transform = Node(
        id="transform",
        type="transform",
        name="Transform",
        fields=[
            NodeField(name="person_id", type="STRING"),
            NodeField(name="full_name", type="STRING"),
            NodeField(name="phone", type="STRING"),
            NodeField(name="status", type="STRING"),
        ],
    )
    target = Node(
        id="target",
        type="target",
        name="Target",
        config=_target_config(),
        fields=[
            NodeField(name="person_id", type="STRING"),
            NodeField(name="display_name", type="STRING"),
            NodeField(name="phone_normalized", type="STRING"),
            NodeField(name="status", type="STRING", cast_to="STRING"),
        ],
    )
    return PipelineGraph(
        name="people",
        nodes=[source, transform, target],
        edges=[
            Edge(
                **{"from": "source", "to": "transform"},
                mappings=[
                    FieldMapping(source="id", target="person_id"),
                    FieldMapping(
                        source="first_name",
                        target="full_name",
                        transformation=Transformation(
                            kind=TransformationKind.EXPRESSION,
                            expression="CONCAT(first_name, ' ', last_name)",
                            inputs=["first_name", "last_name"],
                        ),
                    ),
                    FieldMapping(source="phone", target="phone"),
                    FieldMapping(
                        target="status",
                        transformation=Transformation(
                            kind=TransformationKind.CONSTANT,
                            constant="active",
                        ),
                    ),
                ],
            ),
            Edge(
                **{"from": "transform", "to": "target"},
                mappings=[
                    FieldMapping(source="person_id", target="person_id"),
                    FieldMapping(source="full_name", target="display_name"),
                    FieldMapping(
                        source="phone",
                        target="phone_normalized",
                        transformation=Transformation(
                            kind=TransformationKind.CUSTOM_CODE,
                            function="transforms.normalize_phone",
                            inputs=["phone"],
                        ),
                    ),
                    FieldMapping(source="status", target="status"),
                ],
            ),
        ],
    )


def test_compiles_linear_graph_to_explicit_bigquery_sql() -> None:
    compiled = compile_target(
        _linear_graph(),
        "target",
        source_relations={"source": "dander-test.raw.people"},
    )

    assert compiled.write_mode is WriteMode.SCD1
    assert compiled.target.business_key == ("person_id",)
    assert "FROM `dander-test.raw.people`" in compiled.query
    assert "SAFE_CAST(`id` AS STRING) AS `id`" in compiled.query
    assert "CONCAT(source.first_name, ' ', source.last_name) AS `full_name`" in compiled.query
    assert (
        "REGEXP_REPLACE(CAST(source.`phone` AS STRING), r'[^0-9+]', '') AS `phone_normalized`"
    ) in compiled.query
    assert "SAFE_CAST(source.`status` AS STRING) AS `status`" in compiled.query
    assert "SELECT *" not in compiled.query


def test_compiles_ordered_schema_preserving_transform_operations() -> None:
    graph = _linear_graph()
    config = graph.nodes[1].config
    assert isinstance(config, TransformNodeConfig)
    config.operations = [
        OperationSpec.model_validate({"kind": "trim_whitespace", "params": {"field": "full_name"}}),
        OperationSpec.model_validate(
            {
                "kind": "truncate_string",
                "params": {"field": "full_name", "max_length": 80},
            }
        ),
        OperationSpec.model_validate(
            {
                "kind": "default_value",
                "params": {"field": "status", "default": "unknown"},
            }
        ),
        OperationSpec.model_validate(
            {
                "kind": "filter_rows",
                "params": {
                    "conditions": [
                        {"field": "status", "op": "not_in", "value": ["archived", "deleted"]},
                        {"field": "full_name", "op": "is_not_null"},
                    ]
                },
            }
        ),
    ]

    query = compile_target(
        graph,
        "target",
        source_relations={"source": "dander-test.raw.people"},
    ).query

    assert "TRIM(source.`full_name`) AS `full_name`" in query
    assert "FROM `_node_1` AS source" in query
    assert "SUBSTR(source.`full_name`, 1, 80) AS `full_name`" in query
    assert "FROM `_node_2` AS source" in query
    assert "COALESCE(source.`status`, CAST('unknown' AS STRING)) AS `status`" in query
    assert "FROM `_node_3` AS source" in query
    assert (
        "WHERE (source.`status` NOT IN ('archived', 'deleted')) AND "
        "(source.`full_name` IS NOT NULL)"
    ) in query
    assert "FROM `_node_4` AS source" in query
    assert "SELECT *" not in query
    assert sqlglot.parse_one(query, read="bigquery") is not None


def test_string_operation_rejects_non_string_declared_field() -> None:
    graph = _linear_graph()
    config = graph.nodes[1].config
    assert isinstance(config, TransformNodeConfig)
    config.operations = [
        OperationSpec.model_validate({"kind": "trim_whitespace", "params": {"field": "person_id"}})
    ]
    graph.nodes[1].fields[0].type = "INT64"

    with pytest.raises(PipelineCompileError, match="non-STRING field 'person_id'"):
        compile_target(
            graph,
            "target",
            source_relations={"source": "dander-test.raw.people"},
        )


@pytest.mark.parametrize(
    ("expression", "inputs", "message"),
    [
        ("(SELECT first_name FROM other_table)", ["first_name"], "row-local"),
        ("CONCAT(first_name, last_name)", ["first_name"], "declared inputs"),
        ("MD5(first_name)", ["first_name"], "not allow-listed"),
    ],
)
def test_expression_compilation_fails_closed(
    expression: str,
    inputs: list[str],
    message: str,
) -> None:
    graph = _linear_graph()
    graph.edges[0].mappings[1].transformation = Transformation(
        kind=TransformationKind.EXPRESSION,
        expression=expression,
        inputs=inputs,
    )

    with pytest.raises(PipelineCompileError, match=message):
        compile_target(
            graph,
            "target",
            source_relations={"source": "dander-test.raw.people"},
        )


def test_compile_rejects_ambiguous_join_execution() -> None:
    graph = _linear_graph()
    from dander.pipeline.graph import JoinKeyPair, JoinSpec, JoinType

    graph.edges[0].join = JoinSpec(
        type=JoinType.LEFT,
        keys=[JoinKeyPair(left="id", right="person_id")],
    )

    with pytest.raises(PipelineCompileError, match="Legacy edge joins"):
        compile_target(
            graph,
            "target",
            source_relations={"source": "dander-test.raw.people"},
        )


@pytest.mark.parametrize(
    ("join_type", "keyword"),
    [
        (ExecutableJoinType.INNER, "INNER JOIN"),
        (ExecutableJoinType.LEFT, "LEFT JOIN"),
        (ExecutableJoinType.RIGHT, "RIGHT JOIN"),
        (ExecutableJoinType.FULL, "FULL OUTER JOIN"),
    ],
)
def test_compiles_explicit_two_input_join_transform(
    join_type: ExecutableJoinType,
    keyword: str,
) -> None:
    left = Node(
        id="people",
        type="source",
        name="People",
        fields=[
            NodeField(name="person_id", type="STRING"),
            NodeField(name="name", type="STRING"),
        ],
    )
    right = Node(
        id="scores",
        type="source",
        name="Scores",
        fields=[
            NodeField(name="person_id", type="STRING"),
            NodeField(name="score", type="INT64"),
        ],
    )
    joined = Node(
        id="joined",
        type="transform",
        name="Joined",
        config=TransformNodeConfig(
            join=TransformJoinConfig(
                left_input="people",
                right_input="scores",
                type=join_type,
                keys=[ExecutableJoinKey(left="person_id", right="person_id")],
            )
        ),
        fields=[
            NodeField(name="person_id", type="STRING"),
            NodeField(name="name", type="STRING"),
            NodeField(name="score", type="INT64"),
        ],
    )
    target = Node(
        id="target",
        type="target",
        name="Target",
        config=_target_config(),
        fields=[
            NodeField(name="person_id", type="STRING"),
            NodeField(name="name", type="STRING"),
            NodeField(name="score", type="INT64"),
        ],
    )
    graph = PipelineGraph(
        name="joined_people",
        nodes=[left, right, joined, target],
        edges=[
            Edge(
                **{"from": "people", "to": "joined"},
                mappings=[
                    FieldMapping(source="person_id", target="person_id"),
                    FieldMapping(source="name", target="name"),
                ],
            ),
            Edge(
                **{"from": "scores", "to": "joined"},
                mappings=[FieldMapping(source="score", target="score")],
            ),
            Edge(
                **{"from": "joined", "to": "target"},
                mappings=[
                    FieldMapping(source="person_id", target="person_id"),
                    FieldMapping(source="name", target="name"),
                    FieldMapping(source="score", target="score"),
                ],
            ),
        ],
    )

    compiled = compile_target(
        graph,
        "target",
        source_relations={
            "people": "dander-test.raw.people",
            "scores": "dander-test.raw.scores",
        },
    )

    assert f"{keyword} `_node_1` AS rhs" in compiled.query
    assert "ON lhs.`person_id` = rhs.`person_id`" in compiled.query
    assert "lhs.`name` AS `name`" in compiled.query
    assert "rhs.`score` AS `score`" in compiled.query
    assert sqlglot.parse_one(compiled.query, read="bigquery") is not None


def test_compile_rejects_duplicate_target_mapping() -> None:
    graph = _linear_graph()
    graph.edges[0].mappings.append(FieldMapping(source="id", target="person_id"))

    with pytest.raises(PipelineCompileError, match="more than once"):
        compile_target(
            graph,
            "target",
            source_relations={"source": "dander-test.raw.people"},
        )


@pytest.mark.parametrize(
    ("mode", "expected_type"),
    [
        (WriteMode.SCD1, BigQueryScd1Writer),
        (WriteMode.SCD2, BigQueryScd2Writer),
        (WriteMode.INCREMENTAL, BigQueryIncrementalWriter),
        (WriteMode.SNAPSHOT, BigQuerySnapshotWriter),
        (WriteMode.REPLACE, BigQueryReplaceWriter),
    ],
)
def test_target_config_dispatches_every_writer(
    mode: WriteMode,
    expected_type: type[Any],
) -> None:
    node = Node(id="target", type="target", name="Target", config=_target_config(mode))

    prepared = prepare_target_writer(node, default_project="fallback", client=object())

    assert isinstance(prepared.writer, expected_type)
    assert prepared.target.project == "dander-test"


def test_writer_dispatch_resolves_default_project() -> None:
    config = _target_config(WriteMode.REPLACE)
    assert config.writer is not None
    config.writer.destination.project = None
    node = Node(id="target", type="target", name="Target", config=config)

    prepared = prepare_target_writer(node, default_project="fallback-project", client=object())

    assert prepared.target.project == "fallback-project"


def test_compile_resolves_default_project() -> None:
    graph = _linear_graph()
    assert isinstance(graph.nodes[-1].config, TargetNodeConfig)
    assert graph.nodes[-1].config.writer is not None
    graph.nodes[-1].config.writer.destination.project = None

    compiled = compile_target(
        graph,
        "target",
        source_relations={"source": "dander-test.raw.people"},
        default_project="fallback-project",
    )

    assert compiled.target.project == "fallback-project"


@pytest.mark.parametrize(
    ("mode", "expected_type"),
    [
        (WriteMode.SCD1, BigQueryStorageScd1Writer),
        (WriteMode.INCREMENTAL, BigQueryStorageIncrementalWriter),
    ],
)
def test_target_dispatches_storage_write_transport(
    mode: WriteMode,
    expected_type: type[Any],
) -> None:
    config = _target_config(mode)
    assert config.writer is not None
    config.writer.transport = WriteTransport.STORAGE_WRITE
    node = Node(
        id="target",
        type="target",
        name="Target",
        config=config,
        fields=[
            NodeField(name="person_id", type="STRING"),
            NodeField(name="updated_at", type="STRING"),
        ],
    )

    prepared = prepare_target_writer(node, default_project="fallback", client=object())

    assert isinstance(prepared.writer, expected_type)
    assert prepared.target.schema[0].data_type == "STRING"
