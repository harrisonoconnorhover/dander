"""Hosted Control adapter for existing GCP Cloud Run Jobs.

Control retains its provider-neutral lifecycle and durable store.  This adapter uses the Job's
``startExecutionToken`` as a deterministic execution suffix, so a lost response or Control restart
adopts the same Cloud Run execution instead of submitting a duplicate.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import quote

from dander.control.execution_results import (
    ExecutionResultCollectionError,
    collect_execution_result_summary,
)
from dander.control.orchestration import (
    BackendExecutionState,
    BackendHandle,
    BackendLogPage,
    BackendLogRecord,
    BackendObservation,
    CleanupState,
    ExecutionBackendError,
    ExecutionPlan,
    ResultsState,
    RunOutcome,
    RunTrigger,
    TriggerKind,
)
from dander.identity.aws_google import FargateIdentityError
from dander.identity.control_google import (
    GoogleControlIdentityError,
    prepare_control_google_identity,
)
from dander.pipeline.repair import GraphRepairWindow
from dander.providers.cloud_run import CloudRunBinding, CloudRunOperationError

if TYPE_CHECKING:
    from collections.abc import Callable

_BACKEND_ID = "cloud_run"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_TOKEN = re.compile(r"^[0-9a-f]{16}$")
_ARTIFACT_IMAGE = re.compile(
    r"^(?P<region>[a-z]+(?:-[a-z0-9]+)+[0-9])-docker\.pkg\.dev/"
    r"(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])/"
    r"[a-z][a-z0-9._/-]*@sha256:[0-9a-f]{64}$"
)
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_API_ROOT = "https://run.googleapis.com/v2"
_LOGGING_ENDPOINT = "https://logging.googleapis.com/v2/entries:list"
_MAX_CURSOR_LENGTH = 2_048
_MAX_LOG_MESSAGE_LENGTH = 16_384
_DEFAULT_TIMEOUT_SECONDS = 30.0
_REPAIR_ENV = "DANDER_GRAPH_REPAIR_JSON"
_REPAIR_ARGUMENTS = ("--graph-repair-contract", "io.dander.graph-repair/v1")


class _Response(Protocol):
    status_code: int

    def json(self) -> object: ...


class _Transport(Protocol):
    def request(self, method: str, url: str, **kwargs: object) -> _Response: ...

    def close(self) -> None: ...


class _GoogleCallError(RuntimeError):
    def __init__(self, operation: str, status_code: int | None) -> None:
        super().__init__(f"Google {operation} failed")
        self.status_code = status_code


class CloudRunExecutionBackend:
    """Execute explicitly registered Control plans through existing Cloud Run Jobs."""

    def __init__(
        self,
        plan_bindings: Mapping[str, CloudRunBinding],
        *,
        transport: _Transport | None = None,
        credential_factory: Callable[[], object] = prepare_control_google_identity,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        bindings = dict(plan_bindings)
        if not bindings or any(_SHA256.fullmatch(revision) is None for revision in bindings):
            raise ExecutionBackendError("Cloud Run plan bindings are missing or invalid.")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ExecutionBackendError("Cloud Run backend timeout is invalid.")
        coordinates = {(item.project_id, item.region) for item in bindings.values()}
        if len(coordinates) != 1:
            raise ExecutionBackendError(
                "Cloud Run plan bindings must share one GCP project and region."
            )
        for binding in bindings.values():
            _validate_binding(binding)
        if transport is None:
            try:
                from google.auth.transport.requests import AuthorizedSession

                transport = cast(
                    "_Transport",
                    AuthorizedSession(credential_factory()),  # type: ignore[no-untyped-call]
                )
            except (FargateIdentityError, GoogleControlIdentityError, ImportError) as error:
                raise ExecutionBackendError(
                    "Cloud Run workload identity is unavailable."
                ) from error
        self._plan_bindings = bindings
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timeout = float(timeout_seconds)
        self._closed = False

    def submit_or_adopt(
        self,
        plan: ExecutionPlan,
        *,
        run_id: str,
        attempt_id: str,
        trigger: RunTrigger,
    ) -> BackendHandle:
        """Start or adopt the deterministic Cloud Run execution for one attempt."""
        binding = self._binding_for(plan)
        token = _execution_token(run_id, attempt_id)
        try:
            execution_resource = binding.execution_resource(token)
        except CloudRunOperationError as error:
            raise ExecutionBackendError("Cloud Run execution identity is invalid.") from error
        handle = BackendHandle(backend_id=_BACKEND_ID, execution_id=execution_resource)
        repair_payload = _repair_payload(plan, trigger, execution_resource)
        execution = self._try_get_execution(execution_resource)
        if execution is not None:
            self._validate_submission_execution(
                plan, binding, execution, execution_resource, repair_payload
            )
            return handle

        job = self._get_job(binding)
        self._validate_job(plan, binding, job)
        if job.get("startExecutionToken") == token:
            _require_repair_payload(
                _job_task(job), repair_payload, _execution_arguments(plan, repair_payload)
            )
            return handle
        etag = job.get("etag")
        if not isinstance(etag, str) or not etag:
            raise ExecutionBackendError("Cloud Run Job does not expose a concurrency token.")
        update = _job_update(job)
        _set_repair_payload(update, plan, repair_payload)
        update["startExecutionToken"] = token
        try:
            self._request_json(
                "start execution",
                "PATCH",
                f"{_API_ROOT}/{binding.job_resource}",
                json=update,
                expected=(200,),
            )
        except _GoogleCallError as start_error:
            execution = self._try_get_execution(execution_resource)
            if execution is not None:
                self._validate_submission_execution(
                    plan, binding, execution, execution_resource, repair_payload
                )
                return handle
            reconciled_job = self._get_job(binding)
            self._validate_job(plan, binding, reconciled_job)
            if reconciled_job.get("startExecutionToken") == token:
                _require_repair_payload(
                    _job_task(reconciled_job),
                    repair_payload,
                    _execution_arguments(plan, repair_payload),
                )
                return handle
            raise ExecutionBackendError(
                "Cloud Run execution could not be created or adopted."
            ) from start_error
        return handle

    def observe(self, plan: ExecutionPlan, handle: BackendHandle) -> BackendObservation:
        """Normalize Cloud Run execution state and terminal worker cleanup."""
        binding = self._binding_and_handle(plan, handle)
        execution = self._try_get_execution(handle.execution_id)
        if execution is None:
            return self._running("starting")
        self._validate_execution(binding, execution, handle.execution_id)
        completion_time = execution.get("completionTime")
        terminal_cancellation = _terminal_cancellation(execution)
        if completion_time is None and not terminal_cancellation:
            return self._running("running" if execution.get("startTime") else "starting")
        if completion_time is not None and not isinstance(completion_time, str):
            raise ExecutionBackendError("Cloud Run returned an invalid completion time.")
        task_count = _count(execution.get("taskCount"))
        succeeded = _count(execution.get("succeededCount"))
        failed = _count(execution.get("failedCount"))
        canceled = _count(execution.get("cancelledCount"))
        if canceled > 0 or terminal_cancellation:
            outcome = RunOutcome.CANCELED
            stage = "canceled"
            failure_code = "operator_cancelled"
        elif task_count > 0 and succeeded >= task_count and failed == 0:
            outcome = RunOutcome.SUCCEEDED
            stage = "succeeded"
            failure_code = None
        else:
            outcome = RunOutcome.FAILED
            stage = "failed"
            failure_code = _execution_failure_code(execution)
        cleanup_state = self._restore_repair_job(plan, binding, execution, handle.execution_id)
        result_summary = None
        if outcome is RunOutcome.SUCCEEDED:
            try:
                result_summary = collect_execution_result_summary(
                    lambda cursor, limit: self.logs(
                        plan,
                        handle,
                        cursor=cursor,
                        limit=limit,
                    ),
                    pipeline_id=plan.execution_template.pipeline_id,
                )
            except ExecutionResultCollectionError as error:
                raise ExecutionBackendError(
                    "Cloud Run result summary is temporarily unavailable."
                ) from error
        return BackendObservation(
            execution_state=BackendExecutionState.TERMINAL,
            outcome=outcome,
            results_state=(
                ResultsState.AVAILABLE
                if outcome is RunOutcome.SUCCEEDED
                else ResultsState.UNAVAILABLE
            ),
            cleanup_state=cleanup_state,
            observed_at=self._now(),
            stage=stage,
            failure_code=failure_code,
            result_summary=result_summary,
        )

    def _restore_repair_job(
        self,
        plan: ExecutionPlan,
        binding: CloudRunBinding,
        execution: Mapping[str, object],
        resource: str,
    ) -> CleanupState:
        """Restore only this completed execution's still-owned Job configuration."""
        repair_payload = _execution_repair_payload(plan, execution, resource)
        if repair_payload is None:
            return CleanupState.CONFIRMED
        self._validate_submission_execution(plan, binding, execution, resource, repair_payload)
        token = resource.rsplit("-", maxsplit=1)[-1]
        try:
            job = self._get_job(binding)
        except ExecutionBackendError:
            return CleanupState.PENDING
        if not _job_has_repair_selection(job, token=token, payload=repair_payload, plan=plan):
            return CleanupState.CONFIRMED
        # A new deployment or launch supersedes ownership. Do not restore an old plan over it.
        try:
            self._validate_job(plan, binding, job)
        except ExecutionBackendError:
            return CleanupState.CONFIRMED
        if not isinstance(job.get("etag"), str) or not job["etag"]:
            return CleanupState.PENDING
        update = _job_update(job)
        _set_repair_payload(update, plan, None)
        # Lost responses and ETag conflicts are reconciled below before another patch.
        with suppress(_GoogleCallError, ExecutionBackendError):
            # Omitting both execution-token fields updates configuration without starting work.
            self._request_json(
                "restore job",
                "PATCH",
                f"{_API_ROOT}/{binding.job_resource}",
                json=update,
                expected=(200,),
            )
        try:
            current = self._get_job(binding)
        except ExecutionBackendError:
            return CleanupState.PENDING
        return (
            CleanupState.PENDING
            if _job_has_repair_selection(current, token=token, payload=repair_payload, plan=plan)
            else CleanupState.CONFIRMED
        )

    def logs(
        self,
        plan: ExecutionPlan,
        handle: BackendHandle,
        *,
        cursor: str | None,
        limit: int,
    ) -> BackendLogPage:
        """Return one bounded Cloud Logging page for the owned execution."""
        binding = self._binding_and_handle(plan, handle)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ExecutionBackendError("Cloud Run log page size is invalid.")
        if cursor is not None and (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > _MAX_CURSOR_LENGTH
            or "\n" in cursor
        ):
            raise ExecutionBackendError("Cloud Run log cursor is invalid.")
        execution_name = handle.execution_id.rsplit("/", maxsplit=1)[-1]
        payload: dict[str, object] = {
            "resourceNames": [f"projects/{binding.project_id}"],
            "filter": (
                'resource.type="cloud_run_job" AND '
                f'resource.labels.job_name="{binding.job_name}" AND '
                f'labels."run.googleapis.com/execution_name"="{execution_name}"'
            ),
            "orderBy": "timestamp asc",
            "pageSize": limit,
        }
        if cursor is not None:
            payload["pageToken"] = cursor
        try:
            response = self._request_json(
                "read logs",
                "POST",
                _LOGGING_ENDPOINT,
                json=payload,
                expected=(200,),
            )
        except _GoogleCallError as error:
            raise ExecutionBackendError("Cloud Run logs are unavailable.") from error
        raw_entries = response.get("entries", [])
        if not isinstance(raw_entries, list) or len(raw_entries) > limit:
            raise ExecutionBackendError("Cloud Run returned an invalid log page.")
        records: list[BackendLogRecord] = []
        for entry in raw_entries:
            if not isinstance(entry, Mapping):
                raise ExecutionBackendError("Cloud Run returned an invalid log entry.")
            timestamp = _timestamp(entry.get("timestamp"))
            message = _log_message(entry)
            records.append(BackendLogRecord(occurred_at=timestamp, message=message))
        next_cursor = response.get("nextPageToken")
        if next_cursor is not None and (
            not isinstance(next_cursor, str)
            or not next_cursor
            or len(next_cursor) > _MAX_CURSOR_LENGTH
            or "\n" in next_cursor
        ):
            raise ExecutionBackendError("Cloud Run returned an invalid log cursor.")
        if next_cursor == cursor:
            next_cursor = None
        return BackendLogPage(records=tuple(records), next_cursor=next_cursor)

    def cancel(self, plan: ExecutionPlan, handle: BackendHandle) -> None:
        """Idempotently cancel one owned non-terminal Cloud Run execution."""
        binding = self._binding_and_handle(plan, handle)
        execution = self._try_get_execution(handle.execution_id)
        if execution is None:
            raise ExecutionBackendError("Cloud Run execution is not available for cancellation.")
        self._validate_execution(binding, execution, handle.execution_id)
        if execution.get("completionTime") is not None or _terminal_cancellation(execution):
            return
        etag = execution.get("etag")
        request = {"etag": etag} if isinstance(etag, str) and etag else {}
        try:
            self._request_json(
                "cancel execution",
                "POST",
                f"{_API_ROOT}/{handle.execution_id}:cancel",
                json=request,
                expected=(200,),
            )
        except _GoogleCallError as cancel_error:
            reconciled = self._try_get_execution(handle.execution_id)
            if reconciled is not None and (
                reconciled.get("completionTime") is not None or _terminal_cancellation(reconciled)
            ):
                return
            raise ExecutionBackendError(
                "Cloud Run cancellation could not be reconciled."
            ) from cancel_error

    def close(self) -> None:
        """Close the authorized transport exactly once."""
        if self._closed:
            return
        try:
            self._transport.close()
        except Exception as error:
            raise ExecutionBackendError("Cloud Run backend shutdown failed.") from error
        self._closed = True

    def _binding_for(self, plan: ExecutionPlan) -> CloudRunBinding:
        if plan.backend_id != _BACKEND_ID:
            raise ExecutionBackendError("The execution plan does not select Cloud Run.")
        binding = self._plan_bindings.get(plan.revision)
        if binding is None:
            raise ExecutionBackendError("The execution plan is not registered with Cloud Run.")
        template = plan.execution_template
        image = _ARTIFACT_IMAGE.fullmatch(plan.image)
        if (
            template.pipeline_id != binding.pipeline_id
            or plan.profile_id != binding.profile_id
            or image is None
            or image.group("project") != binding.project_id
            or image.group("region") != binding.region
            or template.workload_identity != binding.runtime_service_account
            or _REPAIR_ARGUMENTS[0] in template.command
            or any(name == _REPAIR_ENV for name, _ in template.environment)
        ):
            raise ExecutionBackendError("The execution plan does not match its Cloud Run binding.")
        return binding

    def _binding_and_handle(
        self,
        plan: ExecutionPlan,
        handle: BackendHandle,
    ) -> CloudRunBinding:
        binding = self._binding_for(plan)
        prefix = f"{binding.job_resource}/executions/{binding.job_name}-"
        token = handle.execution_id.removeprefix(prefix)
        if (
            handle.backend_id != _BACKEND_ID
            or not handle.execution_id.startswith(prefix)
            or _EXECUTION_TOKEN.fullmatch(token) is None
        ):
            raise ExecutionBackendError("The execution handle is outside its Cloud Run binding.")
        return binding

    def _get_job(self, binding: CloudRunBinding) -> Mapping[str, object]:
        try:
            return self._request_json(
                "read job",
                "GET",
                f"{_API_ROOT}/{binding.job_resource}",
                expected=(200,),
            )
        except _GoogleCallError as error:
            raise ExecutionBackendError("Cloud Run Job lookup failed.") from error

    def _try_get_execution(self, resource: str) -> Mapping[str, object] | None:
        try:
            return self._request_json(
                "read execution",
                "GET",
                f"{_API_ROOT}/{resource}",
                expected=(200,),
            )
        except _GoogleCallError as error:
            if error.status_code == 404:
                return None
            raise ExecutionBackendError("Cloud Run execution lookup failed.") from error

    @staticmethod
    def _validate_job(
        plan: ExecutionPlan,
        binding: CloudRunBinding,
        job: Mapping[str, object],
    ) -> None:
        raw_template = job.get("template")
        template: Mapping[str, object] = (
            cast("Mapping[str, object]", raw_template) if isinstance(raw_template, Mapping) else {}
        )
        raw_task_template = template.get("template")
        task_template: Mapping[str, object] = (
            cast("Mapping[str, object]", raw_task_template)
            if isinstance(raw_task_template, Mapping)
            else {}
        )
        containers = task_template.get("containers")
        container = containers[0] if isinstance(containers, list) and len(containers) == 1 else None
        plan_template = plan.execution_template
        if (
            job.get("name") != binding.job_resource
            or not isinstance(container, Mapping)
            or container.get("image") != plan.image
            or container.get("args")
            not in [list(plan_template.command), list(plan_template.command + _REPAIR_ARGUMENTS)]
            or template.get("taskCount") != plan_template.schedule.task_count
            or template.get("parallelism") != plan_template.schedule.maximum_parallelism
            or task_template.get("serviceAccount") != binding.runtime_service_account
        ):
            raise ExecutionBackendError(
                "The deployed Cloud Run Job does not match its immutable execution plan."
            )

    @staticmethod
    def _validate_execution(
        binding: CloudRunBinding,
        execution: Mapping[str, object],
        resource: str,
    ) -> None:
        if execution.get("name") != resource or execution.get("job") != binding.job_name:
            raise ExecutionBackendError("Cloud Run returned an unexpected execution.")

    def _validate_submission_execution(
        self,
        plan: ExecutionPlan,
        binding: CloudRunBinding,
        execution: Mapping[str, object],
        resource: str,
        repair_payload: str | None,
    ) -> None:
        self._validate_execution(binding, execution, resource)
        task = execution.get("template")
        containers = task.get("containers") if isinstance(task, Mapping) else None
        container = containers[0] if isinstance(containers, list) and len(containers) == 1 else None
        if (
            not isinstance(task, Mapping)
            or not isinstance(container, Mapping)
            or task.get("serviceAccount") != binding.runtime_service_account
            or container.get("args") != _execution_arguments(plan, repair_payload)
            or not self._execution_image_matches(plan.image, container.get("image"))
        ):
            raise ExecutionBackendError(
                "The Cloud Run execution does not match its immutable execution plan."
            )
        _require_repair_payload(task, repair_payload, _execution_arguments(plan, repair_payload))

    def _execution_image_matches(self, expected: str, actual: object) -> bool:
        if actual == expected:
            return True
        if (
            not isinstance(actual, str)
            or _ARTIFACT_IMAGE.fullmatch(actual) is None
            or actual.rpartition("@")[0] != expected.rpartition("@")[0]
        ):
            return False
        # Cloud Run resolves a configured OCI index to its Linux/AMD64 manifest.
        # Verify that relationship against the accepted index, never a mutable tag.
        parts = expected.split("/", maxsplit=3)
        if len(parts) != 4:
            return False
        registry, project, repository, image = parts
        region = registry.removesuffix("-docker.pkg.dev")
        resource = (
            f"projects/{project}/locations/{region}/repositories/{repository}/"
            f"dockerImages/{quote(image, safe='')}"
        )
        try:
            metadata = self._request_json(
                "read image",
                "GET",
                f"https://artifactregistry.googleapis.com/v1/{resource}",
                expected=(200,),
            )
        except _GoogleCallError as error:
            raise ExecutionBackendError("Cloud Run image identity lookup failed.") from error
        manifests = metadata.get("imageManifests")
        if (
            metadata.get("uri") != expected
            or metadata.get("mediaType")
            not in (
                "application/vnd.oci.image.index.v1+json",
                "application/vnd.docker.distribution.manifest.list.v2+json",
            )
            or not isinstance(manifests, list)
        ):
            return False
        selected = [
            item
            for item in manifests
            if isinstance(item, Mapping)
            and item.get("os") == "linux"
            and item.get("architecture") == "amd64"
        ]
        return len(selected) == 1 and selected[0].get("digest") == actual.rpartition("@")[2]

    def _request_json(
        self,
        operation: str,
        method: str,
        url: str,
        *,
        expected: tuple[int, ...],
        **kwargs: object,
    ) -> Mapping[str, object]:
        try:
            response = self._transport.request(
                method,
                url,
                timeout=self._timeout,
                **kwargs,
            )
        except Exception as error:
            raise _GoogleCallError(operation, None) from error
        status = getattr(response, "status_code", None)
        if not isinstance(status, int) or status not in expected:
            raise _GoogleCallError(operation, status if isinstance(status, int) else None)
        try:
            payload = response.json()
        except Exception as error:
            raise ExecutionBackendError("Cloud Run returned invalid provider JSON.") from error
        if not isinstance(payload, Mapping):
            raise ExecutionBackendError("Cloud Run returned an invalid provider response.")
        return cast("Mapping[str, object]", payload)

    def _running(self, stage: str) -> BackendObservation:
        return BackendObservation(
            execution_state=BackendExecutionState.RUNNING,
            outcome=RunOutcome.UNKNOWN,
            results_state=ResultsState.PENDING,
            cleanup_state=CleanupState.PENDING,
            observed_at=self._now(),
            stage=stage,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ExecutionBackendError("Cloud Run backend clock must return an aware datetime.")
        return now.astimezone(UTC)


def _validate_binding(binding: CloudRunBinding) -> None:
    if not binding.job_resource.endswith(f"/jobs/{binding.job_name}"):
        raise ExecutionBackendError("A Cloud Run plan binding is invalid.")


def _repair_payload(plan: ExecutionPlan, trigger: RunTrigger, resource: str) -> str | None:
    if trigger.repair_window is None:
        return None
    if plan.execution_template.schedule.task_count != 1:
        raise ExecutionBackendError("Date repair requires a single Cloud Run task.")
    return json.dumps(
        {
            **trigger.repair_window.model_dump(mode="json"),
            "graph_content_sha256": plan.graph_content_sha256,
            "execution_name": resource.rsplit("/", maxsplit=1)[-1],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _execution_arguments(plan: ExecutionPlan, repair_payload: str | None) -> list[str]:
    suffix = _REPAIR_ARGUMENTS if repair_payload is not None else ()
    return list(plan.execution_template.command + suffix)


def _execution_repair_payload(
    plan: ExecutionPlan, execution: Mapping[str, object], resource: str
) -> str | None:
    task = execution.get("template")
    containers = task.get("containers") if isinstance(task, Mapping) else None
    container = containers[0] if isinstance(containers, list) and len(containers) == 1 else None
    environment = container.get("env", []) if isinstance(container, Mapping) else []
    if not isinstance(environment, list):
        raise ExecutionBackendError("Cloud Run execution environment is invalid.")
    selected = [
        item
        for item in environment
        if isinstance(item, Mapping) and item.get("name") == _REPAIR_ENV
    ]
    if not selected:
        return None
    raw = selected[0].get("value")
    try:
        if len(selected) != 1 or not isinstance(raw, str) or len(raw) > 1024:
            raise ValueError("invalid repair selection")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("invalid repair selection")
        window = GraphRepairWindow.model_validate(
            {"start_date": payload["start_date"], "end_date": payload["end_date"]}
        )
        trigger = RunTrigger(kind=TriggerKind.API, trigger_id="control-api", repair_window=window)
        if raw != _repair_payload(plan, trigger, resource):
            raise ValueError("repair selection does not match execution")
    except (KeyError, TypeError, ValueError) as error:
        raise ExecutionBackendError("Cloud Run execution repair selection is invalid.") from error
    return raw


def _job_has_repair_selection(
    job: Mapping[str, object], *, token: str, payload: str, plan: ExecutionPlan
) -> bool:
    if job.get("startExecutionToken") != token:
        return False
    try:
        _require_repair_payload(_job_task(job), payload, _execution_arguments(plan, payload))
    except ExecutionBackendError:
        return False
    return True


def _job_update(job: Mapping[str, object]) -> dict[str, object]:
    return {
        key: deepcopy(value)
        for key, value in job.items()
        if key
        in {
            "name",
            "labels",
            "annotations",
            "launchStage",
            "binaryAuthorization",
            "template",
            "client",
            "clientVersion",
            "etag",
        }
    }


def _job_task(job: Mapping[str, object]) -> Mapping[str, object]:
    template = job.get("template")
    task = template.get("template") if isinstance(template, Mapping) else None
    if not isinstance(task, Mapping):
        raise ExecutionBackendError("Cloud Run Job task template is invalid.")
    return cast("Mapping[str, object]", task)


def _require_repair_payload(
    task: Mapping[str, object], expected: str | None, arguments: list[str]
) -> None:
    containers = task.get("containers")
    container = containers[0] if isinstance(containers, list) and len(containers) == 1 else None
    if not isinstance(container, Mapping):
        raise ExecutionBackendError("Cloud Run task container is invalid.")
    if container.get("args") != arguments:
        raise ExecutionBackendError("Cloud Run execution has a different date-repair command.")
    environment = container.get("env", [])
    if not isinstance(environment, list) or any(
        not isinstance(item, Mapping) for item in environment
    ):
        raise ExecutionBackendError("Cloud Run task environment is invalid.")
    selected = [item for item in environment if item.get("name") == _REPAIR_ENV]
    wanted = [{"name": _REPAIR_ENV, "value": expected}] if expected is not None else []
    if selected != wanted:
        raise ExecutionBackendError("Cloud Run execution has a different date-repair selection.")


def _set_repair_payload(
    update: dict[str, object], plan: ExecutionPlan, payload: str | None
) -> None:
    # The patch has already been deep-copied. Preserve every unrelated container setting,
    # and always clear a previous selection when a normal run follows a repair.
    task = _job_task(update)
    containers = task.get("containers")
    container = containers[0] if isinstance(containers, list) and len(containers) == 1 else None
    if not isinstance(container, dict):
        raise ExecutionBackendError("Cloud Run task container is invalid.")
    environment = container.get("env", [])
    if not isinstance(environment, list) or any(
        not isinstance(item, Mapping) for item in environment
    ):
        raise ExecutionBackendError("Cloud Run task environment is invalid.")
    remaining = [item for item in environment if item.get("name") != _REPAIR_ENV]
    if payload is not None:
        remaining.append({"name": _REPAIR_ENV, "value": payload})
    if remaining or "env" in container:
        container["env"] = remaining
    container["args"] = _execution_arguments(plan, payload)


def _execution_token(run_id: str, attempt_id: str) -> str:
    return hashlib.sha256(f"{run_id}\0{attempt_id}".encode()).hexdigest()[:16]


def _count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _terminal_cancellation(execution: Mapping[str, object]) -> bool:
    """Cloud Run omits completionTime when cancellation precedes task startup."""
    conditions = execution.get("conditions")
    return isinstance(conditions, list) and any(
        isinstance(condition, Mapping)
        and condition.get("type") == "Completed"
        and condition.get("state") == "CONDITION_FAILED"
        and condition.get("executionReason") == "CANCELLED"
        for condition in conditions
    )


def _execution_failure_code(execution: Mapping[str, object]) -> str:
    conditions = execution.get("conditions")
    if isinstance(conditions, list):
        for condition in conditions:
            if not isinstance(condition, Mapping) or condition.get("state") != "CONDITION_FAILED":
                continue
            reason = condition.get("reason")
            if isinstance(reason, str):
                candidate = reason.casefold().replace("_", "-")
                candidate = re.sub(r"[^a-z0-9.-]+", "-", candidate).strip("-")
                if _FAILURE_CODE.fullmatch(candidate):
                    return candidate
    return "launcher_execution_failed"


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ExecutionBackendError("Cloud Run returned an invalid log timestamp.")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExecutionBackendError("Cloud Run returned an invalid log timestamp.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ExecutionBackendError("Cloud Run returned an invalid log timestamp.")
    return timestamp.astimezone(UTC)


def _log_message(entry: Mapping[str, object]) -> str:
    text = entry.get("textPayload")
    if isinstance(text, str):
        message = text or "(empty log entry)"
    else:
        payload = entry.get("jsonPayload")
        if isinstance(payload, Mapping):
            message = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        else:
            message = "(structured log entry)"
    if len(message) > _MAX_LOG_MESSAGE_LENGTH:
        return message[: _MAX_LOG_MESSAGE_LENGTH - 3] + "..."
    return message


__all__ = ["CloudRunExecutionBackend"]
