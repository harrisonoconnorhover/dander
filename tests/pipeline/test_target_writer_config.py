"""Unit tests for the target/writer node config (DANDER-16).

Covers `WriterConfig`/`DestinationSpec`/`PartitioningSpec` attached to `TargetNodeConfig.writer`:
loading representative write patterns from YAML and JSON, boundary-constraint rejections, YAML/JSON
round-trip stability (including partitioning/clustering survival), and backward compatibility with
a `target` node that has no `writer` block. No network; only `tmp_path` I/O, matching
`test_node_config.py`/`test_graph.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from dander.pipeline.graph import (
    Node,
    PipelineGraph,
    dump_graph_to_json,
    dump_graph_to_yaml,
    load_graph_from_json,
    load_graph_from_yaml,
)
from dander.pipeline.node_config import (
    DestinationSpec,
    PartitioningSpec,
    PartitioningType,
    TargetNodeConfig,
    WriterConfig,
)
from dander.writer.base import WriteMode, WriteTransport

if TYPE_CHECKING:
    from pathlib import Path

_YAML_DOC = """
name: candidate_load
nodes:
  - id: n1
    type: source
    name: extract_candidates
    config:
      endpoint: /candidates
  - id: n2
    type: target
    name: load_scd1
    config:
      writer:
        write_mode: scd1
        destination:
          dataset: analytics
          table: dim_candidate
          business_key: [candidate_id]
  - id: n3
    type: target
    name: load_scd2
    config:
      writer:
        write_mode: scd2
        destination:
          dataset: analytics
          table: dim_candidate_history
          business_key: [candidate_id]
  - id: n4
    type: target
    name: load_snapshot
    config:
      writer:
        write_mode: snapshot
        destination:
          dataset: analytics
          table: fact_candidate_snapshot
        partitioning:
          field: snapshot_date
          granularity: day
        clustering: [region, department]
  - id: n5
    type: target
    name: load_incremental
    config:
      writer:
        write_mode: incremental
        destination:
          dataset: analytics
          table: fact_candidate_events
          business_key: [event_id]
        cursor_field: updated_at
edges:
  - from: n1
    to: n2
  - from: n2
    to: n3
  - from: n3
    to: n4
  - from: n4
    to: n5
"""

_JSON_DOC = """
{
  "name": "candidate_load",
  "nodes": [
    {
      "id": "n1",
      "type": "source",
      "name": "extract_candidates",
      "config": {"endpoint": "/candidates"}
    },
    {
      "id": "n2",
      "type": "target",
      "name": "load_scd1",
      "config": {
        "writer": {
          "write_mode": "scd1",
          "destination": {
            "dataset": "analytics",
            "table": "dim_candidate",
            "business_key": ["candidate_id"]
          }
        }
      }
    },
    {
      "id": "n3",
      "type": "target",
      "name": "load_scd2",
      "config": {
        "writer": {
          "write_mode": "scd2",
          "destination": {
            "dataset": "analytics",
            "table": "dim_candidate_history",
            "business_key": ["candidate_id"]
          }
        }
      }
    },
    {
      "id": "n4",
      "type": "target",
      "name": "load_snapshot",
      "config": {
        "writer": {
          "write_mode": "snapshot",
          "destination": {
            "dataset": "analytics",
            "table": "fact_candidate_snapshot"
          },
          "partitioning": {"field": "snapshot_date", "granularity": "day"},
          "clustering": ["region", "department"]
        }
      }
    },
    {
      "id": "n5",
      "type": "target",
      "name": "load_incremental",
      "config": {
        "writer": {
          "write_mode": "incremental",
          "destination": {
            "dataset": "analytics",
            "table": "fact_candidate_events",
            "business_key": ["event_id"]
          },
          "cursor_field": "updated_at"
        }
      }
    }
  ],
  "edges": [
    {"from": "n1", "to": "n2"},
    {"from": "n2", "to": "n3"},
    {"from": "n3", "to": "n4"},
    {"from": "n4", "to": "n5"}
  ]
}
"""

_NO_WRITER_YAML_DOC = """
name: candidate_ingest
nodes:
  - id: n1
    type: source
    name: extract_candidates
    config:
      endpoint: /candidates
  - id: n2
    type: target
    name: load_candidates
edges:
  - from: n1
    to: n2
