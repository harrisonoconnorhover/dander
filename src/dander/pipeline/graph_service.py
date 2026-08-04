"""Local, single-file HTTP bridge for visual ``PipelineGraph`` editors.

The bridge deliberately exposes one graph file selected by the operator at startup. Browser
clients never submit filesystem paths, and Dander remains the authority for parsing, semantic
validation, canonical serialization, and conflict detection.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import yaml
from pydantic import ValidationError

from dander.pipeline.errors import GraphValidationError
from dander.pipeline.graph import (
    PipelineGraph,
    dump_graph_to_json,
    dump_graph_to_yaml,
)
from dander.pipeline.graph_operations import (
    GraphOperationConflictError,
    GraphOperationRevisionError,
    GraphOperations,
    GraphOperationUnavailableError,
    GraphOperationValidationError,
)
from dander.pipeline.graph_ops import validate_field_wiring

if TYPE_CHECKING:
    from dander.plugins import InstalledConnectorPlugin

GRAPH_API_PATH = "/v1/graph"
CONNECTORS_API_PATH = "/v1/connectors"
GRAPH_STATUS_API_PATH = "/v1/graph/status"
GRAPH_VALIDATE_API_PATH = "/v1/graph/validate"
GRAPH_RUN_API_PATH = "/v1/graph/run"
GRAPH_PREVIEW_API_PATH = "/v1/graph/deployment-preview"
MAX_GRAPH_DOCUMENT_BYTES = 5 * 1024 * 1024


class GraphDocumentError(Exception):
    """Base error for graph-document service failures safe to report to an operator."""


class GraphDocumentConflictError(GraphDocumentError):
    """Raised when a save is based on a graph revision that is no longer current."""


class GraphDocumentValidationError(GraphDocumentError):
    """Raised when a graph document cannot be parsed or fails Dander validation."""

    def __init__(self, message: str, *, details: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.details = details or []


@dataclass(frozen=True)
class GraphDocument:
    """One validated graph and the SHA-256 revision of its serialized source file."""

    graph: PipelineGraph
    revision: str


class GraphDocumentStore:
    """Validated, optimistic-concurrency persistence for one explicit graph file."""

    def __init__(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved.suffix.lower() not in {".yaml", ".yml", ".json"}:
            raise GraphDocumentError("Graph file must use a .yaml, .yml, or .json extension.")
        if not resolved.is_file():
            raise GraphDocumentError(f"Graph file does not exist: {resolved}")
        self.path = resolved
        self._lock = threading.Lock()

    def load(self) -> GraphDocument:
        """Load and validate the configured graph, returning its current revision."""
        with self._lock:
            return self._load_unlocked()

    def save(self, payload: object, *, expected_revision: str) -> GraphDocument:
        """Validate and atomically save ``payload`` if its base revision is still current."""
        graph = _validate_graph_payload(payload)
        with self._lock:
            current_revision = _revision(self.path.read_bytes())
            if expected_revision != current_revision:
                raise GraphDocumentConflictError(
                    "The graph changed after Druff opened it. Reload before saving again."
                )

            temporary_path = self._write_temporary(graph)
            try:
                # Detect a normal external-editor change during serialization instead of
                # silently replacing it. The final os.replace is atomic for readers.
                latest_revision = _revision(self.path.read_bytes())
                if latest_revision != expected_revision:
                    raise GraphDocumentConflictError(
                        "The graph changed while Druff was saving it. Reload before saving again."
                    )
                os.replace(temporary_path, self.path)
                _sync_directory(self.path.parent)
            finally:
                temporary_path.unlink(missing_ok=True)

            return self._load_unlocked()

    def _load_unlocked(self) -> GraphDocument:
        raw = self.path.read_bytes()
        try:
            text = raw.decode()
            payload = (
                json.loads(text) if self.path.suffix.lower() == ".json" else yaml.safe_load(text)
            )
            graph = PipelineGraph.model_validate(payload, extra="forbid")
        except ValidationError as error:
            raise _pydantic_graph_error(error) from error
        except (json.JSONDecodeError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise GraphDocumentValidationError(
                "The configured graph file is not valid YAML/JSON."
            ) from error

        _validate_graph_semantics(graph)
        return GraphDocument(graph=graph, revision=_revision(raw))

    def _write_temporary(self, graph: PipelineGraph) -> Path:
        descriptor, raw_path = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.druff-",
            suffix=self.path.suffix,
        )
        os.close(descriptor)
        temporary_path = Path(raw_path)
        try:
            if self.path.suffix.lower() == ".json":
                dump_graph_to_json(graph, temporary_path)
            else:
                dump_graph_to_yaml(graph, temporary_path)
            with temporary_path.open("rb") as temporary_file:
                os.fsync(temporary_file.fileno())
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return temporary_path


def create_graph_server(
    graph_file: Path,
    *,
    origin: str = "http://localhost:3000",
    port: int = 8765,
    operations: GraphOperations | None = None,
    connector_plugins: tuple[InstalledConnectorPlugin, ...] = (),
) -> ThreadingHTTPServer:
    """Create a loopback-only graph server without starting its blocking event loop."""
    store = GraphDocumentStore(graph_file)
    # Fail before binding if the configured document is already invalid.
    store.load()
    store_for_handler = store
    origin_for_handler = origin
    operations_for_handler = operations
    connector_catalog_for_handler = _connector_catalog(connector_plugins)

    class BoundGraphRequestHandler(_GraphRequestHandler):
        store = store_for_handler
        allowed_origin = origin_for_handler
        operations = operations_for_handler
        connector_catalog = connector_catalog_for_handler

    return ThreadingHTTPServer(("127.0.0.1", port), BoundGraphRequestHandler)


def serve_graph_file(
    graph_file: Path,
    *,
    origin: str = "http://localhost:3000",
    port: int = 8765,
    operations: GraphOperations | None = None,
    connector_plugins: tuple[InstalledConnectorPlugin, ...] = (),
) -> None:
    """Serve one graph file on loopback until interrupted."""
    server = create_graph_server(
        graph_file,
        origin=origin,
        port=port,
        operations=operations,
        connector_plugins=connector_plugins,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


class _GraphRequestHandler(BaseHTTPRequestHandler):
    """HTTP/1.1 handler for the deliberately tiny graph-document API."""

    protocol_version = "HTTP/1.1"
    store: ClassVar[GraphDocumentStore]
    allowed_origin: ClassVar[str]
    operations: ClassVar[GraphOperations | None]
    connector_catalog: ClassVar[dict[str, object]]

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._request_is_allowed(
            {
                GRAPH_API_PATH,
                CONNECTORS_API_PATH,
                GRAPH_STATUS_API_PATH,
                GRAPH_VALIDATE_API_PATH,
                GRAPH_RUN_API_PATH,
                GRAPH_PREVIEW_API_PATH,
            }
        ):
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._request_is_allowed(
            {GRAPH_API_PATH, GRAPH_STATUS_API_PATH, CONNECTORS_API_PATH}
        ):
            return
        if self.path == CONNECTORS_API_PATH:
            self._send_json(HTTPStatus.OK, self.connector_catalog)
            return
        if self.path == GRAPH_STATUS_API_PATH:
            self._get_status()
            return
        try:
            document = self.store.load()
        except GraphDocumentValidationError as error:
            self._send_graph_error(HTTPStatus.UNPROCESSABLE_ENTITY, error)
            return
        except OSError:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Could not read graph."})
            return

        self._send_json(
            HTTPStatus.OK,
            document.graph.model_dump(by_alias=True, mode="json"),
            revision=document.revision,
        )

    def do_POST(self) -> None:  # noqa: N802
        if not self._request_is_allowed(
            {GRAPH_VALIDATE_API_PATH, GRAPH_RUN_API_PATH, GRAPH_PREVIEW_API_PATH}
        ):
            return
        if self.operations is None:
            self._send_json(
                HTTPStatus.CONFLICT,
                {
                    "error": (
                        "Execution controls are disabled. Restart Dander with a project and "
                        "pipeline binding."
                    )
                },
            )
            return
        expected_revision = _parse_if_match(self.headers.get("If-Match"))
        if expected_revision is None:
            self._send_json(
                HTTPStatus.PRECONDITION_REQUIRED,
                {"error": "Graph operations require the ETag returned by the last open."},
            )
            return
        try:
            if self.path == GRAPH_VALIDATE_API_PATH:
                result = self.operations.validate(
                    self.store,
                    expected_revision=expected_revision,
                )
                self._send_json(
                    HTTPStatus.OK,
                    result,
                    revision=expected_revision,
                )
            elif self.path == GRAPH_RUN_API_PATH:
                execution = self.operations.trigger(
                    self.store,
                    expected_revision=expected_revision,
                )
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    {"execution": execution.as_dict()},
                )
            else:
                preview = self.operations.preview_deployment(
                    self.store,
                    expected_revision=expected_revision,
                )
                self._send_json(HTTPStatus.OK, preview.as_dict())
        except GraphOperationRevisionError as error:
            self._send_json(HTTPStatus.PRECONDITION_FAILED, {"error": str(error)})
        except GraphOperationConflictError as error:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
        except (GraphDocumentValidationError, GraphOperationValidationError) as error:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
        except GraphOperationUnavailableError as error:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})

    def do_PUT(self) -> None:  # noqa: N802
        if not self._request_is_allowed({GRAPH_API_PATH}):
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "Graph saves require Content-Type: application/json."},
            )
            return

        expected_revision = _parse_if_match(self.headers.get("If-Match"))
        if expected_revision is None:
            self._send_json(
                HTTPStatus.PRECONDITION_REQUIRED,
                {"error": "Graph saves require the ETag returned by the last open."},
            )
            return

        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0:
            self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length is required."})
            return
        if length > MAX_GRAPH_DOCUMENT_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Graph document is too large."}
            )
            return

        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Request body is not valid JSON."})
            return

        try:
            document = self.store.save(payload, expected_revision=expected_revision)
        except GraphDocumentConflictError as error:
            self._send_json(HTTPStatus.PRECONDITION_FAILED, {"error": str(error)})
            return
        except GraphDocumentValidationError as error:
            self._send_graph_error(HTTPStatus.UNPROCESSABLE_ENTITY, error)
            return
        except OSError:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Could not save graph."})
            return

        self._send_json(
            HTTPStatus.OK,
            document.graph.model_dump(by_alias=True, mode="json"),
            revision=document.revision,
        )

    def _get_status(self) -> None:
        if self.operations is None:
            self._send_json(HTTPStatus.OK, {"enabled": False})
            return
        try:
            self._send_json(HTTPStatus.OK, self.operations.status(self.store))
        except (GraphDocumentValidationError, GraphOperationValidationError) as error:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
        except GraphOperationUnavailableError as error:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})

    def _request_is_allowed(self, paths: set[str]) -> bool:
        if self.path not in paths:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."}, include_cors=False)
            return False
        if self.headers.get("Origin") != self.allowed_origin:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "Browser origin is not allowed."},
                include_cors=False,
            )
            return False
        return True

    def _send_graph_error(self, status: HTTPStatus, error: GraphDocumentValidationError) -> None:
        payload: dict[str, object] = {"error": str(error)}
        if error.details:
            payload["details"] = error.details
        self._send_json(status, payload)

    def _send_json(
        self,
        status: HTTPStatus,
        payload: object,
        *,
        revision: str | None = None,
        include_cors: bool = True,
    ) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        if include_cors:
            self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if revision is not None:
            self.send_header("ETag", f'"{revision}"')
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.allowed_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, If-Match")
        self.send_header("Access-Control-Expose-Headers", "ETag")
        self.send_header("Vary", "Origin")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the local bridge quiet; the CLI prints its bound file and address."""


