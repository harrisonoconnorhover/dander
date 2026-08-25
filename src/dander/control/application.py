"""Provider-neutral application boundary behind the hosted Dander Control API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from dander import __version__
from dander.control.bundle import BUNDLE_ID, packaged_bundle_digest
from dander.control.catalogs import (
    build_connector_catalog,
    build_typed_operation_catalog,
    build_typed_plugin_catalog,
)
from dander.control.graph_store import (
    MAX_GRAPH_DOCUMENT_BYTES,
    MAX_GRAPH_PAGE_SIZE,
    GraphPage,
    GraphRecord,
    GraphStore,
    GraphStoreNotFoundError,
)
from dander.control.models import (
    CapabilitiesResponse,
    CompatibilityRange,
    ConnectorCatalogResponse,
    ContractIdentity,
    ControlLimits,
    DeploymentPreviewResponse,
    GraphPageResponse,
    GraphResourceResponse,
    GraphSummaryResponse,
    GraphValidationResponse,
    LogPageResponse,
    MutationResult,
    OperationCatalogResponse,
    PluginCatalogResponse,
    ProjectListResponse,
    ProjectSummaryResponse,
    RunPageResponse,
    RunStatusResponse,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from dander.control.orchestration import RunSubmission
    from dander.plugins import InstalledConnectorPlugin

MAX_LOG_RECORDS = 500


class ControlOperationError(RuntimeError):
    """Base for normalized operation failures safe to map to a public API error."""


class ControlOperationUnavailableError(ControlOperationError):
    """The selected profile does not implement the requested operation."""


class ControlOperationNotFoundError(ControlOperationError):
    """The addressed run does not exist in the selected lifecycle boundary."""


class ControlOperationConflictError(ControlOperationError):
    """The requested lifecycle transition is invalid for the current state."""


class ControlOperationIdempotencyConflictError(ControlOperationConflictError):
    """A lifecycle idempotency key was reused for a different request."""


class GraphRevisionConflictError(ControlOperationConflictError):
    """An operation targeted a graph revision that is no longer current."""


@dataclass(frozen=True, slots=True)
class RunAddress:
    """Provider-neutral global address for one run."""

    run_id: str


class GraphValidationPort(Protocol):
    """Validate one already canonical graph against selected-profile application rules."""

    def validate(self, record: GraphRecord) -> GraphValidationResponse: ...


class DeploymentPreviewPort(Protocol):
    """Build one normalized, presentation-safe preview for the exact supplied graph revision."""

    def preview(self, record: GraphRecord) -> DeploymentPreviewResponse: ...

    def close(self) -> None: ...


class RunLifecyclePort(Protocol):
    """Normalize selected-provider run behavior without exposing provider payloads.

    ``start``, ``cancel``, and ``replay`` own durable idempotency at this boundary. Identical
    successful retries must replay their original result, conflicting key reuse must fail, and
    failed validation/preconditions must not consume a key. ``start`` receives one resolved
    provider-neutral submission after the application has checked the requested graph revision.
    ``replay`` creates a new logical run and returns its distinct run ID.
    """

    def start(self, submission: RunSubmission) -> RunStatusResponse: ...

    def list(self, *, cursor: str | None, limit: int) -> RunPageResponse: ...

    def get(self, address: RunAddress) -> RunStatusResponse: ...

    def logs(
        self,
        address: RunAddress,
        *,
        cursor: str | None,
        limit: int,
    ) -> LogPageResponse: ...

    def cancel(self, address: RunAddress, *, idempotency_key: str) -> MutationResult: ...

    def replay(self, address: RunAddress, *, idempotency_key: str) -> MutationResult: ...

    def close(self) -> None: ...


class RunSubmissionResolver(Protocol):
    """Resolve the compatibility graph route into one exact environment and execution plan."""

    def resolve(
        self,
        record: GraphRecord,
        *,
        idempotency_key: str,
        requested_at: datetime,
    ) -> RunSubmission: ...


class CanonicalGraphValidator:
    """Reapply Dander's canonical graph semantics without selecting a provider."""

    def validate(self, record: GraphRecord) -> GraphValidationResponse:
        record.document.to_domain()
        return GraphValidationResponse(
            valid=True,
            graph_name=record.document.name,
            content_sha256=record.content_sha256,
            issues=(),
        )


