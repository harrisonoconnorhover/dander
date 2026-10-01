"""Read-only presentation of an output repair against a saved graph."""

from __future__ import annotations

from typing import Literal

from dander.control.models import (
    ControlModel,
    DestinationDocument,
    PipelineGraphDocument,
    TargetNodeDocument,
)
from dander.pipeline.repair import GraphRepairWindow, plan_graph_repair


class GraphRepairOutput(ControlModel):
    node_id: str
    name: str
    destination: DestinationDocument
    partition_field: str
    partition_type: Literal["DATE", "TIMESTAMP"]
    business_key: tuple[str, ...]


class GraphRepairPreviewResponse(ControlModel):
    graph_content_sha256: str
    window: GraphRepairWindow
    outputs: tuple[GraphRepairOutput, ...]
    environments: tuple[str, ...]
    source: Literal["retained_raw"] = "retained_raw"
    summary: str = (
        "Rebuild the selected output dates from the raw data currently stored in your warehouse."
    )
    limitations: tuple[str, ...] = (
        "Start date is included; end date is excluded. Timestamp fields use UTC calendar dates.",
        "Source extraction and its normal saved progress are unchanged. "
        "This is not a historical snapshot.",
        "Only selected output dates are published; computing them can scan data "
        "outside that range.",
        "A record moving across the date boundary stops publication for that output. "
        "Choose a wider range or rebuild the full output.",
        "Each output publishes atomically. Earlier successful outputs remain if a later output "
        "fails; repeating the same repair converges on the current raw data.",
        "Execution requires a current repair-capable runtime with a BigQuery warehouse. "
        "Runtime validation and warehouse checks still apply.",
    )
    estimated_rows: None = None
    estimated_cost_usd: None = None


def preview_graph_repair(
    document: PipelineGraphDocument,
    *,
    content_sha256: str,
    window: GraphRepairWindow,
    environments: tuple[str, ...],
) -> GraphRepairPreviewResponse:
    """Describe declared eligibility without reading rows or starting an execution."""
    targets = plan_graph_repair(document.to_domain(), window)
    nodes = {node.id: node for node in document.nodes}
    outputs: list[GraphRepairOutput] = []
    for target in targets:
        node = nodes[target.node_id]
        assert isinstance(node, TargetNodeDocument) and node.config.writer is not None
        outputs.append(
            GraphRepairOutput(
                node_id=node.id,
                name=node.name,
                destination=node.config.writer.destination,
                partition_field=target.partition_field,
                partition_type=target.partition_type,
                business_key=target.business_key,
            )
        )
    return GraphRepairPreviewResponse(
        graph_content_sha256=content_sha256,
        window=window,
        outputs=tuple(outputs),
        environments=environments,
    )
