"""Artifact-level checks for the generated Control contract bundle."""

from __future__ import annotations

import hashlib
import json

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from dander.control.bundle import (
    BUNDLE_ID,
    JSON_SCHEMA_DIALECT,
    PACKAGED_BUNDLE_DIRECTORY,
    bundle_drift,
)


def _read(path: str) -> object:
    return json.loads((PACKAGED_BUNDLE_DIRECTORY / path).read_text(encoding="utf-8"))


def _graph_validator() -> Draft202012Validator:
    schema = _read("schemas/pipeline-graph.schema.json")
    assert isinstance(schema, dict)
    return Draft202012Validator(schema)


def test_committed_bundle_is_deterministic_and_self_consistent() -> None:
    assert bundle_drift() == ()
    manifest = _read("manifest.json")
    assert isinstance(manifest, dict)
    assert manifest["bundle_id"] == BUNDLE_ID
    assert manifest["json_schema_dialect"] == JSON_SCHEMA_DIALECT

    digest = hashlib.sha256()
    for entry in manifest["files"]:
        path = entry["path"]
        content = (PACKAGED_BUNDLE_DIRECTORY / path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
    assert digest.hexdigest() == manifest["bundle_sha256"]


def test_every_schema_and_fixture_passes_independent_draft_2020_12_validation() -> None:
    manifest = _read("manifest.json")
    assert isinstance(manifest, dict)
    schemas: dict[str, Draft202012Validator] = {}
    for entry in manifest["files"]:
        if entry["kind"] != "schema":
            continue
        schema = _read(entry["path"])
        assert isinstance(schema, dict)
        assert schema["$schema"] == JSON_SCHEMA_DIALECT
        assert schema["$id"] == f"urn:dander:control:contracts:v1:{entry['contract']}"
        Draft202012Validator.check_schema(schema)
        schemas[entry["contract"]] = Draft202012Validator(schema)

    for entry in manifest["files"]:
        if entry["kind"] == "fixture":
            schemas[entry["contract"]].validate(_read(entry["path"]))


@pytest.mark.parametrize(
    "payload",
    [
        {
            "name": "wrong_known_config",
            "nodes": [
                {
                    "id": "source",
                    "type": "source",
                    "name": "Source",
                    "config": {"request": {"method": "BOGUS"}},
                    "fields": [],
                }
            ],
            "edges": [],
        },
        {
            "name": "wrong_operation_params",
            "nodes": [
                {
                    "id": "transform",
                    "type": "transform",
                    "name": "Transform",
                    "config": {
                        "operations": [{"kind": "truncate_string", "params": {"field": "name"}}]
                    },
                    "fields": [],
                }
            ],
            "edges": [],
        },
        {
            "name": "authored_direct_transport",
            "nodes": [
                {
                    "id": "target",
                    "type": "target",
                    "name": "Target",
                    "config": {
                        "writer": {
                            "write_mode": "snapshot",
                            "destination": {"dataset": "analytics", "table": "records"},
                            "transport": "direct",
                        }
                    },
                    "fields": [],
                }
            ],
            "edges": [],
        },
        {
            "name": "unknown_writer_property",
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
        },
        {"name": "strict_extra", "nodes": [], "edges": [], "unexpected": True},
        {
            "name": "strict_edge",
            "nodes": [
                {"id": "a", "type": "task", "name": "A", "config": {}, "fields": []},
                {"id": "b", "type": "task", "name": "B", "config": {}, "fields": []},
            ],
            "edges": [{"from": "a", "to": "b", "unexpected": True}],
        },
    ],
)
def test_emitted_graph_schema_rejects_fallback_and_strictness_escapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _graph_validator().validate(payload)


def test_emitted_graph_schema_preserves_unknown_extension_config_and_legacy_alias() -> None:
    validator = _graph_validator()
    payload = {
        "name": "extension",
        "nodes": [
            {
                "id": "custom",
                "type": "task",
                "name": "Custom",
                "params": {"nested": {"enabled": True}, "items": [1, "two"]},
                "fields": [],
            }
        ],
        "edges": [],
    }
    validator.validate(payload)


def test_bundle_contains_only_json_contract_assets() -> None:
    paths = [path for path in PACKAGED_BUNDLE_DIRECTORY.rglob("*") if path.is_file()]
    assert paths
    assert all(path.suffix == ".json" for path in paths)
    assert not any(path.name.endswith((".tfstate", ".tfplan")) for path in paths)
