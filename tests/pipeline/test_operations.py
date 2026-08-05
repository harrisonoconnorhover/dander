"""Canonical graph-operation configuration and semantic validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from dander.pipeline import (
    DefaultValueParams,
    FilterRowsParams,
    OperationKind,
    TransformNodeConfig,
    UnknownOperationFieldError,
    load_graph_from_yaml,
    validate_field_wiring,
)
from dander.pipeline.graph import PipelineGraph, dump_graph_to_yaml
from dander.pipeline.operations import TrimWhitespaceParams, TruncateStringParams

if TYPE_CHECKING:
    from pathlib import Path


def _graph_payload() -> dict[str, object]:
    return {
        "name": "operation_graph",
        "nodes": [
            {
                "id": "source",
                "type": "source",
                "name": "Source",
                "fields": [
                    {"name": "id", "type": "INT64"},
                    {"name": "title", "type": "STRING"},
                    {"name": "status", "type": "STRING"},
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
                            "kind": "truncate_string",
                            "params": {"field": "title", "max_length": 80},
                        },
                        {
                            "kind": "default_value",
                            "params": {"field": "status", "default": "unknown"},
                        },
                        {
                            "kind": "filter_rows",
                            "params": {
                                "logic": "all",
                                "conditions": [
                                    {"field": "status", "op": "ne", "value": "archived"},
                                    {"field": "title", "op": "is_not_null"},
                                ],
                            },
                        },
                    ]
                },
                "fields": [
                    {"name": "id", "type": "INT64"},
                    {"name": "title", "type": "STRING"},
                    {"name": "status", "type": "STRING"},
                ],
            },
        ],
        "edges": [
            {
                "from": "source",
                "to": "clean",
                "mappings": [
                    {"source": "id", "target": "id"},
                    {"source": "title", "target": "title"},
                    {"source": "status", "target": "status"},
                ],
            }
        ],
    }


def test_operations_validate_and_round_trip_in_declared_order(tmp_path: Path) -> None:
    graph = PipelineGraph.model_validate(_graph_payload())
    config = graph.nodes[1].config
    assert isinstance(config, TransformNodeConfig)
    assert [operation.kind for operation in config.operations] == [
        OperationKind.TRIM_WHITESPACE,
        OperationKind.TRUNCATE_STRING,
        OperationKind.DEFAULT_VALUE,
        OperationKind.FILTER_ROWS,
    ]
    assert isinstance(config.operations[0].params, TrimWhitespaceParams)
    assert isinstance(config.operations[1].params, TruncateStringParams)
    assert isinstance(config.operations[2].params, DefaultValueParams)
    assert isinstance(config.operations[3].params, FilterRowsParams)

    path = tmp_path / "pipeline.yaml"
    dump_graph_to_yaml(graph, path)
    reloaded = load_graph_from_yaml(path)

    assert reloaded == graph
    assert "params:" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "unknown", "params": {}},
        {"kind": "trim_whitespace", "params": {"field": "not a field"}},
        {"kind": "truncate_string", "params": {"field": "title", "max_length": -1}},
        {"kind": "default_value", "params": {"field": "status"}},
        {
            "kind": "filter_rows",
            "params": {"conditions": [{"field": "status", "op": "is_null", "value": 1}]},
        },
        {
            "kind": "filter_rows",
            "params": {"conditions": [{"field": "status", "op": "in", "value": []}]},
        },
    ],
)
def test_invalid_operation_contract_fails_at_graph_load(operation: dict[str, object]) -> None:
    payload = _graph_payload()
    nodes = payload["nodes"]
    assert isinstance(nodes, list)
    transform = nodes[1]
    assert isinstance(transform, dict)
    transform["config"] = {"operations": [operation]}

    with pytest.raises(ValidationError):
        PipelineGraph.model_validate(payload)


def test_operation_must_reference_owning_transform_output_field() -> None:
    payload = _graph_payload()
    nodes = payload["nodes"]
    assert isinstance(nodes, list)
    transform = nodes[1]
    assert isinstance(transform, dict)
    transform["config"] = {
        "operations": [{"kind": "trim_whitespace", "params": {"field": "missing"}}]
    }
    graph = PipelineGraph.model_validate(payload)

    with pytest.raises(UnknownOperationFieldError) as error:
        validate_field_wiring(graph)

    assert error.value.node_id == "clean"
    assert error.value.field_name == "missing"
    assert error.value.operation_kind == "trim_whitespace"
    assert error.value.operation_index == 0
