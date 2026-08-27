"""Hosted Control adapter for fixed-size Managed Service for Apache Spark batches."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

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
)
from dander.identity.aws_google import FargateIdentityError, prepare_fargate_google_identity
from dander.physical_plan import ExchangeTransport, PhysicalExecutionMode

if TYPE_CHECKING:
    from collections.abc import Callable

    from dander.providers.dataproc_serverless import DataprocServerlessBinding

_BACKEND_ID = "dataproc_serverless"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BATCH_ID = re.compile(r"^dander-[0-9a-f]{40}$")
_ATTEMPT_ID = re.compile(r"^attempt-([1-9][0-9]*)-[0-9a-f]{20}$")
_ARTIFACT_IMAGE = re.compile(
    r"^(?P<region>[a-z]+(?:-[a-z0-9]+)+[0-9])-docker\.pkg\.dev/"
    r"(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])/"
    r"[a-z][a-z0-9._/-]*@sha256:[0-9a-f]{64}$"
)
_OPERATION = re.compile(
    r"^projects/(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])/(?:locations|regions)/"
    r"(?P<region>[a-z]+(?:-[a-z0-9]+)+[0-9])/operations/[A-Za-z0-9._-]{1,128}$"
)
_TERMINAL_STATES = {"CANCELLED", "FAILED", "SUCCEEDED"}
_RUNNING_STATES = {"PENDING", "RUNNING", "CANCELLING"}
_API_ROOT = "https://dataproc.googleapis.com/v1"
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


class DataprocServerlessExecutionBackend:
    """Execute registered distributed plans as fixed-size serverless Spark batches."""

    def __init__(
        self,
        plan_bindings: Mapping[str, DataprocServerlessBinding],
        *,
        transport: _Transport | None = None,
        credential_factory: Callable[[], object] = prepare_fargate_google_identity,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        bindings = dict(plan_bindings)
        if not bindings or any(_SHA256.fullmatch(revision) is None for revision in bindings):
            raise ExecutionBackendError("Managed Spark plan bindings are missing or invalid.")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ExecutionBackendError("Managed Spark backend timeout is invalid.")
        coordinates = {(item.project_id, item.region) for item in bindings.values()}
        if len(coordinates) != 1:
            raise ExecutionBackendError(
                "Managed Spark plan bindings must share one GCP project and region."
            )
        if transport is None:
            try:
                from google.auth.transport.requests import AuthorizedSession

                transport = cast(
                    "_Transport",
                    AuthorizedSession(credential_factory()),  # type: ignore[no-untyped-call]
                )
            except (FargateIdentityError, ImportError) as error:
                raise ExecutionBackendError(
                    "Managed Spark workload identity is unavailable."
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
        """Create or adopt one deterministic batch for the logical attempt."""
        del trigger
        binding = self._binding_for(plan)
        batch_id = _batch_id(run_id, attempt_id)
        batch_resource = binding.batch_resource(batch_id)
        handle = BackendHandle(backend_id=_BACKEND_ID, execution_id=batch_resource)
        batch = self._try_get_batch(batch_resource)
        if batch is not None:
            self._validate_batch(plan, binding, batch, run_id=run_id, attempt_id=attempt_id)
            return handle

        desired = self._desired_batch(plan, binding, run_id=run_id, attempt_id=attempt_id)
        try:
            operation = self._request_json(
                "create batch",
                "POST",
                f"{_API_ROOT}/{binding.parent_resource}/batches",
                params={
                    "batchId": batch_id,
                    "requestId": _request_id(run_id, attempt_id),
                },
                json=desired,
                expected=(200,),
            )
            self._validate_operation(binding, operation)
        except _GoogleCallError as create_error:
            batch = self._try_get_batch(batch_resource)
            if batch is not None:
                self._validate_batch(plan, binding, batch, run_id=run_id, attempt_id=attempt_id)
                return handle
            raise ExecutionBackendError(
                "Managed Spark batch could not be created or adopted."
            ) from create_error
        return handle

    def observe(self, plan: ExecutionPlan, handle: BackendHandle) -> BackendObservation:
        """Normalize batch state, results, and provider-managed compute cleanup."""
        binding = self._binding_and_handle(plan, handle)
        batch = self._try_get_batch(handle.execution_id)
        if batch is None:
            return self._running("starting")
        self._validate_observed_batch(plan, binding, batch, handle.execution_id)
        state = batch.get("state")
        if not isinstance(state, str) or state not in _RUNNING_STATES | _TERMINAL_STATES:
            raise ExecutionBackendError("Managed Spark returned an unknown batch state.")
        if state in _RUNNING_STATES:
            return self._running(state.casefold())
        if state == "SUCCEEDED":
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
                    "Managed Spark result summary is temporarily unavailable."
                ) from error
            return BackendObservation(
                execution_state=BackendExecutionState.TERMINAL,
                outcome=RunOutcome.SUCCEEDED,
                results_state=ResultsState.AVAILABLE,
                cleanup_state=CleanupState.CONFIRMED,
                observed_at=self._now(),
                stage="succeeded",
                result_summary=result_summary,
            )
        canceled = state == "CANCELLED"
        return BackendObservation(
            execution_state=BackendExecutionState.TERMINAL,
            outcome=RunOutcome.CANCELED if canceled else RunOutcome.FAILED,
            results_state=ResultsState.UNAVAILABLE,
            cleanup_state=CleanupState.CONFIRMED,
            observed_at=self._now(),
            stage="canceled" if canceled else "failed",
            failure_code="operator_cancelled" if canceled else "spark_batch_failed",
        )

    def logs(
        self,
        plan: ExecutionPlan,
        handle: BackendHandle,
        *,
        cursor: str | None,
        limit: int,
    ) -> BackendLogPage:
        """Return one bounded Cloud Logging output page for the exact batch."""
        binding = self._binding_and_handle(plan, handle)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ExecutionBackendError("Managed Spark log page size is invalid.")
        if cursor is not None and (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > _MAX_CURSOR_LENGTH
            or "\n" in cursor
        ):
            raise ExecutionBackendError("Managed Spark log cursor is invalid.")
        batch_id = handle.execution_id.rsplit("/", maxsplit=1)[-1]
        payload: dict[str, object] = {
            "resourceNames": [f"projects/{binding.project_id}"],
            "filter": (
                'resource.type="cloud_dataproc_batch" AND '
                f'resource.labels.location="{binding.region}" AND '
                f'resource.labels.batch_id="{batch_id}" AND '
                f'logName="projects/{binding.project_id}/logs/'
                'dataproc.googleapis.com%2Foutput"'
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
            raise ExecutionBackendError("Managed Spark logs are unavailable.") from error
        raw_entries = response.get("entries", [])
        if not isinstance(raw_entries, list) or len(raw_entries) > limit:
            raise ExecutionBackendError("Managed Spark returned an invalid log page.")
        records: list[BackendLogRecord] = []
        for entry in raw_entries:
            if not isinstance(entry, Mapping):
                raise ExecutionBackendError("Managed Spark returned an invalid log entry.")
            level = entry.get("severity")
            records.append(
                BackendLogRecord(
                    occurred_at=_timestamp(entry.get("timestamp")),
                    message=_log_message(entry),
                    level=level.casefold() if isinstance(level, str) and level else None,
                )
            )
        next_cursor = response.get("nextPageToken")
        if next_cursor is not None and (
            not isinstance(next_cursor, str)
            or not next_cursor
            or len(next_cursor) > _MAX_CURSOR_LENGTH
            or "\n" in next_cursor
        ):
            raise ExecutionBackendError("Managed Spark returned an invalid log cursor.")
        if next_cursor == cursor:
            next_cursor = None
        return BackendLogPage(records=tuple(records), next_cursor=next_cursor)

    def cancel(self, plan: ExecutionPlan, handle: BackendHandle) -> None:
        """Idempotently request cancellation of one owned non-terminal batch."""
        binding = self._binding_and_handle(plan, handle)
        batch = self._try_get_batch(handle.execution_id)
        if batch is None:
            raise ExecutionBackendError("Managed Spark batch is unavailable for cancellation.")
        self._validate_observed_batch(plan, binding, batch, handle.execution_id)
        state = batch.get("state")
        if state in _TERMINAL_STATES or state == "CANCELLING":
            return
        if state not in {"PENDING", "RUNNING"}:
            raise ExecutionBackendError("Managed Spark returned an unknown batch state.")
        operation = batch.get("operation")
        if not isinstance(operation, str) or not self._operation_matches(binding, operation):
            raise ExecutionBackendError("Managed Spark batch operation is invalid.")
        try:
            self._request_json(
                "cancel batch",
                "POST",
                f"{_API_ROOT}/{operation}:cancel",
                json={},
                expected=(200,),
            )
        except _GoogleCallError as cancel_error:
            reconciled = self._try_get_batch(handle.execution_id)
            if reconciled is not None and reconciled.get("state") in (
                _TERMINAL_STATES | {"CANCELLING"}
            ):
                return
            raise ExecutionBackendError(
                "Managed Spark cancellation could not be reconciled."
            ) from cancel_error

    def close(self) -> None:
        """Close the authorized transport exactly once."""
        if self._closed:
            return
        try:
            self._transport.close()
        except Exception as error:
            raise ExecutionBackendError("Managed Spark backend shutdown failed.") from error
        self._closed = True

    def _binding_for(self, plan: ExecutionPlan) -> DataprocServerlessBinding:
        if plan.backend_id != _BACKEND_ID:
            raise ExecutionBackendError("The execution plan does not select Managed Spark.")
        binding = self._plan_bindings.get(plan.revision)
        if binding is None:
            raise ExecutionBackendError("The execution plan is not registered with Managed Spark.")
        template = plan.execution_template
        physical = plan.physical_plan
        image = _ARTIFACT_IMAGE.fullmatch(plan.image)
        if (
            physical is None
            or physical.execution_mode is not PhysicalExecutionMode.DISTRIBUTED
            or any(
                item.transport is not ExchangeTransport.OBJECT_STORE for item in physical.exchanges
            )
        ):
            raise ExecutionBackendError(
                "Managed Spark requires a distributed plan with object-store exchanges."
            )
        if (
            template.pipeline_id != binding.pipeline_id
            or plan.profile_id != binding.profile_id
            or image is None
            or image.group("project") != binding.project_id
            or image.group("region") != binding.region
            or template.workload_identity != binding.runtime_service_account
        ):
            raise ExecutionBackendError(
                "The execution plan does not match its Managed Spark binding."
            )
        if any(
            reference.provider != "gcp_secret_manager" for _, reference in template.secret_bindings
        ):
            raise ExecutionBackendError(
                "Managed Spark accepts only service-account-resolved GCP secret references."
            )
        cores = _executor_cores(plan)
        if (
            template.schedule.task_count < 2
            or template.schedule.task_count > 2_000
            or template.schedule.maximum_parallelism != template.schedule.task_count
            or physical.maximum_parallelism > template.schedule.task_count * cores
            or template.resources.ephemeral_storage_mib is not None
            or not 600 <= plan.deadline_seconds <= 14 * 24 * 60 * 60
        ):
            raise ExecutionBackendError("Managed Spark fixed resource bounds are invalid.")
        _memory_parts(plan)
        return binding

    def _binding_and_handle(
        self,
        plan: ExecutionPlan,
        handle: BackendHandle,
    ) -> DataprocServerlessBinding:
        binding = self._binding_for(plan)
        prefix = f"{binding.parent_resource}/batches/"
        batch_id = handle.execution_id.removeprefix(prefix)
        if (
            handle.backend_id != _BACKEND_ID
            or not handle.execution_id.startswith(prefix)
            or _BATCH_ID.fullmatch(batch_id) is None
        ):
            raise ExecutionBackendError(
                "The execution handle is outside its Managed Spark binding."
            )
        return binding

    def _desired_batch(
        self,
        plan: ExecutionPlan,
        binding: DataprocServerlessBinding,
        *,
        run_id: str,
        attempt_id: str,
    ) -> dict[str, object]:
        template = plan.execution_template
        batch_id = _batch_id(run_id, attempt_id)
        attempt_number = _attempt_number(attempt_id)
        heap_mib, overhead_mib = _memory_parts(plan)
        cores = _executor_cores(plan)
        execution_environment = template.bind(
            run_id=run_id,
            launcher_execution_id=binding.batch_resource(batch_id),
            attempt=attempt_number,
        ).environment()
        properties = {
            "dataproc.tier": "standard",
            "spark.dynamicAllocation.enabled": "false",
            "spark.executor.instances": str(template.schedule.task_count),
            "spark.driver.cores": str(cores),
            "spark.executor.cores": str(cores),
            "spark.driver.memory": f"{heap_mib}m",
            "spark.executor.memory": f"{heap_mib}m",
            "spark.driver.memoryOverhead": f"{overhead_mib}m",
            "spark.executor.memoryOverhead": f"{overhead_mib}m",
        }
        for name, value in execution_environment.items():
            properties[f"spark.dataproc.driverEnv.{name}"] = value
            properties[f"spark.executorEnv.{name}"] = value
        properties["spark.dataproc.driverEnv.DANDER_CONFIGURATION_REFERENCE"] = (
            template.configuration_reference
        )
        if template.secret_bindings:
            properties["spark.dataproc.driverEnv.DANDER_SECRET_BINDINGS_JSON"] = json.dumps(
                {
                    name: {"provider": reference.provider, "reference": reference.reference}
                    for name, reference in template.secret_bindings
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        return {
            "labels": {
                "dander-plan": f"p{plan.revision[:32]}",
                "dander-attempt": f"a{hashlib.sha256(attempt_id.encode()).hexdigest()[:32]}",
            },
            "pysparkBatch": {
                "mainPythonFileUri": binding.main_python_file_uri,
                "args": list(template.command),
            },
            "runtimeConfig": {
                "version": binding.runtime_version,
                "containerImage": binding.container_image_tag,
                "properties": dict(sorted(properties.items())),
            },
            "environmentConfig": {"executionConfig": _execution_config(plan, binding)},
        }

    def _try_get_batch(self, resource: str) -> Mapping[str, object] | None:
        try:
            return self._request_json(
                "read batch",
                "GET",
                f"{_API_ROOT}/{resource}",
                expected=(200,),
            )
        except _GoogleCallError as error:
            if error.status_code == 404:
                return None
            raise ExecutionBackendError("Managed Spark batch lookup failed.") from error

    def _validate_batch(
        self,
        plan: ExecutionPlan,
        binding: DataprocServerlessBinding,
        batch: Mapping[str, object],
        *,
        run_id: str,
        attempt_id: str,
    ) -> None:
        expected_resource = binding.batch_resource(_batch_id(run_id, attempt_id))
        desired = self._desired_batch(plan, binding, run_id=run_id, attempt_id=attempt_id)
        if batch.get("name") != expected_resource:
            raise ExecutionBackendError("Managed Spark returned an unexpected batch.")
        for key in ("labels", "pysparkBatch", "environmentConfig"):
            if not _mapping_contains(batch.get(key), desired[key]):
                raise ExecutionBackendError(
                    "The deployed Managed Spark batch does not match its immutable execution plan."
                )
        if not _runtime_config_contains(batch.get("runtimeConfig"), desired["runtimeConfig"]):
            raise ExecutionBackendError(
                "The deployed Managed Spark batch does not match its immutable execution plan."
            )

    def _validate_observed_batch(
        self,
        plan: ExecutionPlan,
        binding: DataprocServerlessBinding,
        batch: Mapping[str, object],
        resource: str,
    ) -> None:
        template = plan.execution_template
        heap_mib, overhead_mib = _memory_parts(plan)
        cores = _executor_cores(plan)
        expected = {
            "labels": {"dander-plan": f"p{plan.revision[:32]}"},
            "pysparkBatch": {
                "mainPythonFileUri": binding.main_python_file_uri,
                "args": list(template.command),
            },
            "environmentConfig": {"executionConfig": _execution_config(plan, binding)},
        }
        expected_runtime = {
            "version": binding.runtime_version,
            "containerImage": binding.container_image_tag,
            "properties": {
                "dataproc.tier": "standard",
                "spark.dynamicAllocation.enabled": "false",
                "spark.executor.instances": str(template.schedule.task_count),
                "spark.driver.cores": str(cores),
                "spark.executor.cores": str(cores),
                "spark.driver.memory": f"{heap_mib}m",
                "spark.executor.memory": f"{heap_mib}m",
                "spark.driver.memoryOverhead": f"{overhead_mib}m",
                "spark.executor.memoryOverhead": f"{overhead_mib}m",
            },
        }
        if (
            batch.get("name") != resource
            or any(not _mapping_contains(batch.get(key), value) for key, value in expected.items())
            or not _runtime_config_contains(batch.get("runtimeConfig"), expected_runtime)
        ):
            raise ExecutionBackendError(
                "The observed Managed Spark batch does not match its immutable execution plan."
            )

    def _validate_operation(
        self,
        binding: DataprocServerlessBinding,
        operation: Mapping[str, object],
    ) -> None:
        name = operation.get("name")
        if not isinstance(name, str) or not self._operation_matches(binding, name):
            raise ExecutionBackendError("Managed Spark returned an invalid operation.")

    @staticmethod
    def _operation_matches(binding: DataprocServerlessBinding, value: str) -> bool:
        match = _OPERATION.fullmatch(value)
        return (
            match is not None
            and match.group("project") == binding.project_id
            and match.group("region") == binding.region
        )

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
            response = self._transport.request(method, url, timeout=self._timeout, **kwargs)
        except Exception as error:
            raise _GoogleCallError(operation, None) from error
        status = getattr(response, "status_code", None)
        if not isinstance(status, int) or status not in expected:
            raise _GoogleCallError(operation, status if isinstance(status, int) else None)
        try:
            payload = response.json()
        except Exception as error:
            raise ExecutionBackendError("Managed Spark returned invalid provider JSON.") from error
        if not isinstance(payload, Mapping):
            raise ExecutionBackendError("Managed Spark returned an invalid provider response.")
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
            raise ExecutionBackendError(
                "Managed Spark backend clock must return an aware datetime."
            )
        return now.astimezone(UTC)


def _batch_id(run_id: str, attempt_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{attempt_id}".encode()).hexdigest()
    return f"dander-{digest[:40]}"


def _request_id(run_id: str, attempt_id: str) -> str:
    digest = hashlib.sha256(f"request\0{run_id}\0{attempt_id}".encode()).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))


def _attempt_number(attempt_id: str) -> int:
    match = _ATTEMPT_ID.fullmatch(attempt_id)
    if match is None:
        raise ExecutionBackendError("Managed Spark attempt identity is invalid.")
    return int(match.group(1))


def _executor_cores(plan: ExecutionPlan) -> int:
    cpu_millis = plan.execution_template.resources.cpu_millis
    if cpu_millis not in {4_000, 8_000, 16_000}:
        raise ExecutionBackendError("Managed Spark requires 4, 8, or 16 cores per executor.")
    return cpu_millis // 1_000


def _memory_parts(plan: ExecutionPlan) -> tuple[int, int]:
    memory_mib = plan.execution_template.resources.memory_mib
    cores = _executor_cores(plan)
    if not 1_024 <= memory_mib // cores <= 7_424:
        raise ExecutionBackendError("Managed Spark memory per core is outside its fixed bound.")
    heap_mib = memory_mib * 3 // 5
    overhead_mib = memory_mib - heap_mib
    return heap_mib, overhead_mib


def _execution_config(
    plan: ExecutionPlan,
    binding: DataprocServerlessBinding,
) -> dict[str, object]:
    config: dict[str, object] = {
        "serviceAccount": binding.runtime_service_account,
        "stagingBucket": binding.staging_bucket,
        "ttl": f"{plan.deadline_seconds}s",
        "authenticationConfig": {"userWorkloadAuthenticationType": "SERVICE_ACCOUNT"},
    }
    if binding.subnetwork_uri is not None:
        config["subnetworkUri"] = binding.subnetwork_uri
    return config


def _mapping_contains(actual: object, expected: object) -> bool:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(
            key in actual and _mapping_contains(actual[key], value)
            for key, value in expected.items()
        )
    return actual == expected


def _runtime_config_contains(actual: object, expected: object) -> bool:
    if not isinstance(actual, Mapping) or not isinstance(expected, Mapping):
        return False
    actual_version = actual.get("version")
    expected_version = expected.get("version")
    if not _runtime_version_matches(actual_version, expected_version):
        return False
    if actual.get("containerImage") != expected.get("containerImage"):
        return False
    properties = _normalized_provider_properties(actual.get("properties"))
    return properties is not None and _mapping_contains(properties, expected.get("properties"))


def _runtime_version_matches(actual: object, expected: object) -> bool:
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    if actual == expected:
        return True
    prefix = f"{expected}."
    suffix = actual.removeprefix(prefix)
    return (
        actual.startswith(prefix)
        and bool(suffix)
        and all(part.isdigit() for part in suffix.split("."))
    )


def _normalized_provider_properties(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        canonical = key
        for namespace in ("spark", "dataproc"):
            prefix = f"{namespace}:"
            candidate = key.removeprefix(prefix)
            if key.startswith(prefix) and candidate.startswith(f"{namespace}."):
                canonical = candidate
                break
        if canonical in normalized and normalized[canonical] != item:
            return None
        normalized[canonical] = item
    return normalized


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ExecutionBackendError("Managed Spark returned an invalid log timestamp.")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExecutionBackendError("Managed Spark returned an invalid log timestamp.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ExecutionBackendError("Managed Spark returned an invalid log timestamp.")
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


__all__ = ["DataprocServerlessExecutionBackend"]
