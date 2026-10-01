"""Static change previews explain impact without saving graphs or reading warehouse rows."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from dander.control.change_preview import compare_graph_changes
from dander.control.graph_store import GraphStoreDocumentError, canonicalize_graph_document
from dander.control.models import PipelineGraphDocument


def _graph() -> dict[str, Any]:
    def node(node_id: str, kind: str) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if kind == "source":
            config = {"connector": "records", "endpoint": node_id}
        elif kind == "target":
            config = {
                "writer": {
                    "write_mode": "replace",
                    "destination": {"dataset": "analytics", "table": node_id},
                }
            }
        return {
            "id": node_id,
            "name": node_id,
            "type": kind,
            "config": config,
            "fields": [{"name": "id", "type": "STRING"}],
        }

    return {
        "name": "example",
        "nodes": [
            node("source_a", "source"),
            node("transform", "transform"),
            node("output_a", "target"),
            node("source_b", "source"),
            node("output_b", "target"),
        ],
        "edges": [
            {"from": "source_a", "to": "transform"},
            {"from": "transform", "to": "output_a"},
            {"from": "source_b", "to": "output_b"},
        ],
    }


def _document(payload: dict[str, Any]) -> PipelineGraphDocument:
    return PipelineGraphDocument.model_validate(payload)


def test_source_edit_marks_only_its_downstream_output_but_full_run_still_writes_both() -> None:
    baseline = _graph()
    candidate = deepcopy(baseline)
    candidate["nodes"][0]["config"]["endpoint"] = "changed_endpoint"
    candidate["nodes"][0]["config"]["private_note"] = "do-not-copy-this-configuration"

    result = compare_graph_changes(_document(baseline), _document(candidate))

    assert result.baseline_content_sha256 == canonicalize_graph_document(baseline).content_sha256
    assert result.candidate_content_sha256 == canonicalize_graph_document(candidate).content_sha256
    assert result.node_changes[0].node_id == "source_a"
    assert result.node_changes[0].change == "modified"
    assert result.node_changes[0].changed_properties == ("config",)
    assert result.affected_outputs == ("output_a",)
    assert result.run_output_ids == ("output_a", "output_b")
    assert not result.outputs[1].affected
    assert "do-not-copy-this-configuration" not in result.model_dump_json()
    assert baseline == _graph()


@pytest.mark.parametrize("remove_node", [False, True])
def test_disconnecting_a_branch_retains_its_previous_downstream_impact(remove_node: bool) -> None:
    baseline = _graph()
    candidate = deepcopy(baseline)
    candidate["edges"] = [edge for edge in candidate["edges"] if edge["from"] != "source_a"]
    if remove_node:
        candidate["nodes"] = [node for node in candidate["nodes"] if node["id"] != "source_a"]

    result = compare_graph_changes(_document(baseline), _document(candidate))

    assert result.affected_outputs == ("output_a",)
    assert result.connection_changes[0].change == "removed"
    assert result.connection_changes[0].target == "transform"
    if remove_node:
        assert result.node_changes[0].change == "removed"


def test_layout_only_edit_changes_identity_without_claiming_data_impact() -> None:
    baseline = _graph()
    candidate = deepcopy(baseline)
    candidate["nodes"][0]["visual"] = {"position": {"x": 20, "y": 30}}

    result = compare_graph_changes(_document(baseline), _document(candidate))

    assert result.baseline_content_sha256 != result.candidate_content_sha256
    assert result.node_changes[0].change == "layout_only"
    assert not result.node_changes[0].affects_data
    assert result.affected_outputs == ()
    assert result.run_output_ids == ("output_a", "output_b")


def test_changed_field_mapping_marks_downstream_output_without_echoing_expression_values() -> None:
    baseline = _graph()
    baseline["edges"][0]["mappings"] = [{"source": "id", "target": "id"}]
    candidate = deepcopy(baseline)
    candidate["edges"][0]["mappings"] = [
        {
            "source": None,
            "target": "id",
            "transformation": {"kind": "constant", "constant": "private-config-value"},
        }
    ]

    result = compare_graph_changes(_document(baseline), _document(candidate))

    assert result.node_changes == ()
    assert result.connection_changes[0].change == "modified"
    assert result.affected_outputs == ("output_a",)
    assert "private-config-value" not in result.model_dump_json()


@pytest.mark.parametrize("schedule_changed", [False, True])
def test_graph_schedule_change_affects_all_outputs_but_graph_title_does_not(
    schedule_changed: bool,
) -> None:
    baseline = _graph()
    candidate = deepcopy(baseline)
    if schedule_changed:
        candidate["trigger"] = {"kind": "schedule", "cron": "0 6 * * *"}
    else:
        candidate["name"] = "A clearer title"

    result = compare_graph_changes(_document(baseline), _document(candidate))

    assert result.graph_properties_changed == (("trigger",) if schedule_changed else ("name",))
    assert result.affected_outputs == (("output_a", "output_b") if schedule_changed else ())


def test_moving_and_removing_outputs_explains_existing_data_is_retained() -> None:
    baseline = _graph()
    candidate = deepcopy(baseline)
    candidate["nodes"][2]["config"]["writer"]["destination"]["table"] = "new_output"
    candidate["nodes"] = [node for node in candidate["nodes"] if node["id"] != "output_b"]
    candidate["edges"] = [edge for edge in candidate["edges"] if edge["to"] != "output_b"]

    result = compare_graph_changes(_document(baseline), _document(candidate))
    moved, removed = result.outputs

    assert moved.before is not None and moved.before.writer is not None
    assert moved.after is not None and moved.after.writer is not None
    assert moved.before.writer.destination.table == "output_a"
    assert moved.after.writer.destination.table == "new_output"
    assert "previous destination is retained" in moved.effect
    assert "Replaces all rows" in moved.effect
    assert removed.before is not None
    assert removed.after is None
    assert "existing warehouse data is not deleted" in removed.effect
    assert result.affected_outputs == ("output_a", "output_b")
    assert result.run_output_ids == ("output_a",)


def test_added_output_reports_declared_schema_and_unknown_cost_without_execution_claim() -> None:
    baseline = _graph()
    candidate = deepcopy(baseline)
    new_target = deepcopy(candidate["nodes"][2])
    new_target["id"] = "output_c"
    new_target["name"] = "Another output"
    new_target["config"]["writer"]["destination"]["table"] = "output_c"
    new_target["config"]["writer"]["destination"]["business_key"] = ["id"]
    new_target["config"]["writer"]["write_mode"] = "scd1"
    candidate["nodes"].append(new_target)
    candidate["edges"].append({"from": "transform", "to": "output_c"})

    result = compare_graph_changes(_document(baseline), _document(candidate))
    added = result.outputs[2]

    assert added.before is None
    assert added.after is not None
    assert added.after.field_names == ("id",)
    assert added.after.writer is not None
    assert added.after.writer.write_mode == "scd1"
    assert "New graph output" in added.effect
    assert "Updates matching business keys" in added.effect
    assert result.affected_outputs == ("output_c",)
    assert result.estimates.rows_written is None
    assert result.estimates.cost_usd is None
    assert any("not proof that this graph can execute" in note for note in result.limitations)


def test_target_without_writer_and_reordered_graph_remain_honest_and_deterministic() -> None:
    baseline = _graph()
    baseline["nodes"][2]["config"] = {}
    candidate = deepcopy(baseline)
    candidate["nodes"].reverse()
    candidate["edges"].reverse()

    result = compare_graph_changes(_document(baseline), _document(candidate))

    assert result.node_changes == ()
    assert result.connection_changes == ()
    assert result.affected_outputs == ()
    assert result.run_output_ids == ("output_a", "output_b")
    assert "no declared writer" in result.outputs[0].effect


def test_union_of_old_and_new_edges_can_be_cyclic_without_hanging_preview() -> None:
    baseline = _graph()
    other_transform = deepcopy(baseline["nodes"][1])
    other_transform["id"] = "other_transform"
    baseline["nodes"].append(other_transform)
    baseline["edges"] = [
        {"from": "source_a", "to": "transform"},
        {"from": "transform", "to": "other_transform"},
        {"from": "other_transform", "to": "output_a"},
        {"from": "source_b", "to": "output_b"},
    ]
    candidate = deepcopy(baseline)
    candidate["edges"] = [
        {"from": "source_a", "to": "other_transform"},
        {"from": "other_transform", "to": "transform"},
        {"from": "transform", "to": "output_a"},
        {"from": "source_b", "to": "output_b"},
    ]

    result = compare_graph_changes(_document(baseline), _document(candidate))

    assert result.affected_outputs == ("output_a",)


def test_preview_reuses_graph_store_inline_credential_rejection() -> None:
    baseline = _graph()
    candidate = deepcopy(baseline)
    candidate["nodes"][0]["config"]["password"] = "synthetic-secret"

    with pytest.raises(GraphStoreDocumentError, match="canonical graph contract"):
        compare_graph_changes(_document(baseline), _document(candidate))