def _connector_catalog(plugins: tuple[InstalledConnectorPlugin, ...]) -> dict[str, object]:
    """Project installed-plugin descriptors into the non-secret Druff discovery contract."""
    connectors: list[dict[str, object]] = []
    for installed in plugins:
        plugin = installed.plugin
        for connector in plugin.connectors:
            endpoints = [
                {
                    "id": endpoint.endpoint_id,
                    "display_name": endpoint.display_name,
                    "graph_binding": {
                        "connector": connector.connector_id,
                        "endpoint": endpoint.endpoint_id,
                    },
                    "fields": [
                        {
                            "name": field.name,
                            "display_name": field.display_name,
                            "data_type": field.data_type,
                            "required": field.required,
                        }
                        for field in endpoint.fields
                    ],
                }
                for endpoint in connector.endpoints
            ]
            connectors.append(
                {
                    "id": connector.connector_id,
                    "display_name": connector.display_name,
                    "engine": connector.engine,
                    "description": connector.description,
                    "plugin": {
                        "id": plugin.plugin_id,
                        "distribution": installed.distribution,
                        "version": installed.version,
                    },
                    "endpoints": endpoints,
                }
            )
    return {"connectors": connectors}


def _validate_graph_payload(payload: object) -> PipelineGraph:
    try:
        graph = PipelineGraph.model_validate(payload, extra="forbid")
    except ValidationError as error:
        raise _pydantic_graph_error(error) from error
    _validate_graph_semantics(graph)
    return graph


def _validate_graph_semantics(graph: PipelineGraph) -> None:
    try:
        validate_field_wiring(graph)
    except GraphValidationError as error:
        raise GraphDocumentValidationError(str(error)) from error


def _pydantic_graph_error(error: ValidationError) -> GraphDocumentValidationError:
    details = [
        {
            "location": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in error.errors(include_input=False, include_url=False)
    ]
    return GraphDocumentValidationError(
        "Graph does not match Dander's PipelineGraph contract.", details=details
    )


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_if_match(value: str | None) -> str | None:
    if value is None or len(value) < 3 or not (value.startswith('"') and value.endswith('"')):
        return None
    revision = value[1:-1]
    if len(revision) != 64 or any(character not in "0123456789abcdef" for character in revision):
        return None
    return revision


def _sync_directory(directory: Path) -> None:
    """Durably record the atomic rename where the platform supports directory fsync."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
