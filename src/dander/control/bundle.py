"""Deterministic builder for the packaged Dander Control contract bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from pydantic import BaseModel

from dander.control.models import (
    ApiErrorEnvelope,
    CapabilitiesResponse,
    ConnectorCatalogResponse,
    DeploymentPreviewResponse,
    GraphValidationResponse,
    LogPageResponse,
    MutationResult,
    OperationCatalogResponse,
    PipelineGraphDocument,
    PluginCatalogResponse,
    RunRequest,
    RunStatusResponse,
)
from dander.pipeline.graph import graph_to_payload

BUNDLE_ID: Final = "io.dander.control.contracts/v1"
MANIFEST_SCHEMA: Final = "io.dander.control.contracts-manifest/v1"
JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID_PREFIX: Final = "urn:dander:control:contracts:v1:"
PACKAGED_BUNDLE_DIRECTORY: Final = Path(__file__).resolve().parent / "contracts" / "v1"

CONTRACT_MODELS: Final[dict[str, type[BaseModel]]] = {
    "api-error": ApiErrorEnvelope,
    "capabilities": CapabilitiesResponse,
    "connector-catalog": ConnectorCatalogResponse,
    "deployment-preview": DeploymentPreviewResponse,
    "graph-validation": GraphValidationResponse,
    "log-page": LogPageResponse,
    "mutation-result": MutationResult,
    "operation-catalog": OperationCatalogResponse,
    "pipeline-graph": PipelineGraphDocument,
    "plugin-catalog": PluginCatalogResponse,
    "run-request": RunRequest,
    "run-status": RunStatusResponse,
}


def _graph_fixture() -> dict[str, Any]:
    def make_field(name: str) -> dict[str, Any]:
        return {
            "name": name,
            "type": "STRING",
            "nullable": True,
            "tests": [],
            "extensions": [],
            "metadata": {},
        }

    target_fields = [make_field("id"), make_field("updated_at")]
    graph = PipelineGraphDocument.model_validate(
        {
            "name": "control_contract_fixture",
            "trigger": {
                "kind": "schedule",
                "cron": "0 6 * * *",
                "depends_on": [],
                "event": None,
                "metadata": {},
            },
            "nodes": [
                {
                    "id": "source_records",
                    "type": "source",
                    "name": "Records",
                    "config": {
                        "connector": "records",
                        "endpoint": "records",
                        "request": {
                            "method": "GET",
                            "headers": {"Authorization": "env:RECORDS_TOKEN"},
                            "query_params": {"since": "field:updated_at"},
                            "body": None,
                        },
                        "provider_hint": "preserved-extension",
                    },
                    "fields": [
                        {
                            **make_field("id"),
                            "nullable": False,
                            "tests": [
                                {
                                    "kind": "not_null",
                                    "values": [],
                                    "to": None,
                                    "field": None,
                                    "metadata": {},
                                }
                            ],
                            "extensions": [
                                {
                                    "provider": "redshift",
                                    "name": "source_type",
                                    "value": "varchar",
                                }
                            ],
                        },
                        make_field("name"),
                        make_field("updated_at"),
                    ],
                    "trigger": {
                        "kind": "manual",
                        "cron": None,
                        "depends_on": [],
                        "event": "operator",
                        "metadata": {},
                    },
                    "cursor": {
                        "field": "updated_at",
                        "kind": "timestamp",
                        "params": {"format": "rfc3339"},
                        "metadata": {},
                    },
                    "visual": {
                        "position": {"x": 40.0, "y": 80.0},
                        "color": "blue",
                        "icon": "database",
                    },
                },
                {
                    "id": "source_regions",
                    "type": "source",
                    "name": "Regions",
                    "config": {},
                    "fields": [make_field("id"), make_field("region")],
                },
                {
                    "id": "normalize_records",
                    "type": "transform",
                    "name": "Normalize records",
                    "config": {
                        "join": {
                            "left_input": "source_records",
                            "right_input": "source_regions",
                            "type": "left",
                            "keys": [{"left": "id", "right": "id"}],
                        },
                        "operations": [
                            {
                                "kind": "trim_whitespace",
                                "params": {"field": "name_short"},
                                "metadata": {},
                            },
                            {
                                "kind": "truncate_string",
                                "params": {"field": "normalized", "max_length": 64},
                                "metadata": {},
                            },
                            {
                                "kind": "default_value",
                                "params": {"field": "status", "default": "unknown"},
                                "metadata": {},
                            },
                            {
                                "kind": "filter_rows",
                                "params": {
                                    "conditions": [
                                        {"field": "id", "op": "is_not_null", "value": None}
                                    ],
                                    "logic": "all",
                                },
                                "metadata": {},
                            },
                        ],
                    },
                    "fields": [
                        make_field("id"),
                        make_field("name_short"),
                        make_field("normalized"),
                        make_field("status"),
                        make_field("region"),
                        make_field("updated_at"),
                    ],
                    "trigger": {
                        "kind": "dependency",
                        "cron": None,
                        "depends_on": ["source_records", "source_regions"],
                        "event": None,
                        "metadata": {},
                    },
                },
                *_writer_nodes(target_fields),
                {
                    "id": "notify_operator",
                    "type": "task",
                    "name": "Notify operator",
                    "config": {
                        "channel": "synthetic",
                        "nested": {"enabled": True},
                    },
                    "fields": [],
                },
            ],
            "edges": [
                {
                    "from": "source_records",
                    "to": "normalize_records",
                    "metadata": {},
                    "mappings": [
                        {
                            "source": "id",
                            "target": "id",
                            "transformation": {
                                "kind": "direct",
                                "expression": None,
                                "constant": None,
                                "function": None,
                                "arguments": {},
                                "inputs": [],
                                "metadata": {},
                            },
                            "metadata": {},
                        },
                        {
                            "source": "name",
                            "target": "name_short",
                            "transformation": {
                                "kind": "expression",
                                "expression": "SUBSTR(name, 1, 64)",
                                "constant": None,
                                "function": None,
                                "arguments": {},
                                "inputs": ["name"],
                                "metadata": {},
                            },
                            "metadata": {},
                        },
                        {
                            "source": None,
                            "target": "status",
                            "transformation": {
                                "kind": "constant",
                                "expression": None,
                                "constant": "ready",
                                "function": None,
                                "arguments": {},
                                "inputs": [],
                                "metadata": {},
                            },
                            "metadata": {},
                        },
                        {
                            "source": "name",
                            "target": "normalized",
                            "transformation": {
                                "kind": "custom_code",
                                "expression": None,
                                "constant": None,
                                "function": "transforms.normalize_name",
                                "arguments": {"locale": "en-US"},
                                "inputs": ["name"],
                                "metadata": {},
                            },
                            "metadata": {},
                        },
                        {
                            "source": "updated_at",
                            "target": "updated_at",
                            "transformation": None,
                            "metadata": {},
                        },
                    ],
                    "join": {
                        "type": "left",
                        "keys": [{"left": "id", "right": "id"}],
                        "metadata": {},
                    },
                },
                {
                    "from": "source_regions",
                    "to": "normalize_records",
                    "metadata": {},
                    "mappings": [
                        {
                            "source": "region",
                            "target": "region",
                            "transformation": None,
                            "metadata": {},
                        }
                    ],
                },
                *[
                    {
                        "from": "normalize_records",
                        "to": node_id,
                        "metadata": {},
                        "mappings": [
                            {
                                "source": "id",
                                "target": "id",
                                "transformation": None,
                                "metadata": {},
                            },
                            {
                                "source": "updated_at",
                                "target": "updated_at",
                                "transformation": None,
                                "metadata": {},
                            },
                        ],
                    }
                    for node_id in (
                        "target_scd1",
                        "target_scd2",
                        "target_snapshot",
                        "target_incremental",
                        "target_replace",
                    )
                ],
            ],
        }
    )
    return graph_to_payload(graph.to_domain())


def _writer_nodes(target_fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specifications: tuple[tuple[str, str, str, list[str], str | None], ...] = (
        ("target_scd1", "scd1", "load_job", ["id"], None),
        ("target_scd2", "scd2", "copy", ["id"], None),
        ("target_snapshot", "snapshot", "load_job", [], None),
        ("target_incremental", "incremental", "storage_write", ["id"], "updated_at"),
        ("target_replace", "replace", "copy", [], None),
    )
    nodes: list[dict[str, Any]] = []
    for node_id, mode, transport, key, cursor in specifications:
        nodes.append(
            {
                "id": node_id,
                "type": "target",
                "name": node_id.replace("_", " ").title(),
                "config": {
                    "writer": {
                        "write_mode": mode,
                        "destination": {
                            "project": None,
                            "dataset": "analytics",
                            "table": node_id,
                            "business_key": key,
                        },
                        "cursor_field": cursor,
                        "partitioning": None,
                        "clustering": [],
                        "max_batch_rows": 10_000,
                        "schema_evolution": "strict",
                        "transport": transport,
                    }
                },
                "fields": [dict(item) for item in target_fields],
            }
        )
    return nodes


def _fixtures() -> dict[str, tuple[str, dict[str, Any]]]:
    digest = "0" * 64
    return {
        "api-error": (
            "api-error",
            {
                "error": {
                    "code": "graph_invalid",
                    "message": "The graph is invalid.",
                    "correlation_id": "corr-synthetic",
                    "details": [
                        {"location": "nodes.0", "code": "invalid", "message": "Invalid node."}
                    ],
                }
            },
        ),
        "capabilities": (
            "capabilities",
            {
                "api_version": "v1",
                "dander_version": "0.0.0",
                "contract": {"id": BUNDLE_ID, "sha256": digest},
                "compatibility": {
                    "minimum_druff_contract": "1.0.0",
                    "maximum_druff_contract": "1.x",
                },
                "operations": ["graph.read", "graph.edit", "graph.validate", "run.read"],
                "limits": {
                    "max_graph_bytes": 5 * 1024 * 1024,
                    "max_page_size": 100,
                    "max_log_records": 500,
                },
            },
        ),
        "connector-catalog": (
            "connector-catalog",
            {
                "connectors": [
                    {
                        "id": "records",
                        "display_name": "Records",
                        "engine": "synthetic",
                        "description": "Synthetic connector metadata.",
                        "plugin": {
                            "id": "records",
                            "distribution": "dander-connector-records",
                            "version": "1.0.0",
                        },
                        "endpoints": [
                            {
                                "id": "records",
                                "display_name": "Records",
                                "graph_binding": {
                                    "connector": "records",
                                    "endpoint": "records",
                                },
                                "fields": [
                                    {
                                        "name": "id",
                                        "display_name": "ID",
                                        "data_type": "STRING",
                                        "required": True,
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        ),
        "deployment-preview": (
            "deployment-preview",
            {
                "revision": "opaque-revision",
                "candidate_image": "registry.invalid/dander@sha256:" + digest,
                "plan_sha256": digest,
                "plan_summary": "Plan: 1 to add, 0 to change, 0 to destroy.",
                "plan_text": "Synthetic bounded plan.",
                "affected_jobs": ["records"],
            },
        ),
        "graph-validation": (
            "graph-validation",
            {
                "valid": False,
                "graph_name": "control_contract_fixture",
                "content_sha256": digest,
                "issues": [
                    {
                        "location": "nodes.0.config",
                        "message": "Invalid node config.",
                        "type": "value_error",
                    }
                ],
            },
        ),
        "log-page": (
            "log-page",
            {
                "records": [
                    {
                        "timestamp": "2026-08-13T12:00:00Z",
                        "level": "info",
                        "code": "run_started",
                        "message": "Run started.",
                        "correlation_id": "corr-synthetic",
                    }
                ],
                "next_cursor": None,
            },
        ),
        "mutation-result": (
            "mutation-result",
            {
                "operation": "replay",
                "accepted": True,
                "run_id": "run-synthetic",
                "resulting_run_id": "run-replay-synthetic",
                "state": "queued",
            },
        ),
        "operation-catalog": (
            "operation-catalog",
            {
                "schema_version": 1,
                "operations": [
                    {
                        "kind": "truncate_string",
                        "display_name": "Truncate string",
                        "description": "Limit a string field.",
                        "parameters": [
                            {
                                "name": "max_length",
                                "display_name": "Maximum length",
                                "control": "integer",
                                "required": True,
                                "minimum": 0,
                                "default": None,
                                "options": [],
                                "operators": [],
                            }
                        ],
                    }
                ],
            },
        ),
        "pipeline-graph": ("pipeline-graph", _graph_fixture()),
        "pipeline-graph-alias-input": (
            "pipeline-graph",
            {
                "name": "legacy_alias",
                "nodes": [
                    {
                        "id": "extension",
                        "type": "task",
                        "name": "Extension",
                        "params": {"preserved": {"value": 1}},
                        "fields": [],
                    }
                ],
                "edges": [],
            },
        ),
        "plugin-catalog": (
            "plugin-catalog",
            {
                "schema_version": 1,
                "dander_version": "0.0.0",
                "connectors": [
                    {
                        "id": "records",
                        "display_name": "Records",
                        "description": "Synthetic catalog entry.",
                        "distribution": "dander-connector-records",
                        "version": "1.0.0",
                        "dander_specifier": ">=0.9,<1",
                        "compatible": True,
                        "support_status": "synthetic",
                        "validation_status": "test-only",
                        "documentation_url": "https://example.invalid/docs",
                        "pypi_url": "https://example.invalid/package",
                        "repository_url": "https://example.invalid/repository",
                        "installed": False,
                        "installed_version": None,
                    }
                ],
            },
        ),
        "run-request": (
            "run-request",
            {"expected_revision": "opaque-revision", "idempotency_key": "idem-synthetic"},
        ),
        "run-status": (
            "run-status",
            {
                "run_id": "run-synthetic",
                "state": "succeeded",
                "stage": "complete",
                "started_at": "2026-08-13T12:00:00Z",
                "finished_at": "2026-08-13T12:01:00Z",
                "endpoints": 1,
                "extracted": 2,
                "affected": 2,
                "models": 1,
                "assertions": 1,
                "assets": 1,
                "failure_code": None,
                "failure_summary": None,
                "can_cancel": False,
                "can_replay": True,
                "logs_available": True,
            },
        ),
    }


def render_bundle() -> dict[str, bytes]:
    """Render every schema, fixture, and the non-self-referential digest manifest."""
    files: dict[str, bytes] = {}
    for name, model in sorted(CONTRACT_MODELS.items()):
        schema = model.model_json_schema(mode="validation", ref_template="#/$defs/{model}")
        schema["$schema"] = JSON_SCHEMA_DIALECT
        schema["$id"] = SCHEMA_ID_PREFIX + name
        files[f"schemas/{name}.schema.json"] = _json_bytes(schema)

    fixture_contracts: dict[str, str] = {}
    for fixture_name, (contract_name, payload) in sorted(_fixtures().items()):
        CONTRACT_MODELS[contract_name].model_validate(payload)
        path = f"fixtures/{fixture_name}.json"
        files[path] = _json_bytes(payload)
        fixture_contracts[path] = contract_name

    entries = [
        {
            "path": path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "kind": "schema" if path.startswith("schemas/") else "fixture",
            "contract": (
                Path(path).name.removesuffix(".schema.json")
                if path.startswith("schemas/")
                else fixture_contracts[path]
            ),
        }
        for path, content in sorted(files.items())
    ]
    bundle_digest = _bundle_digest(files)
    files["manifest.json"] = _json_bytes(
        {
            "schema": MANIFEST_SCHEMA,
            "bundle_id": BUNDLE_ID,
            "json_schema_dialect": JSON_SCHEMA_DIALECT,
            "reference_convention": "self-contained internal #/$defs references",
            "bundle_sha256": bundle_digest,
            "files": entries,
        }
    )
    return files


def write_bundle(destination: Path = PACKAGED_BUNDLE_DIRECTORY) -> None:
    """Replace generated JSON files in ``destination`` with the deterministic bundle."""
    rendered = render_bundle()
    destination.mkdir(parents=True, exist_ok=True)
    for existing in sorted(destination.rglob("*.json")):
        relative = existing.relative_to(destination).as_posix()
        if relative not in rendered:
            existing.unlink()
    for relative, content in rendered.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def bundle_drift(destination: Path = PACKAGED_BUNDLE_DIRECTORY) -> tuple[str, ...]:
    """Return deterministic differences between generated and committed bundle files."""
    rendered = render_bundle()
    actual_paths = (
        {
            path.relative_to(destination).as_posix()
            for path in destination.rglob("*.json")
            if path.is_file()
        }
        if destination.is_dir()
        else set()
    )
    errors: list[str] = []
    for path in sorted(set(rendered) | actual_paths):
        expected = rendered.get(path)
        actual_path = destination / path
        actual = actual_path.read_bytes() if actual_path.is_file() else None
        if actual != expected:
            errors.append(path)
    return tuple(errors)


def _bundle_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (rendered + "\n").encode()
