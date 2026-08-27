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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

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
)
from dander.identity.aws_google import FargateIdentityError, prepare_fargate_google_identity
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
        credential_factory: Callable[[], object] = prepare_fargate_google_identity,
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
            except (FargateIdentityError, ImportError) as error:
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
        del trigger
        binding = self._binding_for(plan)
        token = _execution_token(run_id, attempt_id)
        try:
            execution_resource = binding.execution_resource(token)
        except CloudRunOperationError as error:
            raise ExecutionBackendError("Cloud Run execution identity is invalid.") from error
        handle = BackendHandle(backend_id=_BACKEND_ID, execution_id=execution_resource)
        execution = self._try_get_execution(execution_resource)
        if execution is not None:
            self._validate_execution(binding, execution, execution_resource)
            return handle

        job = self._get_job(binding)
        self._validate_job(plan, binding, job)
        if job.get("startExecutionToken") == token:
            return handle
        etag = job.get("etag")
        if not isinstance(etag, str) or not etag:
            raise ExecutionBackendError("Cloud Run Job does not expose a concurrency token.")
        update = {
            key: value
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
                self._validate_execution(binding, execution, execution_resource)
                return handle
            reconciled_job = self._get_job(binding)
            self._validate_job(plan, binding, reconciled_job)
            if reconciled_job.get("startExecutionToken") == token:
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
        if completion_time is None:
            return self._running("running" if execution.get("startTime") else "starting")
        if not isinstance(completion_time, str):
            raise ExecutionBackendError("Cloud Run returned an invalid completion time.")
        task_count = _count(execution.get("taskCount"))
        succeeded = _count(execution.get("succeededCount"))
        failed = _count(execution.get("failedCount"))
        canceled = _count(execution.get("cancelledCount"))
        if canceled > 0:
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
        return BackendObservation(
            execution_state=BackendExecutionState.TERMINAL,
            outcome=outcome,
            results_state=(
                ResultsState.AVAILABLE
                if outcome is RunOutcome.SUCCEEDED
                else ResultsState.UNAVAILABLE
            ),
            cleanup_state=CleanupState.CONFIRMED,
            observed_at=self._now(),
            stage=stage,
            failure_code=failure_code,
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
        if execution.get("completionTime") is not None:
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
            if reconciled is not None and reconciled.get("completionTime") is not None:
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
            or container.get("args") != list(plan_template.command)
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


def _execution_token(run_id: str, attempt_id: str) -> str:
    return hashlib.sha256(f"{run_id}\0{attempt_id}".encode()).hexdigest()[:16]


def _count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


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
