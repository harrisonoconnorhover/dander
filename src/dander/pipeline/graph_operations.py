"""Operator-bound execution controls for Dander's loopback graph service.

The browser never chooses a project, pipeline, region, job, or filesystem path. The operator
selects one graph pipeline when starting ``dander graph serve``; Dander derives its deployed Cloud
Run job from the validated project manifest and exposes only that fixed binding.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol, cast

from dander.pipeline.graph_deployment import (
    GraphDeploymentError,
    GraphDeploymentPreview,
    GraphDeploymentStaleError,
)
from dander.project import ProjectConfigError, load_project_config
from dander.state import BigQueryRunHistoryStore, RunRecord

if TYPE_CHECKING:
    from pathlib import Path

    from dander.pipeline.graph_service import GraphDocumentStore

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_EXECUTION_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_ACTIVE_EXECUTION_FILTER = "status.conditions.type=Completed AND status.conditions.status=Unknown"


class GraphOperationError(RuntimeError):
    """Base error safe to report through the local operations API."""


class GraphOperationValidationError(GraphOperationError):
    """The selected project no longer validates against its fixed graph binding."""


class GraphOperationRevisionError(GraphOperationError):
    """The browser requested an operation against a stale graph revision."""


class GraphOperationConflictError(GraphOperationError):
    """A deployed execution is already active or being submitted."""


class GraphOperationUnavailableError(GraphOperationError):
    """Cloud Run or run-history status could not be read safely."""


class _RunHistoryReader(Protocol):
    def recent(
        self,
        *,
        limit: int = 20,
        pipeline_id: str | None = None,
    ) -> tuple[RunRecord, ...]: ...


class _DeploymentPreviewer(Protocol):
    def preview(
        self,
        store: GraphDocumentStore,
        *,
        expected_revision: str,
    ) -> GraphDeploymentPreview: ...


CommandRunner = Callable[[tuple[str, ...]], str]


def _run_gcloud(args: tuple[str, ...]) -> str:
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise GraphOperationUnavailableError("The gcloud CLI is not available.") from error
    except subprocess.CalledProcessError as error:
        raise GraphOperationUnavailableError(
            "The deployed Cloud Run job is unavailable."
        ) from error
    return result.stdout


@dataclass(frozen=True)
class GraphOperationBinding:
    """One immutable local-to-hosted graph binding selected by the operator."""

    project: str
    pipeline_id: str
    region: str
    job_name: str
    graph_file: Path
    project_config: Path

    @classmethod
    def from_project(
        cls,
        *,
        graph_file: Path,
        project_config: Path,
        pipeline_id: str,
        project: str,
    ) -> GraphOperationBinding:
        if not _PROJECT_ID.fullmatch(project):
            raise GraphOperationValidationError("Project must be a valid GCP project id.")
        resolved_graph = graph_file.expanduser().resolve()
        resolved_config = project_config.expanduser().resolve()
        try:
            manifest = load_project_config(resolved_config)
            pipeline = manifest.pipelines[pipeline_id]
        except KeyError as error:
            raise GraphOperationValidationError(
                f"Pipeline {pipeline_id!r} is not declared in the project manifest."
            ) from error
        except ProjectConfigError as error:
            raise GraphOperationValidationError(str(error)) from error
        if pipeline.graph is None:
            raise GraphOperationValidationError(
                f"Pipeline {pipeline_id!r} is not configured as a graph pipeline."
            )
        expected_graph = (resolved_config.parent / pipeline.graph).resolve()
        if expected_graph != resolved_graph:
            raise GraphOperationValidationError(
                "The served graph file does not match the selected pipeline's graph."
            )
        try:
            manifest.validate_references(resolved_config.parent)
        except ProjectConfigError as error:
            raise GraphOperationValidationError(str(error)) from error
        job_name = cast("str", manifest.terraform_pipelines()[pipeline_id]["job_name"])
        return cls(
            project=project,
            pipeline_id=pipeline_id,
            region=manifest.platform.region,
            job_name=job_name,
            graph_file=resolved_graph,
            project_config=resolved_config,
        )

    def validate_current_project(self) -> None:
        """Fail closed if files changed after the operator selected this binding."""
        current = type(self).from_project(
            graph_file=self.graph_file,
            project_config=self.project_config,
            pipeline_id=self.pipeline_id,
            project=self.project,
        )
        if current != self:
            raise GraphOperationValidationError(
                "The graph deployment binding changed. Restart `dander graph serve`."
            )

    def as_dict(self) -> dict[str, str]:
        return {
            "project": self.project,
            "pipeline_id": self.pipeline_id,
            "region": self.region,
            "job_name": self.job_name,
        }


@dataclass(frozen=True)
class CloudRunExecution:
    """Small, non-sensitive projection of one Cloud Run job execution."""

    name: str
    state: str
    started_at: str | None = None
    completed_at: str | None = None
    succeeded_count: int = 0
    failed_count: int = 0
    log_uri: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {"succeeded", "failed"}

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class GraphOperations:
    """Validate, submit, and inspect one explicitly bound deployed graph pipeline."""

    def __init__(
        self,
        binding: GraphOperationBinding,
        *,
        command_runner: CommandRunner = _run_gcloud,
        run_history: _RunHistoryReader | None = None,
        deployment_previewer: _DeploymentPreviewer | None = None,
    ) -> None:
        self.binding = binding
        self._run = command_runner
        self._history = run_history or BigQueryRunHistoryStore(
            project=binding.project,
            dataset="dander_meta",
            initialize_on_read=False,
        )
        self._lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._submitted_execution: str | None = None
        self._deployment_previewer = deployment_previewer

    def validate(
        self,
        store: GraphDocumentStore,
        *,
        expected_revision: str,
    ) -> dict[str, object]:
        document = store.load()
        if document.revision != expected_revision:
            raise GraphOperationRevisionError(
                "The graph changed after Druff opened it. Reopen before continuing."
            )
        self.binding.validate_current_project()
        return {
            "valid": True,
            "graph_name": document.graph.name,
            "revision": document.revision,
            "binding": self.binding.as_dict(),
        }

    def trigger(
        self,
        store: GraphDocumentStore,
        *,
        expected_revision: str,
    ) -> CloudRunExecution:
        with self._lock:
            self.validate(store, expected_revision=expected_revision)
            if self._submitted_execution is not None:
                raise GraphOperationConflictError(
                    "A deployed execution is still being submitted or observed."
                )
            if self._active_executions():
                raise GraphOperationConflictError("A deployed execution is already active.")
            payload = self._load_mapping_json(
                (
                    "gcloud",
                    "run",
                    "jobs",
                    "execute",
                    self.binding.job_name,
                    "--project",
                    self.binding.project,
                    "--region",
                    self.binding.region,
                    "--async",
                    "--format=json",
                ),
            )
            name = _execution_name(payload)
            self._submitted_execution = name
            return CloudRunExecution(name=name, state="starting")

    def preview_deployment(
        self,
        store: GraphDocumentStore,
        *,
        expected_revision: str,
    ) -> GraphDeploymentPreview:
        """Build one candidate and isolated plan for the exact saved graph revision."""
        if self._deployment_previewer is None:
            raise GraphOperationConflictError(
                "Deployment preview is disabled. Restart Dander with its operator inputs."
            )
        if not self._preview_lock.acquire(blocking=False):
            raise GraphOperationConflictError("A deployment preview is already being created.")
        try:
            self.validate(store, expected_revision=expected_revision)
            try:
                preview = self._deployment_previewer.preview(
                    store,
                    expected_revision=expected_revision,
                )
            except GraphDeploymentStaleError as error:
                raise GraphOperationRevisionError(str(error)) from error
            except GraphDeploymentError as error:
                raise GraphOperationUnavailableError(str(error)) from error
            self.validate(store, expected_revision=expected_revision)
            return preview
        finally:
            self._preview_lock.release()

    def status(self, store: GraphDocumentStore) -> dict[str, object]:
        with self._lock:
            self.binding.validate_current_project()
            document = store.load()
            execution = self._current_execution()
            try:
                records = self._history.recent(limit=1, pipeline_id=self.binding.pipeline_id)
            except Exception as error:
                raise GraphOperationUnavailableError(
                    "Could not read Dander run history."
                ) from error
            return {
                "enabled": True,
                "deployment_preview_enabled": self._deployment_previewer is not None,
                "graph_name": document.graph.name,
                "revision": document.revision,
                "binding": self.binding.as_dict(),
                "execution": execution.as_dict() if execution is not None else None,
                "run": _run_record(records[0]) if records else None,
            }

    def _current_execution(self) -> CloudRunExecution | None:
        if self._submitted_execution is not None:
            matches = self._list_executions(
                filter_expression=f"metadata.name={self._submitted_execution}"
            )
            if not matches:
                return CloudRunExecution(name=self._submitted_execution, state="starting")
            execution = matches[0]
            if execution.terminal:
                self._submitted_execution = None
            return execution

        active = self._active_executions()
        if active:
            return active[0]
        latest = self._list_executions(limit=1)
        return latest[0] if latest else None

    def _active_executions(self) -> list[CloudRunExecution]:
        return self._list_executions(filter_expression=_ACTIVE_EXECUTION_FILTER)

    def _list_executions(
        self,
        *,
        filter_expression: str | None = None,
        limit: int | None = None,
    ) -> list[CloudRunExecution]:
        args = [
            "gcloud",
            "run",
            "jobs",
            "executions",
            "list",
            "--job",
            self.binding.job_name,
            "--project",
            self.binding.project,
            "--region",
            self.binding.region,
            "--sort-by=~metadata.creationTimestamp",
        ]
        if filter_expression is not None:
            args.append(f"--filter={filter_expression}")
        if limit is not None:
            args.append(f"--limit={limit}")
        args.append("--format=json")
        payload = self._load_list_json(tuple(args))
        return [_parse_execution(item) for item in payload]

    def _load_mapping_json(self, args: tuple[str, ...]) -> dict[str, object]:
        payload = self._load_json(args)
        if not isinstance(payload, dict):
            raise GraphOperationUnavailableError("The gcloud CLI returned an invalid response.")
        return cast("dict[str, object]", payload)

    def _load_list_json(self, args: tuple[str, ...]) -> list[object]:
        payload = self._load_json(args)
        if not isinstance(payload, list):
            raise GraphOperationUnavailableError("The gcloud CLI returned an invalid response.")
        return cast("list[object]", payload)

    def _load_json(self, args: tuple[str, ...]) -> object:
        try:
            payload: object = json.loads(self._run(args))
        except (json.JSONDecodeError, TypeError) as error:
            raise GraphOperationUnavailableError(
                "The gcloud CLI returned an invalid response."
            ) from error
        return payload


def _execution_name(payload: dict[str, object]) -> str:
    metadata = payload.get("metadata")
    name = metadata.get("name") if isinstance(metadata, dict) else None
    if not isinstance(name, str) or not _EXECUTION_NAME.fullmatch(name):
        raise GraphOperationUnavailableError("Cloud Run did not return an execution name.")
    return name


def _parse_execution(payload: object) -> CloudRunExecution:
    if not isinstance(payload, dict):
        raise GraphOperationUnavailableError("Cloud Run returned an invalid execution.")
    name = _execution_name(cast("dict[str, object]", payload))
    status = payload.get("status")
    if not isinstance(status, dict):
        status = {}
    conditions = status.get("conditions")
    completed_status: str | None = None
    started = False
    if isinstance(conditions, list):
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            if condition.get("type") == "Completed":
                completed_status = cast("str | None", condition.get("status"))
            if condition.get("type") == "Started" and condition.get("status") == "True":
                started = True
    failed_count = _count(status.get("failedCount"))
    if completed_status == "True" and failed_count == 0:
        state = "succeeded"
    elif completed_status in {"True", "False"}:
        state = "failed"
    else:
        state = "running" if started else "starting"
    return CloudRunExecution(
        name=name,
        state=state,
        started_at=_optional_string(status.get("startTime")),
        completed_at=_optional_string(status.get("completionTime")),
        succeeded_count=_count(status.get("succeededCount")),
        failed_count=failed_count,
        log_uri=_optional_string(status.get("logUri")),
    )


def _count(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _run_record(record: RunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "pipeline_id": record.pipeline_id,
        "source": record.source,
        "status": record.status.value,
        "stage": record.stage.value,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "endpoints": record.endpoints,
        "extracted": record.extracted,
        "affected": record.affected,
        "models": record.models,
        "assertions": record.assertions,
        "assets": record.assets,
        "failure_stage": record.failure_stage.value if record.failure_stage is not None else None,
    }
