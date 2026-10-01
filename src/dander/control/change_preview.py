"""Read-only impact of an unsaved graph compared with its saved definition.

This is a static authoring preview, not an execution plan or a warehouse data diff. It never
loads provider credentials, reads rows, or saves the candidate. The caller owns revision checks.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from dander.control.graph_store import canonicalize_graph_document
from dander.control.models import (
    ControlModel,
    GraphNodeDocument,
    PipelineGraphDocument,
    TargetNodeDocument,
    WriterDocument,
)
from dander.writer import WriteMode


class GraphNodeChange(ControlModel):
    node_id: str
    name: str
    change: Literal["added", "removed", "modified", "layout_only"]
    changed_properties: tuple[str, ...] = ()
    affects_data: bool


class GraphConnectionChange(ControlModel):
    source: str
    target: str
    change: Literal["added", "removed", "modified"]


class GraphOutputState(ControlModel):
    """Only declared output coordinates and field names, never arbitrary node configuration."""

    writer: WriterDocument | None
    field_names: tuple[str, ...]


class GraphOutputImpact(ControlModel):
    node_id: str
    name: str
    affected: bool
    before: GraphOutputState | None
    after: GraphOutputState | None
    effect: str


class GraphChangeEstimates(ControlModel):
    rows_written: None = None
    cost_usd: None = None
    explanation: str = (
        "Unknown: this preview reads graph definitions only, not source or warehouse data."
    )


class GraphChangePreviewResponse(ControlModel):
    """Static changes and potential downstream effects; a run still executes all outputs."""

    baseline_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_properties_changed: tuple[str, ...]
    node_changes: tuple[GraphNodeChange, ...]
    connection_changes: tuple[GraphConnectionChange, ...]
    affected_outputs: tuple[str, ...]
    outputs: tuple[GraphOutputImpact, ...]
    run_output_ids: tuple[str, ...]
    estimates: GraphChangeEstimates = Field(default_factory=GraphChangeEstimates)
    limitations: tuple[str, ...] = (
        "Static impact within this graph only; external consumers are not inspected.",
        "Affected outputs describe changed definitions, not a selection of outputs to execute. "
        "A full run writes every configured output, including unchanged outputs.",
        "Declared write behavior is not proof that this graph can execute. Runtime validation "
        "and provider checks still apply; the current graph runtime supports replace targets.",
        "Saving a graph does not write or delete warehouse data.",
    )


def compare_graph_changes(
    baseline: PipelineGraphDocument,
    candidate: PipelineGraphDocument,
) -> GraphChangePreviewResponse:
    """Compare canonical documents without exposing configuration values or touching providers.

    Nodes are matched by stable ID, and connections by endpoint pair. The old and new edges
    both participate in downstream impact, so disconnecting or removing a node cannot hide its
    previous outputs. Traversal is iterative because the union of two valid DAGs can be cyclic.
    """
    before = canonicalize_graph_document(baseline)
    after = canonicalize_graph_document(candidate)
    old_nodes = {node.id: node for node in before.document.nodes}
    new_nodes = {node.id: node for node in after.document.nodes}
    node_changes = tuple(
        change
        for node_id in sorted(old_nodes.keys() | new_nodes.keys())
        if (change := _node_change(node_id, old_nodes.get(node_id), new_nodes.get(node_id)))
        is not None
    )
    old_edges = _connections(before.document)
    new_edges = _connections(after.document)
    connection_changes = tuple(
        GraphConnectionChange(
            source=source,
            target=target,
            change=(
                "added"
                if (source, target) not in old_edges
                else "removed"
                if (source, target) not in new_edges
                else "modified"
            ),
        )
        for source, target in sorted(old_edges.keys() | new_edges.keys())
        if old_edges.get((source, target)) != new_edges.get((source, target))
    )
    graph_properties_changed = tuple(
        key
        for key in ("name", "trigger")
        if getattr(before.document, key) != getattr(after.document, key)
    )
    affected = {change.node_id for change in node_changes if change.affects_data}
    affected.update(change.target for change in connection_changes)
    if "trigger" in graph_properties_changed:
        affected.update(old_nodes.keys() | new_nodes.keys())
    successors: dict[str, set[str]] = {}
    for source, target in old_edges.keys() | new_edges.keys():
        successors.setdefault(source, set()).add(target)
    pending = list(affected)
    while pending:
        for node_id in successors.get(pending.pop(), set()) - affected:
            affected.add(node_id)
            pending.append(node_id)

    old_targets = {
        node_id: node for node_id, node in old_nodes.items() if isinstance(node, TargetNodeDocument)
    }
    new_targets = {
        node_id: node for node_id, node in new_nodes.items() if isinstance(node, TargetNodeDocument)
    }
    outputs = tuple(
        _output_impact(node_id, old_targets.get(node_id), new_targets.get(node_id), affected)
        for node_id in sorted(old_targets.keys() | new_targets.keys())
    )
    return GraphChangePreviewResponse(
        baseline_content_sha256=before.content_sha256,
        candidate_content_sha256=after.content_sha256,
        graph_properties_changed=graph_properties_changed,
        node_changes=node_changes,
        connection_changes=connection_changes,
        affected_outputs=tuple(output.node_id for output in outputs if output.affected),
        outputs=outputs,
        run_output_ids=tuple(sorted(new_targets)),
    )


def _node_change(
    node_id: str,
    before: GraphNodeDocument | None,
    after: GraphNodeDocument | None,
) -> GraphNodeChange | None:
    if before is None:
        assert after is not None
        return GraphNodeChange(node_id=node_id, name=after.name, change="added", affects_data=True)
    if after is None:
        return GraphNodeChange(
            node_id=node_id, name=before.name, change="removed", affects_data=True
        )
    old_payload = before.model_dump(mode="json", by_alias=True)
    new_payload = after.model_dump(mode="json", by_alias=True)
    properties = tuple(
        key
        for key in sorted(old_payload.keys() | new_payload.keys())
        if old_payload.get(key) != new_payload.get(key)
    )
    if not properties:
        return None
    layout_only = properties == ("visual",)
    return GraphNodeChange(
        node_id=node_id,
        name=after.name,
        change="layout_only" if layout_only else "modified",
        changed_properties=properties,
        affects_data=not layout_only,
    )


def _connections(document: PipelineGraphDocument) -> dict[tuple[str, str], tuple[str, ...]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for edge in document.edges:
        grouped.setdefault((edge.source, edge.target), []).append(
            json.dumps(edge.model_dump(mode="json", by_alias=True), sort_keys=True)
        )
    return {pair: tuple(sorted(edges)) for pair, edges in grouped.items()}


def _output_state(node: TargetNodeDocument | None) -> GraphOutputState | None:
    if node is None:
        return None
    return GraphOutputState(
        writer=node.config.writer, field_names=tuple(f.name for f in node.fields)
    )


def _output_impact(
    node_id: str,
    before: TargetNodeDocument | None,
    after: TargetNodeDocument | None,
    affected: set[str],
) -> GraphOutputImpact:
    old_state = _output_state(before)
    new_state = _output_state(after)
    if after is None:
        assert before is not None
        effect = (
            "Removed from this graph. Future runs stop writing this output; "
            "existing warehouse data is not deleted."
        )
        name = before.name
    else:
        name = after.name
        prefix = "New graph output. " if before is None else ""
        old_writer = old_state.writer if old_state is not None else None
        new_writer = after.config.writer
        if old_writer is not None and new_writer is not None:
            old_destination = old_writer.destination
            new_destination = new_writer.destination
            if (
                old_destination.project,
                old_destination.dataset,
                old_destination.table,
            ) != (
                new_destination.project,
                new_destination.dataset,
                new_destination.table,
            ):
                prefix = (
                    "Destination changed. Existing data at the previous destination is retained. "
                )
        effect = prefix + _write_effect(new_writer)
    return GraphOutputImpact(
        node_id=node_id,
        name=name,
        affected=node_id in affected,
        before=old_state,
        after=new_state,
        effect=effect,
    )


def _write_effect(writer: WriterDocument | None) -> str:
    if writer is None:
        return "Write behavior is unknown: this target has no declared writer configuration."
    effects = {
        WriteMode.REPLACE: "Replaces all rows in the destination with this run's result.",
        WriteMode.SCD1: "Updates matching business keys and inserts new rows.",
        WriteMode.SCD2: "Keeps row history and records new versions of changed business keys.",
        WriteMode.SNAPSHOT: "Writes a snapshot using the configured partitioning.",
        WriteMode.INCREMENTAL: "Writes incremental rows using the configured cursor and keys.",
    }
    return "Declared next-run behavior: " + effects[writer.write_mode]
