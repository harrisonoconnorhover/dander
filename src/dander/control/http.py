"""Hosted HTTP transport for Dander's provider-neutral Control application."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from dander.control.application import (
    ControlApplication,
    ControlOperationConflictError,
    ControlOperationIdempotencyConflictError,
    ControlOperationNotFoundError,
    ControlOperationUnavailableError,
    GraphRevisionConflictError,
    RunAddress,
    graph_resource_response,
)
from dander.control.graph_store import (
    MAX_GRAPH_DOCUMENT_BYTES,
    GraphRecord,
    GraphStoreAlreadyExistsError,
    GraphStoreConflictError,
    GraphStoreDocumentError,
    GraphStoreError,
    GraphStoreIdempotencyConflictError,
    GraphStoreIdentifierError,
    GraphStoreNotFoundError,
)
from dander.control.models import (
    ApiError,
    ApiErrorDetail,
    ApiErrorEnvelope,
    GraphCreateRequest,
    PipelineGraphDocument,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.middleware.base import RequestResponseEndpoint

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_CONTROL_RESPONSE_BYTES = 6 * 1024 * 1024
_MUTATION_METHODS = frozenset({"POST", "PUT", "DELETE"})
_LOGGER = logging.getLogger("dander.control.audit")
_IDEMPOTENCY_HEADER = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
_IF_MATCH_HEADER = Annotated[str, Header(alias="If-Match", min_length=3, max_length=1024)]


class GraphBodyError(ValueError):
    """A graph HTTP body is malformed or exceeds the public request bound."""

    def __init__(self, code: str, message: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def encode_revision_etag(revision: str) -> str:
    """Reversibly wrap one opaque adapter revision in a strong HTTP-safe ETag."""
    payload = base64.urlsafe_b64encode(revision.encode("utf-8")).rstrip(b"=").decode("ascii")
    return f'"{payload}"'


def decode_revision_etag(value: str) -> str:
    """Strictly recover the exact adapter token from one strong quoted HTTP ETag."""
    if (
        len(value) > 1024
        or not value.startswith('"')
        or not value.endswith('"')
        or value.startswith("W/")
        or "," in value
        or value == '"*"'
    ):
        raise GraphBodyError(
            "invalid_revision",
            "If-Match must contain one strong graph ETag.",
            HTTPStatus.BAD_REQUEST,
        )
    encoded = value[1:-1]
    if not encoded or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None:
        raise GraphBodyError(
            "invalid_revision",
            "If-Match must contain one strong graph ETag.",
            HTTPStatus.BAD_REQUEST,
        )
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        revision = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as error:
        raise GraphBodyError(
            "invalid_revision",
            "If-Match must contain one strong graph ETag.",
            HTTPStatus.BAD_REQUEST,
        ) from error
    if not revision or len(revision) > 512 or encode_revision_etag(revision) != value:
        raise GraphBodyError(
            "invalid_revision",
            "If-Match must contain one strong graph ETag.",
            HTTPStatus.BAD_REQUEST,
        )
    return revision


async def read_graph_document(request: Request) -> PipelineGraphDocument:
    """Stream and parse one graph while never buffering more than the limit plus one byte."""
    payload = await _read_bounded_json(request, MAX_GRAPH_DOCUMENT_BYTES)
    try:
        return PipelineGraphDocument.model_validate(payload)
    except ValidationError as error:
        raise GraphBodyError(
            "graph_invalid", "The graph document is invalid.", HTTPStatus.UNPROCESSABLE_ENTITY
        ) from error


async def _read_bounded_json(request: Request, limit: int, *, subject: str = "graph") -> object:
    """Read JSON while buffering at most the configured limit plus one byte."""
    if request.headers.get("content-type", "").partition(";")[0].strip() != "application/json":
        raise GraphBodyError(
            "unsupported_media_type",
            f"{subject.capitalize()} requests require Content-Type: application/json.",
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as error:
            raise GraphBodyError(
                "invalid_content_length", "Content-Length is invalid.", HTTPStatus.BAD_REQUEST
            ) from error
        if declared < 0:
            raise GraphBodyError(
                "invalid_content_length", "Content-Length is invalid.", HTTPStatus.BAD_REQUEST
            )
        if declared > limit:
            raise GraphBodyError(
                f"{subject}_too_large",
                f"The {subject} request exceeds the configured limit.",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
    body = bytearray()
    async for chunk in request.stream():
        remaining = limit + 1 - len(body)
        body.extend(chunk[:remaining])
        if len(body) > limit:
            raise GraphBodyError(
                f"{subject}_too_large",
                f"The {subject} request exceeds the configured limit.",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GraphBodyError(
            f"{subject}_invalid",
            f"The {subject} request is invalid.",
            HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from error


async def _require_empty_body(request: Request) -> None:
    """Reject a body without buffering it for header-only mutation contracts."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as error:
            raise GraphBodyError(
                "invalid_content_length", "Content-Length is invalid.", HTTPStatus.BAD_REQUEST
            ) from error
        if declared < 0:
            raise GraphBodyError(
                "invalid_content_length", "Content-Length is invalid.", HTTPStatus.BAD_REQUEST
            )
        if declared > 0:
            raise GraphBodyError(
                "request_invalid",
                "This operation does not accept a request body.",
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )
    async for chunk in request.stream():
        if chunk:
            raise GraphBodyError(
                "request_invalid",
                "This operation does not accept a request body.",
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )


