"""Tests for the bounded Druff-to-Dander graph document bridge."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

import pytest
import yaml

from dander.pipeline import Node, PipelineGraph, dump_graph_to_yaml, load_graph_from_yaml
from dander.pipeline.graph import NodeVisual, Position
from dander.pipeline.graph_deployment import GraphDeploymentPreview
from dander.pipeline.graph_operations import (
    CloudRunExecution,
    GraphOperationConflictError,
    GraphOperations,
)
from dander.pipeline.graph_service import (
    CONNECTORS_API_PATH,
    GRAPH_API_PATH,
    GRAPH_PREVIEW_API_PATH,
    GRAPH_RUN_API_PATH,
    GRAPH_STATUS_API_PATH,
    GRAPH_VALIDATE_API_PATH,
    OPERATIONS_API_PATH,
    PLUGIN_CATALOG_API_PATH,
    GraphDocumentConflictError,
    GraphDocumentStore,
    GraphDocumentValidationError,
    create_graph_server,
)
from dander.plugins import (
    ConnectorDescriptor,
    ConnectorEndpointDescriptor,
    ConnectorFieldDescriptor,
    ConnectorPlugin,
    InstalledConnectorPlugin,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

ORIGIN = "http://localhost:3000"


def _write_graph(path: Path) -> PipelineGraph:
    graph = PipelineGraph(
        name="visual-edit",
        nodes=[
            Node(
                id="source",
                type="source",
                name="Source",
                config={"connector": "greenhouse", "unmodeled": {"kept": True}},
                visual=NodeVisual(position=Position(x=10, y=20), color="#123456", icon="building"),
            )
        ],
    )
    dump_graph_to_yaml(graph, path)
    return graph


def test_store_saves_valid_graph_and_preserves_unedited_model_fields(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    original = _write_graph(path)
    store = GraphDocumentStore(path)
    opened = store.load()
    payload = opened.graph.model_dump(by_alias=True, mode="json")
    payload["nodes"][0]["visual"]["position"] = {"x": 50, "y": 75}

    saved = store.save(payload, expected_revision=opened.revision)

    assert saved.revision != opened.revision
    reloaded = load_graph_from_yaml(path)
    assert reloaded.nodes[0].visual is not None
    assert reloaded.nodes[0].visual.position == Position(x=50, y=75)
    assert reloaded.nodes[0].visual.color == original.nodes[0].visual.color  # type: ignore[union-attr]
    assert reloaded.nodes[0].visual.icon == original.nodes[0].visual.icon  # type: ignore[union-attr]
    assert reloaded.nodes[0].config.model_dump()["unmodeled"] == {"kept": True}  # type: ignore[union-attr]


def test_store_rejects_stale_revision_without_changing_file(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    _write_graph(path)
    store = GraphDocumentStore(path)
    opened = store.load()
    before = path.read_bytes()

    with pytest.raises(GraphDocumentConflictError, match="changed after Druff opened"):
        store.save(
            opened.graph.model_dump(by_alias=True, mode="json"),
            expected_revision="0" * 64,
        )

    assert path.read_bytes() == before


def test_store_rejects_invalid_graph_without_changing_file(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    _write_graph(path)
    store = GraphDocumentStore(path)
    opened = store.load()
    before = path.read_bytes()
    invalid = opened.graph.model_dump(by_alias=True, mode="json")
    invalid["edges"] = [{"from": "source", "to": "missing"}]

    with pytest.raises(GraphDocumentValidationError, match="Dangling edge"):
        store.save(invalid, expected_revision=opened.revision)

    assert path.read_bytes() == before


def test_store_rejects_unknown_top_level_field_on_load(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    _write_graph(path)
    payload = yaml.safe_load(path.read_text())
    payload["newer_dander_field"] = {"must_not": "disappear"}
    path.write_text(yaml.safe_dump(payload, sort_keys=False))

    with pytest.raises(GraphDocumentValidationError, match="PipelineGraph contract"):
        GraphDocumentStore(path).load()


def test_store_rejects_unknown_nested_field_without_changing_file(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    _write_graph(path)
    store = GraphDocumentStore(path)
    opened = store.load()
    before = path.read_bytes()
    payload = opened.graph.model_dump(by_alias=True, mode="json")
    payload["nodes"][0]["newer_dander_field"] = {"must_not": "disappear"}

    with pytest.raises(GraphDocumentValidationError, match="PipelineGraph contract"):
        store.save(payload, expected_revision=opened.revision)

    assert path.read_bytes() == before


@contextmanager
def _running_server(
    path: Path,
    *,
    operations: GraphOperations | None = None,
    connector_plugins: tuple[InstalledConnectorPlugin, ...] = (),
) -> Iterator[tuple[str, int]]:
    server = create_graph_server(
        path,
        origin=ORIGIN,
        port=0,
        operations=operations,
        connector_plugins=connector_plugins,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address = server.server_address
        yield str(address[0]), int(address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    address: tuple[str, int],
    method: str,
    *,
    path: str = GRAPH_API_PATH,
    payload: object | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    connection = http.client.HTTPConnection(*address, timeout=5)
    body = None if payload is None else json.dumps(payload)
    request_headers = {"Origin": ORIGIN, **(headers or {})}
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    response_body = json.loads(response.read())
    response_headers = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    return response.status, response_body, response_headers


def test_http_get_and_conditional_put_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    _write_graph(path)

    with _running_server(path) as address:
        status, graph, headers = _request(address, "GET")
        assert status == 200
        assert headers["access-control-allow-origin"] == ORIGIN
        graph["nodes"][0]["visual"]["position"] = {"x": 125, "y": 250}

        status, saved, saved_headers = _request(
            address,
            "PUT",
            payload=graph,
            headers={"Content-Type": "application/json", "If-Match": headers["etag"]},
        )

    assert status == 200
    assert saved_headers["etag"] != headers["etag"]
    assert saved["nodes"][0]["visual"]["color"] == "#123456"
    reloaded = load_graph_from_yaml(path)
    assert reloaded.nodes[0].visual is not None
    assert reloaded.nodes[0].visual.position == Position(x=125, y=250)


def test_http_connector_discovery_returns_only_presentation_safe_plugin_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pipeline.yaml"
    _write_graph(path)
    plugin = ConnectorPlugin(
        plugin_id="salesforce",
        api_version=1,
        engine="salesforce_bulk2",
        display_name="Salesforce",
        source_factory=cast("Any", lambda *_args: None),
        connectors=(
            ConnectorDescriptor(
                connector_id="salesforce",
                display_name="Salesforce",
                engine="salesforce_bulk2",
                endpoints=(
                    ConnectorEndpointDescriptor(
                        endpoint_id="accounts",
                        display_name="Accounts",
                        fields=(
                            ConnectorFieldDescriptor(
                                name="Id",
                                display_name="ID",
                                data_type="STRING",
                                required=True,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    installed = InstalledConnectorPlugin(
        plugin=plugin,
        distribution="dander-connector-salesforce",
        version="0.1.0rc1",
    )

    with _running_server(path, connector_plugins=(installed,)) as address:
        status, body, headers = _request(address, "GET", path=CONNECTORS_API_PATH)

    assert status == 200
    assert headers["access-control-allow-origin"] == ORIGIN
    assert body == {
        "connectors": [
            {
                "id": "salesforce",
                "display_name": "Salesforce",
                "engine": "salesforce_bulk2",
                "description": "",
                "plugin": {
                    "id": "salesforce",
                    "distribution": "dander-connector-salesforce",
                    "version": "0.1.0rc1",
                },
                "endpoints": [
                    {
                        "id": "accounts",
                        "display_name": "Accounts",
                        "graph_binding": {"connector": "salesforce", "endpoint": "accounts"},
                        "fields": [
                            {
                                "name": "Id",
                                "display_name": "ID",
                                "data_type": "STRING",
                                "required": True,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    serialized = json.dumps(body).lower()
    assert all(
        secret_name not in serialized
        for secret_name in ("base_url", "auth", "secret", "request_body", "credential")
    )


def test_http_operation_discovery_returns_only_the_executable_canonical_subset(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pipeline.yaml"
    _write_graph(path)

    with _running_server(path) as address:
        status, body, headers = _request(address, "GET", path=OPERATIONS_API_PATH)

    assert status == 200
    assert headers["access-control-allow-origin"] == ORIGIN
    assert body["schema_version"] == 1
    assert [operation["kind"] for operation in body["operations"]] == [
        "trim_whitespace",
        "truncate_string",
        "default_value",
        "filter_rows",
    ]
    serialized = json.dumps(body).lower()
    assert all(
        excluded not in serialized
        for excluded in ("write_back", "deduplicate", "sql_hook", "credential", "secret")
    )


def test_http_plugin_catalog_marks_only_manifest_plugins_installed(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    _write_graph(path)
    installed = InstalledConnectorPlugin(
        plugin=ConnectorPlugin(
            plugin_id="salesforce",
            api_version=1,
            engine="salesforce_bulk2",
            display_name="Salesforce",
            source_factory=cast("Any", lambda *_args: None),
        ),
        distribution="dander-connector-salesforce",
        version="0.1.1",
    )

    with _running_server(path, connector_plugins=(installed,)) as address:
        status, body, headers = _request(address, "GET", path=PLUGIN_CATALOG_API_PATH)

    connectors = {connector["id"]: connector for connector in body["connectors"]}
    assert status == 200
    assert headers["access-control-allow-origin"] == ORIGIN
    assert body["schema_version"] == 1
    assert connectors["salesforce"]["installed"] is True
    assert connectors["salesforce"]["installed_version"] == "0.1.1"
    assert connectors["servicenow"]["installed"] is False
    serialized = json.dumps(body).lower()
    assert all(
        secret_name not in serialized
        for secret_name in ("base_url", "auth", "secret", "request_body", "credential")
    )


def test_http_rejects_wrong_origin_and_stale_save(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    _write_graph(path)

    with _running_server(path) as address:
        status, graph, headers = _request(address, "GET")
        assert status == 200

        connection = http.client.HTTPConnection(*address, timeout=5)
        connection.request("GET", GRAPH_API_PATH, headers={"Origin": "https://example.com"})
        forbidden = connection.getresponse()
        assert forbidden.status == 403
        forbidden.read()
        connection.close()

        status, error, _ = _request(
            address,
            "PUT",
            payload=graph,
            headers={
                "Content-Type": "application/json",
                "If-Match": '"' + ("0" * 64) + '"',
            },
        )

    assert status == 412
    assert "changed" in error["error"]
    assert headers["etag"] == f'"{GraphDocumentStore(path).load().revision}"'


class _Operations:
    def __init__(self) -> None:
        self.validated: list[str] = []
        self.triggered: list[str] = []
        self.conflict = False
        self.previewed: list[str] = []

    def validate(
        self,
        _store: GraphDocumentStore,
        *,
        expected_revision: str,
    ) -> dict[str, object]:
        self.validated.append(expected_revision)
        return {"valid": True, "revision": expected_revision}

    def trigger(
        self,
        _store: GraphDocumentStore,
        *,
        expected_revision: str,
    ) -> CloudRunExecution:
        self.triggered.append(expected_revision)
        if self.conflict:
            raise GraphOperationConflictError("A deployed execution is already active.")
        return CloudRunExecution(name="dander-graph-records-abcde", state="starting")

    def status(self, store: GraphDocumentStore) -> dict[str, object]:
        return {
            "enabled": True,
            "revision": store.load().revision,
            "execution": None,
            "run": None,
        }

    def preview_deployment(
        self,
        _store: GraphDocumentStore,
        *,
        expected_revision: str,
    ) -> GraphDeploymentPreview:
        self.previewed.append(expected_revision)
        return GraphDeploymentPreview(
            revision=expected_revision,
            candidate_image=(
                "us-central1-docker.pkg.dev/proof-project/dander/dander@sha256:" + "a" * 64
            ),
            plan_sha256="b" * 64,
            plan_summary="Plan: 0 to add, 1 to change, 0 to destroy.",
            plan_text="exact human plan",
            affected_jobs=("dander-graph-records",),
        )


def test_http_operations_are_disabled_without_an_operator_binding(tmp_path: Path) -> None:
    graph_file = tmp_path / "pipeline.yaml"
    _write_graph(graph_file)

    with _running_server(graph_file) as address:
        status, body, _ = _request(address, "GET", path=GRAPH_STATUS_API_PATH)
        run_status, run_body, _ = _request(address, "POST", path=GRAPH_RUN_API_PATH)

    assert status == 200
    assert body == {"enabled": False}
    assert run_status == 409
    assert "disabled" in run_body["error"]


def test_http_validate_and_run_require_and_forward_the_open_revision(tmp_path: Path) -> None:
    graph_file = tmp_path / "pipeline.yaml"
    _write_graph(graph_file)
    operations = _Operations()

    with _running_server(
        graph_file,
        operations=cast("GraphOperations", operations),
    ) as address:
        _, _, graph_headers = _request(address, "GET")
        missing_status, missing_body, _ = _request(
            address,
            "POST",
            path=GRAPH_VALIDATE_API_PATH,
        )
        validate_status, validate_body, validate_headers = _request(
            address,
            "POST",
            path=GRAPH_VALIDATE_API_PATH,
            headers={"If-Match": graph_headers["etag"]},
        )
        run_status, run_body, _ = _request(
            address,
            "POST",
            path=GRAPH_RUN_API_PATH,
            headers={"If-Match": graph_headers["etag"]},
        )

    revision = graph_headers["etag"].strip('"')
    assert missing_status == 428
    assert "ETag" in missing_body["error"]
    assert validate_status == 200
    assert validate_body == {"valid": True, "revision": revision}
    assert validate_headers["etag"] == graph_headers["etag"]
    assert run_status == 202
    assert run_body["execution"]["state"] == "starting"
    assert operations.validated == [revision]
    assert operations.triggered == [revision]


def test_http_deployment_preview_returns_only_the_human_plan_projection(tmp_path: Path) -> None:
    graph_file = tmp_path / "pipeline.yaml"
    _write_graph(graph_file)
    operations = _Operations()

    with _running_server(
        graph_file,
        operations=cast("GraphOperations", operations),
    ) as address:
        _, _, graph_headers = _request(address, "GET")
        status, body, _ = _request(
            address,
            "POST",
            path=GRAPH_PREVIEW_API_PATH,
            headers={"If-Match": graph_headers["etag"]},
        )

    revision = graph_headers["etag"].strip('"')
    assert status == 200
    assert body["revision"] == revision
    assert body["plan_text"] == "exact human plan"
    assert "state" not in body
    assert operations.previewed == [revision]


def test_http_run_maps_active_execution_to_conflict(tmp_path: Path) -> None:
    graph_file = tmp_path / "pipeline.yaml"
    _write_graph(graph_file)
    operations = _Operations()
    operations.conflict = True

    with _running_server(
        graph_file,
        operations=cast("GraphOperations", operations),
    ) as address:
        _, _, graph_headers = _request(address, "GET")
        status, body, _ = _request(
            address,
            "POST",
            path=GRAPH_RUN_API_PATH,
            headers={"If-Match": graph_headers["etag"]},
        )

    assert status == 409
    assert "already active" in body["error"]
