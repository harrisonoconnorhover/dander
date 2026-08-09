"""Provider-neutral graph loading, source binding, and relational planning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from dander.pipeline.compiler import (
    CompiledTarget,
    PipelineCompileError,
    compile_target,
)
from dander.pipeline.errors import GraphValidationError
from dander.pipeline.graph_ops import validate_field_wiring
from dander.pipeline.node_config import SourceNodeConfig, TargetNodeConfig
from dander.warehouse import RelationRef
from dander.writer.base import WriteMode

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from dander.ingestion import SourceConfig
    from dander.pipeline.graph import PipelineGraph

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONNECTOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_PROJECT = re.compile(r"^[A-Za-z][A-Za-z0-9-]{4,61}[A-Za-z0-9]$")


class GraphRuntimeError(RuntimeError):
    """Raised when an authored graph cannot execute through the supported runtime slice."""


def load_graph_for_execution(path: Path) -> PipelineGraph:
    """Load one YAML/JSON graph with strict unknown-field rejection."""
    from dander.pipeline.graph import PipelineGraph

    try:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
        return PipelineGraph.model_validate(raw, extra="forbid")
    except (OSError, json.JSONDecodeError, yaml.YAMLError, ValidationError) as error:
        raise GraphRuntimeError(f"Graph file is invalid: {path.name}") from error


@dataclass(frozen=True)
class GraphSourceBindings:
    """Resolved graph-source nodes for one existing connector configuration."""

    connector: str
    endpoint_names: tuple[str, ...]
    source_relations: Mapping[str, RelationRef]


@dataclass(frozen=True)
class GraphExecutionPlan:
    """Credential-free executable graph plan."""

    bindings: GraphSourceBindings
    targets: tuple[CompiledTarget, ...]


def plan_graph_execution(
    graph: PipelineGraph,
    source_config: SourceConfig,
    *,
    endpoint_relations: Mapping[str, RelationRef] | None = None,
    project: str | None = None,
    dataset: str | None = None,
) -> GraphExecutionPlan:
    """Bind graph sources to connector endpoints and compile every executable target."""
    try:
        validate_field_wiring(graph)
    except GraphValidationError as error:
        raise GraphRuntimeError(str(error)) from error
    if endpoint_relations is None:
        if project is None or not _PROJECT.fullmatch(project):
            raise GraphRuntimeError("Graph execution requires a valid GCP project id")
        if dataset is None or not _IDENTIFIER.fullmatch(dataset):
            raise GraphRuntimeError("Graph execution requires a valid BigQuery raw dataset id")
        endpoint_relations = {
            endpoint.name: RelationRef(
                catalog=project,
                namespace=dataset,
                name=f"{source_config.name}_{endpoint.name}",
            )
            for endpoint in source_config.endpoints
        }
    elif project is not None or dataset is not None:
        raise GraphRuntimeError(
            "Graph execution cannot combine endpoint_relations with project/dataset"
        )
    unsupported = sorted({node.type for node in graph.nodes} - {"source", "transform", "target"})
    if unsupported:
        raise GraphRuntimeError(f"Graph execution does not support node type {unsupported[0]!r}")
    if any(field.tests for node in graph.nodes for field in node.fields):
        raise GraphRuntimeError("Graph field tests are not executable yet")

    endpoints = {endpoint.name: endpoint for endpoint in source_config.endpoints}
    endpoint_names: list[str] = []
    relations: dict[str, RelationRef] = {}
    for node in graph.nodes:
        if node.type != "source":
            continue
        config = node.config
        if not isinstance(config, SourceNodeConfig):
            raise GraphRuntimeError(f"Source node {node.id!r} has invalid configuration")
        if config.model_extra:
            raise GraphRuntimeError(f"Source node {node.id!r} has unsupported configuration")
        if config.request is not None:
            raise GraphRuntimeError(
                f"Source node {node.id!r} must use its connector endpoint, not an inline request"
            )
        if config.connector is None or config.endpoint is None:
            raise GraphRuntimeError(f"Source node {node.id!r} must declare connector and endpoint")
        if not _CONNECTOR.fullmatch(config.connector) or not _IDENTIFIER.fullmatch(config.endpoint):
            raise GraphRuntimeError(
                f"Source node {node.id!r} has an invalid connector or endpoint binding"
            )
        if config.connector != source_config.name:
            raise GraphRuntimeError(
                f"Source node {node.id!r} must bind to pipeline connector {source_config.name!r}"
            )
        endpoint = endpoints.get(config.endpoint)
        if endpoint is None:
            raise GraphRuntimeError(
                f"Source node {node.id!r} references unknown endpoint {config.endpoint!r}"
            )
        declared = {field.name for field in endpoint.raw_schema}
        if missing := sorted({field.name for field in node.fields} - declared):
            raise GraphRuntimeError(
                f"Source node {node.id!r} references undeclared endpoint field {missing[0]!r}"
            )
        if node.cursor is not None and node.cursor.field != endpoint.incremental_cursor:
            raise GraphRuntimeError(
                f"Source node {node.id!r} cursor does not match its connector endpoint"
            )
        if config.endpoint not in endpoint_names:
            endpoint_names.append(config.endpoint)
        try:
            relations[node.id] = endpoint_relations[config.endpoint]
        except KeyError as error:
            raise GraphRuntimeError(
                f"Connector endpoint {config.endpoint!r} has no resolved warehouse relation"
            ) from error

    if not relations:
        raise GraphRuntimeError("Graph execution requires at least one source node")

    target_nodes = [node for node in graph.nodes if node.type == "target"]
    if not target_nodes:
        raise GraphRuntimeError("Graph execution requires at least one target node")
    targets: list[CompiledTarget] = []
    for node in target_nodes:
        if not isinstance(node.config, TargetNodeConfig) or node.config.writer is None:
            raise GraphRuntimeError(f"Target node {node.id!r} must declare writer configuration")
        try:
            compiled = compile_target(
                graph,
                node.id,
                source_relations=relations,
                default_catalog=next(iter(relations.values())).catalog,
            )
        except PipelineCompileError as error:
            raise GraphRuntimeError(str(error)) from error
        if compiled.write_mode is not WriteMode.REPLACE:
            raise GraphRuntimeError(
                f"Target node {node.id!r} uses unsupported graph write mode "
                f"{compiled.write_mode.value!r}; use 'replace'"
            )
        runtime_catalog = next(iter(relations.values())).catalog
        if compiled.target.relation_ref.catalog != runtime_catalog:
            raise GraphRuntimeError(
                f"Target node {node.id!r} must write inside the runtime catalog"
            )
        targets.append(compiled)
    return GraphExecutionPlan(
        bindings=GraphSourceBindings(
            connector=source_config.name,
            endpoint_names=tuple(endpoint_names),
            source_relations=relations,
        ),
        targets=tuple(targets),
    )