def create_control_app(application: ControlApplication) -> FastAPI:
    """Create the separately named hosted API over one fully composed application boundary."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            application.close()

    app = FastAPI(title="Dander Control API", version="v1", lifespan=lifespan)
    app.state.control_application = application

    @app.middleware("http")
    async def correlation(request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("X-Correlation-ID", "")
        correlation_id = supplied if _CORRELATION_ID.fullmatch(supplied) else uuid.uuid4().hex
        request.state.correlation_id = correlation_id
        try:
            response = await call_next(request)
        except Exception:
            response = _error_response(
                request,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The request could not be completed.",
            )
        content_length = response.headers.get("content-length")
        if content_length is not None and int(content_length) > _MAX_CONTROL_RESPONSE_BYTES:
            response = _error_response(
                request,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "response_too_large",
                "The response exceeded the configured limit.",
            )
        response.headers["X-Correlation-ID"] = correlation_id
        if request.method in _MUTATION_METHODS:
            route = request.scope.get("route")
            _LOGGER.info(
                "control_mutation",
                extra={
                    "correlation_id": correlation_id,
                    "http_method": request.method,
                    "route_template": getattr(route, "path", "unmatched"),
                    "status_code": response.status_code,
                },
            )
        return response

    @app.exception_handler(GraphBodyError)
    async def graph_body_error(request: Request, error: GraphBodyError) -> JSONResponse:
        return _error_response(request, error.status, error.code, str(error))

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        details = tuple(
            ApiErrorDetail(
                location=".".join(str(part) for part in item["loc"]),
                code=str(item["type"]),
                message=str(item["msg"]),
            )
            for item in error.errors()
        )
        return _error_response(
            request,
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "request_invalid",
            "The request is invalid.",
            details=details,
        )

    @app.exception_handler(GraphStoreError)
    async def store_error(request: Request, error: GraphStoreError) -> JSONResponse:
        if isinstance(error, GraphStoreNotFoundError):
            return _error_response(request, HTTPStatus.NOT_FOUND, "graph_not_found", str(error))
        if isinstance(error, GraphStoreAlreadyExistsError):
            return _error_response(request, HTTPStatus.CONFLICT, "graph_exists", str(error))
        if isinstance(error, (GraphStoreConflictError, GraphStoreIdempotencyConflictError)):
            return _error_response(request, HTTPStatus.CONFLICT, "graph_conflict", str(error))
        if isinstance(error, (GraphStoreIdentifierError, GraphStoreDocumentError)):
            return _error_response(
                request, HTTPStatus.UNPROCESSABLE_ENTITY, "graph_invalid", str(error)
            )
        return _error_response(
            request,
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "graph_store_error",
            "The graph store could not complete the request.",
        )

    @app.exception_handler(ControlOperationUnavailableError)
    async def unavailable(
        request: Request, error: ControlOperationUnavailableError
    ) -> JSONResponse:
        return _error_response(
            request, HTTPStatus.NOT_IMPLEMENTED, "operation_unavailable", str(error)
        )

    @app.exception_handler(ControlOperationNotFoundError)
    async def run_not_found(request: Request, error: ControlOperationNotFoundError) -> JSONResponse:
        return _error_response(request, HTTPStatus.NOT_FOUND, "run_not_found", str(error))

    @app.exception_handler(ControlOperationConflictError)
    async def operation_conflict(
        request: Request, error: ControlOperationConflictError
    ) -> JSONResponse:
        code = (
            "idempotency_conflict"
            if isinstance(error, ControlOperationIdempotencyConflictError)
            else "operation_conflict"
        )
        status = (
            HTTPStatus.PRECONDITION_FAILED
            if isinstance(error, GraphRevisionConflictError)
            else HTTPStatus.CONFLICT
        )
        return _error_response(request, status, code, str(error))

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readiness(request: Request) -> Response:
        if not application.ready():
            return _error_response(
                request, HTTPStatus.SERVICE_UNAVAILABLE, "not_ready", "The service is not ready."
            )
        return JSONResponse({"status": "ready"})

    @app.get("/v1/capabilities")
    async def capabilities() -> object:
        return application.capabilities()

    @app.get("/v1/connectors")
    async def connectors() -> object:
        return application.connector_catalog

    @app.get("/v1/plugin-catalog")
    async def plugins() -> object:
        return application.plugin_catalog

    @app.get("/v1/operations")
    async def operations() -> object:
        return application.operation_catalog

    @app.get("/v1/projects")
    async def list_projects() -> object:
        return application.list_projects()

    @app.get("/v1/projects/{project}/graphs")
    async def list_graphs(
        project: str,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> object:
        return application.list_graphs(project, cursor=cursor, limit=limit)

    @app.post("/v1/projects/{project}/graphs", status_code=HTTPStatus.CREATED)
    async def create_graph(
        project: str, request: Request, idempotency_key: _IDEMPOTENCY_HEADER
    ) -> Response:
        try:
            body = GraphCreateRequest.model_validate(
                await _read_bounded_json(request, MAX_GRAPH_DOCUMENT_BYTES + 2048)
            )
        except ValidationError as error:
            raise GraphBodyError(
                "graph_invalid",
                "The graph create request is invalid.",
                HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from error
        application.require_project(project)
        record = application.graph_store.create(
            project, body.graph, body.document, idempotency_key=idempotency_key
        )
        return _resource_response(record, HTTPStatus.CREATED)

    @app.get("/v1/projects/{project}/graphs/{graph}")
    async def get_graph(project: str, graph: str) -> Response:
        return _resource_response(application.get_graph(project, graph))

    @app.put("/v1/projects/{project}/graphs/{graph}")
    async def put_graph(
        project: str, graph: str, request: Request, if_match: _IF_MATCH_HEADER
    ) -> Response:
        document = await read_graph_document(request)
        application.require_project(project)
        record = application.graph_store.put(
            project,
            graph,
            document,
            expected_revision=decode_revision_etag(if_match),
        )
        return _resource_response(record)

    @app.delete("/v1/projects/{project}/graphs/{graph}", status_code=HTTPStatus.NO_CONTENT)
    async def delete_graph(
        project: str,
        graph: str,
        if_match: _IF_MATCH_HEADER,
        idempotency_key: _IDEMPOTENCY_HEADER,
    ) -> Response:
        application.require_project(project)
        application.graph_store.delete(
            project,
            graph,
            expected_revision=decode_revision_etag(if_match),
            idempotency_key=idempotency_key,
        )
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @app.post("/v1/projects/{project}/graphs/{graph}/validate")
    async def validate_graph(project: str, graph: str, if_match: _IF_MATCH_HEADER) -> object:
        return application.validate_graph(
            project, graph, expected_revision=decode_revision_etag(if_match)
        )

    @app.post("/v1/projects/{project}/graphs/{graph}/deployment-preview")
    async def preview_graph(project: str, graph: str, if_match: _IF_MATCH_HEADER) -> object:
        return application.preview_graph(
            project, graph, expected_revision=decode_revision_etag(if_match)
        )

    @app.post("/v1/projects/{project}/graphs/{graph}/runs", status_code=HTTPStatus.ACCEPTED)
    async def start_run(
        project: str,
        graph: str,
        request: Request,
        if_match: _IF_MATCH_HEADER,
        idempotency_key: _IDEMPOTENCY_HEADER,
    ) -> object:
        await _require_empty_body(request)
        return application.start_run(
            project,
            graph,
            expected_revision=decode_revision_etag(if_match),
            idempotency_key=idempotency_key,
        )

    @app.get("/v1/runs")
    async def list_runs(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> object:
        return application.list_runs(cursor=cursor, limit=limit)

    @app.get("/v1/runs/{run_id}")
    async def get_run(run_id: str) -> object:
        return application.get_run(RunAddress(run_id))

    @app.get("/v1/runs/{run_id}/logs")
    async def get_logs(
        run_id: str,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> object:
        return application.get_logs(RunAddress(run_id), cursor=cursor, limit=limit)

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, idempotency_key: _IDEMPOTENCY_HEADER) -> object:
        return application.cancel_run(RunAddress(run_id), idempotency_key=idempotency_key)

    @app.post("/v1/runs/{run_id}/replay")
    async def replay_run(run_id: str, idempotency_key: _IDEMPOTENCY_HEADER) -> object:
        return application.replay_run(RunAddress(run_id), idempotency_key=idempotency_key)

    return app


def _resource_response(record: GraphRecord, status: HTTPStatus = HTTPStatus.OK) -> JSONResponse:
    projected = graph_resource_response(record)
    response = JSONResponse(projected.model_dump(mode="json"), status_code=status)
    response.headers["ETag"] = encode_revision_etag(record.revision)
    return response


def _error_response(
    request: Request,
    status: HTTPStatus,
    code: str,
    message: str,
    *,
    details: tuple[ApiErrorDetail, ...] = (),
) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", uuid.uuid4().hex)
    envelope = ApiErrorEnvelope(
        error=ApiError(
            code=code,
            message=message,
            correlation_id=correlation_id,
            details=details,
        )
    )
    return JSONResponse(envelope.model_dump(mode="json"), status_code=status)


__all__ = [
    "create_control_app",
    "decode_revision_etag",
    "encode_revision_etag",
    "read_graph_document",
]