"""


def _assert_representative_patterns(graph: PipelineGraph) -> None:
    _, scd1_node, scd2_node, snapshot_node, incremental_node = graph.nodes

    scd1_writer = scd1_node.config.writer  # type: ignore[union-attr]
    assert isinstance(scd1_writer, WriterConfig)
    assert scd1_writer.write_mode is WriteMode.SCD1
    assert scd1_writer.destination.dataset == "analytics"
    assert scd1_writer.destination.table == "dim_candidate"
    assert scd1_writer.destination.business_key == ["candidate_id"]

    scd2_writer = scd2_node.config.writer  # type: ignore[union-attr]
    assert isinstance(scd2_writer, WriterConfig)
    assert scd2_writer.write_mode is WriteMode.SCD2
    assert scd2_writer.destination.business_key == ["candidate_id"]

    snapshot_writer = snapshot_node.config.writer  # type: ignore[union-attr]
    assert isinstance(snapshot_writer, WriterConfig)
    assert snapshot_writer.write_mode is WriteMode.SNAPSHOT
    assert snapshot_writer.destination.business_key == []
    assert snapshot_writer.partitioning == PartitioningSpec(
        field="snapshot_date", granularity=PartitioningType.DAY
    )
    assert snapshot_writer.clustering == ["region", "department"]

    incremental_writer = incremental_node.config.writer  # type: ignore[union-attr]
    assert isinstance(incremental_writer, WriterConfig)
    assert incremental_writer.write_mode is WriteMode.INCREMENTAL
    assert incremental_writer.cursor_field == "updated_at"
    assert incremental_writer.destination.business_key == ["event_id"]


def test_representative_write_patterns_load_from_yaml(tmp_path: Path) -> None:
    """SCD1/SCD2/SNAPSHOT/INCREMENTAL writer configs load correctly from YAML."""
    path = tmp_path / "graph.yaml"
    path.write_text(_YAML_DOC)
    graph = load_graph_from_yaml(path)
    _assert_representative_patterns(graph)


def test_representative_write_patterns_load_from_json(tmp_path: Path) -> None:
    """SCD1/SCD2/SNAPSHOT/INCREMENTAL writer configs load correctly from JSON."""
    path = tmp_path / "graph.json"
    path.write_text(_JSON_DOC)
    graph = load_graph_from_json(path)
    _assert_representative_patterns(graph)


# -- Boundary rejections -------------------------------------------------------------------------


def test_invalid_write_mode_string_is_rejected() -> None:
    """An out-of-set `write_mode` string fails at the Pydantic boundary."""
    with pytest.raises(ValidationError):
        WriterConfig.model_validate(
            {
                "write_mode": "bulk_overwrite",
                "destination": {"dataset": "analytics", "table": "dim_candidate"},
            }
        )


def test_direct_transport_cannot_be_authored_in_legacy_graphs() -> None:
    with pytest.raises(ValidationError, match="provider-selected"):
        WriterConfig(
            write_mode=WriteMode.SCD1,
            destination=DestinationSpec(
                dataset="analytics",
                table="dim_candidate",
                business_key=["candidate_id"],
            ),
            transport=WriteTransport.DIRECT,
        )


def test_scd1_with_empty_business_key_is_rejected() -> None:
    """SCD1 requires a non-empty `destination.business_key`."""
    with pytest.raises(ValidationError, match="scd1"):
        WriterConfig(
            write_mode=WriteMode.SCD1,
            destination=DestinationSpec(dataset="analytics", table="dim_candidate"),
        )


def test_incremental_missing_cursor_field_is_rejected() -> None:
    """INCREMENTAL requires a non-empty `cursor_field`."""
    with pytest.raises(ValidationError, match="incremental"):
        WriterConfig(
            write_mode=WriteMode.INCREMENTAL,
            destination=DestinationSpec(
                dataset="analytics", table="fact_candidate_events", business_key=["event_id"]
            ),
        )


def test_too_many_clustering_columns_is_rejected() -> None:
    """More than 4 clustering columns fails at the Pydantic boundary."""
    with pytest.raises(ValidationError):
        WriterConfig(
            write_mode=WriteMode.SNAPSHOT,
            destination=DestinationSpec(dataset="analytics", table="fact_candidate_snapshot"),
            clustering=["a", "b", "c", "d", "e"],
        )


def test_duplicate_clustering_columns_is_rejected() -> None:
    """Duplicate clustering column names are rejected."""
    with pytest.raises(ValidationError, match="duplicate"):
        WriterConfig(
            write_mode=WriteMode.SNAPSHOT,
            destination=DestinationSpec(dataset="analytics", table="fact_candidate_snapshot"),
            clustering=["region", "region"],
        )


def test_snapshot_permits_empty_business_key() -> None:
    """SNAPSHOT (append-only) does not require a `business_key`."""
    config = WriterConfig(
        write_mode=WriteMode.SNAPSHOT,
        destination=DestinationSpec(dataset="analytics", table="fact_candidate_snapshot"),
    )
    assert config.destination.business_key == []


# -- Round-trip stability ------------------------------------------------------------------------


def test_yaml_round_trip_is_stable_with_writer_configs(tmp_path: Path) -> None:
    """Load -> dump -> load is stable for a graph of target nodes with writer configs (YAML)."""
    path = tmp_path / "graph.yaml"
    path.write_text(_YAML_DOC)
    loaded = load_graph_from_yaml(path)

    dump_path = tmp_path / "dumped.yaml"
    dump_graph_to_yaml(loaded, dump_path)
    reloaded = load_graph_from_yaml(dump_path)

    assert loaded == reloaded
    _assert_representative_patterns(reloaded)


def test_json_round_trip_is_stable_with_writer_configs(tmp_path: Path) -> None:
    """Load -> dump -> load is stable for a graph of target nodes with writer configs (JSON)."""
    path = tmp_path / "graph.json"
    path.write_text(_JSON_DOC)
    loaded = load_graph_from_json(path)

    dump_path = tmp_path / "dumped.json"
    dump_graph_to_json(loaded, dump_path)
    reloaded = load_graph_from_json(dump_path)

    assert loaded == reloaded
    _assert_representative_patterns(reloaded)


def test_partitioning_and_clustering_survive_round_trip(tmp_path: Path) -> None:
    """`PartitioningSpec` fields and `clustering` order are identical across a round-trip."""
    path = tmp_path / "graph.yaml"
    path.write_text(_YAML_DOC)
    loaded = load_graph_from_yaml(path)

    dump_path = tmp_path / "dumped.yaml"
    dump_graph_to_yaml(loaded, dump_path)
    reloaded = load_graph_from_yaml(dump_path)

    original_writer = loaded.nodes[3].config.writer  # type: ignore[union-attr]
    reloaded_writer = reloaded.nodes[3].config.writer  # type: ignore[union-attr]
    assert isinstance(original_writer, WriterConfig)
    assert isinstance(reloaded_writer, WriterConfig)
    assert original_writer.partitioning == reloaded_writer.partitioning
    assert original_writer.clustering == reloaded_writer.clustering == ["region", "department"]


# -- Backward compatibility ----------------------------------------------------------------------


def test_target_node_with_no_writer_block_loads_and_round_trips(tmp_path: Path) -> None:
    """A `target` node with no `writer` key loads as `writer=None` and round-trips unchanged."""
    path = tmp_path / "graph.yaml"
    path.write_text(_NO_WRITER_YAML_DOC)
    loaded = load_graph_from_yaml(path)

    target_node = loaded.nodes[1]
    assert isinstance(target_node.config, TargetNodeConfig)
    assert target_node.config.writer is None

    dump_path = tmp_path / "dumped.yaml"
    dump_graph_to_yaml(loaded, dump_path)
    reloaded = load_graph_from_yaml(dump_path)

    assert loaded == reloaded
    assert reloaded.nodes[1].config.writer is None  # type: ignore[union-attr]

    dumped_text = dump_path.read_text()
    assert "writer" not in dumped_text


def test_target_node_config_with_no_config_at_all_still_round_trips(tmp_path: Path) -> None:
    """A `target` node with no `config` key at all still loads/round-trips (DANDER-10 baseline)."""
    node = Node(id="n1", type="target", name="n")
    assert node.config == TargetNodeConfig(writer=None)

    graph = PipelineGraph(name="g", nodes=[node])
    path = tmp_path / "graph.yaml"
    dump_graph_to_yaml(graph, path)
    reloaded = load_graph_from_yaml(path)
    assert reloaded == graph
