"""Domain/transport round-trip tests for Control contract DTOs."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from dander.control.bundle import PACKAGED_BUNDLE_DIRECTORY
from dander.control.models import (
    ConnectorCatalogResponse,
    OperationCatalogResponse,
    PipelineGraphDocument,
    PluginCatalogResponse,
    TargetNodeDocument,
    TransformNodeDocument,
)
from dander.pipeline.graph import graph_to_payload
from dander.pipeline.operations import build_operation_catalog
from dander.plugins.catalog import build_plugin_catalog


def test_representative_graph_round_trips_without_semantic_field_loss() -> None:
    payload = json.loads(
        (PACKAGED_BUNDLE_DIRECTORY / "fixtures/pipeline-graph.json").read_text(encoding="utf-8")
    )
    document = PipelineGraphDocument.model_validate(payload)

    assert graph_to_payload(document.to_domain()) == payload
    assert {node.type for node in document.nodes} == {"source", "transform", "target", "task"}
    source = next(node for node in document.nodes if node.id == "source_records")
    assert source.fields[0].extensions[0].provider == "redshift"
    extension = next(node for node in document.nodes if node.id == "notify_operator")
    assert extension.config == {"channel": "synthetic", "nested": {"enabled": True}}

    transform = next(node for node in document.nodes if node.id == "normalize_records")
    assert isinstance(transform, TransformNodeDocument)
    assert {operation.kind for operation in transform.config.operations} == {
        "truncate_string",
        "trim_whitespace",
        "default_value",
        "filter_rows",
    }
    transports = {
        node.config.writer.transport
        for node in document.nodes
        if isinstance(node, TargetNodeDocument) and node.config.writer is not None
    }
    assert transports == {"load_job", "storage_write", "copy"}


def test_constant_null_survives_transport_and_canonical_domain_serialization() -> None:
    payload = {
        "name": "constant_null",
        "nodes": [
            {
                "id": "source",
                "type": "source",
                "name": "Source",
                "config": {},
                "fields": [{"name": "id", "type": "STRING"}],
            },
            {
                "id": "transform",
                "type": "transform",
                "name": "Transform",
                "config": {},
                "fields": [{"name": "value", "type": "STRING"}],
            },
        ],
        "edges": [
            {
                "from": "source",
                "to": "transform",
                "mappings": [
                    {
                        "source": None,
                        "target": "value",
                        "transformation": {"kind": "constant", "constant": None},
                    }
                ],
            }
        ],
    }
    document = PipelineGraphDocument.model_validate(payload)
    dumped = graph_to_payload(document.to_domain())

    transformation = dumped["edges"][0]["mappings"][0]["transformation"]
    assert "constant" in transformation
    assert transformation["constant"] is None


def test_legacy_params_alias_normalizes_to_canonical_config() -> None:
    payload = json.loads(
        (PACKAGED_BUNDLE_DIRECTORY / "fixtures/pipeline-graph-alias-input.json").read_text(
            encoding="utf-8"
        )
    )
    document = PipelineGraphDocument.model_validate(payload)
    canonical = graph_to_payload(document.to_domain())

    assert canonical["nodes"][0]["config"] == {"preserved": {"value": 1}}
    assert "params" not in canonical["nodes"][0]


def test_unknown_writer_property_is_rejected_before_domain_round_trip() -> None:
    payload = {
        "name": "strict_writer",
        "nodes": [
            {
                "id": "target",
                "type": "target",
                "name": "Target",
                "config": {
                    "writer": {
                        "write_mode": "snapshot",
                        "destination": {"dataset": "analytics", "table": "records"},
                        "future": "must-not-be-dropped",
                    }
                },
                "fields": [],
            }
        ],
        "edges": [],
    }

    with pytest.raises(ValidationError):
        PipelineGraphDocument.model_validate(payload)


def test_current_catalog_builders_fit_the_explicit_transport_contracts() -> None:
    ConnectorCatalogResponse.model_validate({"connectors": []})
    PluginCatalogResponse.model_validate(build_plugin_catalog())
    OperationCatalogResponse.model_validate(build_operation_catalog())