class ControlApplication:
    """Compose GraphStore, catalogs, planning, and lifecycle behind normalized DTOs."""

    def __init__(
        self,
        graph_store: GraphStore,
        *,
        validator: GraphValidationPort | None = None,
        preview: DeploymentPreviewPort | None = None,
        lifecycle: RunLifecyclePort | None = None,
        submission_resolver: RunSubmissionResolver | None = None,
        connector_plugins: tuple[InstalledConnectorPlugin, ...] = (),
        projects: tuple[str, ...] = ("default",),
        readiness: Callable[[], bool] | None = None,
    ) -> None:
        self.graph_store = graph_store
        self.validator = validator or CanonicalGraphValidator()
        self.preview_port = preview
        self.lifecycle_port = lifecycle
        self.submission_resolver = submission_resolver
        if (lifecycle is None) != (submission_resolver is None):
            raise ValueError(
                "Control run lifecycle and submission resolver must be configured together."
            )
        self.connector_catalog: ConnectorCatalogResponse = build_connector_catalog(
            connector_plugins
        )
        self.plugin_catalog: PluginCatalogResponse = build_typed_plugin_catalog(connector_plugins)
        self.operation_catalog: OperationCatalogResponse = build_typed_operation_catalog()
        if not projects or len(projects) > 100 or len(set(projects)) != len(projects):
            raise ValueError("Control projects must contain 1 to 100 unique identifiers.")
        for project in projects:
            graph_store.list(project, limit=1)
        self.projects = tuple(sorted(projects))
        self._readiness = readiness or (lambda: True)
        self._closed = False

    def capabilities(self) -> CapabilitiesResponse:
        operations: list[str] = [
            "graph.read",
            "graph.edit",
            "graph.delete",
            "graph.validate",
        ]
        if self.preview_port is not None:
            operations.append("deployment.preview")
        if self.lifecycle_port is not None:
            operations.extend(["run.start", "run.read", "run.logs", "run.cancel", "run.replay"])
        return CapabilitiesResponse(
            dander_version=__version__,
            contract=ContractIdentity(id=BUNDLE_ID, sha256=packaged_bundle_digest()),
            compatibility=CompatibilityRange(
                minimum_druff_contract="1.0.0",
                maximum_druff_contract="1.x",
            ),
            operations=tuple(operations),
            limits=ControlLimits(
                max_graph_bytes=MAX_GRAPH_DOCUMENT_BYTES,
                max_page_size=MAX_GRAPH_PAGE_SIZE,
                max_log_records=MAX_LOG_RECORDS,
            ),
        )

    def list_graphs(
        self,
        project: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> GraphPageResponse:
        self.require_project(project)
        return graph_page_response(self.graph_store.list(project, cursor=cursor, limit=limit))

    def list_projects(self) -> ProjectListResponse:
        return ProjectListResponse(
            projects=tuple(ProjectSummaryResponse(id=project) for project in self.projects)
        )

    def require_project(self, project: str) -> None:
        if project not in self.projects:
            raise GraphStoreNotFoundError("The project does not exist.")

    def get_graph(self, project: str, graph: str) -> GraphRecord:
        self.require_project(project)
        return self.graph_store.get(project, graph)

    def require_graph_revision(
        self,
        project: str,
        graph: str,
        expected_revision: str,
    ) -> GraphRecord:
        record = self.get_graph(project, graph)
        if record.revision != expected_revision:
            raise GraphRevisionConflictError("The graph revision no longer matches.")
        return record

    def validate_graph(
        self,
        project: str,
        graph: str,
        *,
        expected_revision: str,
    ) -> GraphValidationResponse:
        return self.validator.validate(
            self.require_graph_revision(project, graph, expected_revision)
        )

    def preview_graph(
        self,
        project: str,
        graph: str,
        *,
        expected_revision: str,
    ) -> DeploymentPreviewResponse:
        if self.preview_port is None:
            raise ControlOperationUnavailableError(
                "Deployment preview is unavailable for the selected profile."
            )
        return self.preview_port.preview(
            self.require_graph_revision(project, graph, expected_revision)
        )

    def start_run(
        self,
        project: str,
        graph: str,
        *,
        expected_revision: str,
        idempotency_key: str,
    ) -> RunStatusResponse:
        lifecycle = self._require_lifecycle()
        record = self.require_graph_revision(project, graph, expected_revision)
        resolver = self._require_submission_resolver()
        submission = resolver.resolve(
            record,
            idempotency_key=idempotency_key,
            requested_at=datetime.now(UTC),
        )
        if submission.graph != record or submission.idempotency_key != idempotency_key:
            raise RuntimeError("The submission resolver changed validated request identity.")
        return lifecycle.start(submission)

    def get_run(self, address: RunAddress) -> RunStatusResponse:
        return self._require_lifecycle().get(address)

    def list_runs(self, *, cursor: str | None, limit: int) -> RunPageResponse:
        if not 1 <= limit <= 100:
            raise ValueError("The requested run page size is invalid.")
        page = self._require_lifecycle().list(cursor=cursor, limit=limit)
        if len(page.items) > limit:
            raise RuntimeError("The lifecycle adapter returned an oversized run page.")
        return page

    def get_logs(
        self,
        address: RunAddress,
        *,
        cursor: str | None,
        limit: int,
    ) -> LogPageResponse:
        if not 1 <= limit <= MAX_LOG_RECORDS:
            raise ValueError("The requested log page size is invalid.")
        page = self._require_lifecycle().logs(address, cursor=cursor, limit=limit)
        if len(page.records) > limit or len(page.records) > MAX_LOG_RECORDS:
            raise RuntimeError("The lifecycle adapter returned an oversized log page.")
        return page

    def cancel_run(self, address: RunAddress, *, idempotency_key: str) -> MutationResult:
        return self._require_lifecycle().cancel(address, idempotency_key=idempotency_key)

    def replay_run(self, address: RunAddress, *, idempotency_key: str) -> MutationResult:
        return self._require_lifecycle().replay(address, idempotency_key=idempotency_key)

    def ready(self) -> bool:
        return not self._closed and self._readiness()

    def close(self) -> None:
        if self._closed:
            return
        for port in (self.preview_port, self.lifecycle_port):
            if port is not None:
                port.close()
        self._closed = True

    def _require_lifecycle(self) -> RunLifecyclePort:
        if self.lifecycle_port is None:
            raise ControlOperationUnavailableError(
                "Run operations are unavailable for the selected profile."
            )
        return self.lifecycle_port

    def _require_submission_resolver(self) -> RunSubmissionResolver:
        if self.submission_resolver is None:
            raise ControlOperationUnavailableError(
                "Run submission is unavailable for the selected profile."
            )
        return self.submission_resolver


def graph_resource_response(record: GraphRecord) -> GraphResourceResponse:
    """Project one internal record without exposing its provider-native revision in JSON."""
    return GraphResourceResponse(
        project=record.project,
        graph=record.graph,
        document=record.document,
        content_sha256=record.content_sha256,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def graph_page_response(page: GraphPage) -> GraphPageResponse:
    """Project one internal page while retaining its opaque project-bound cursor."""
    return GraphPageResponse(
        items=tuple(
            GraphSummaryResponse(
                project=item.project,
                graph=item.graph,
                content_sha256=item.content_sha256,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in page.items
        ),
        next_cursor=page.next_cursor,
    )


__all__ = [
    "CanonicalGraphValidator",
    "ControlApplication",
    "ControlOperationConflictError",
    "ControlOperationError",
    "ControlOperationIdempotencyConflictError",
    "ControlOperationNotFoundError",
    "ControlOperationUnavailableError",
    "DeploymentPreviewPort",
    "GraphRevisionConflictError",
    "GraphValidationPort",
    "MAX_LOG_RECORDS",
    "RunAddress",
    "RunLifecyclePort",
    "RunSubmissionResolver",
    "graph_page_response",
    "graph_resource_response",
]
