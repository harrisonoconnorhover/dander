"""OCI Functions entrypoint for scheduled and event-assisted lifecycle reconciliation."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from dander.providers.oci_container_instances.controller import (
    OciExecution,
    OciLifecycleController,
    OciLifecycleError,
)
from dander.providers.oci_container_instances.oci_adapter import (
    build_resource_principal_adapters,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import BinaryIO


def handler(ctx: object, data: BinaryIO | None = None) -> object:
    """Handle detached schedules/manual starts and short event/cancel operations."""
    del ctx
    try:
        payload = _payload(data)
        namespace = _required_environment("DANDER_OCI_NAMESPACE")
        bucket = _required_environment("DANDER_OCI_RUN_BUCKET")
        pipeline_id = _required_environment("DANDER_OCI_PIPELINE")
        projection_key = _required_environment("DANDER_OCI_PROJECTION_KEY")
        repository, gateway = build_resource_principal_adapters(
            namespace=namespace,
            bucket=bucket,
        )
        projection = repository.get_projection(projection_key)
        if projection.get("pipeline_id") != pipeline_id:
            raise OciLifecycleError("OCI Function projection does not match its pipeline")
        controller = OciLifecycleController(
            projection=projection,
            repository=repository,
            gateway=gateway,
        )
        action = _action(payload)
        execution: OciExecution | None
        if action == "start":
            key = payload.get("idempotency_key")
            if not isinstance(key, str):
                key = _scheduled_key(pipeline_id)
            execution = controller.start(idempotency_key=key)
            execution = _monitor(controller, execution)
        elif action == "reconcile":
            run_id = payload.get("run_id")
            execution = controller.reconcile(run_id if isinstance(run_id, str) else None)
        elif action == "cancel":
            execution = controller.cancel(_required_string(payload, "run_id"))
        elif action == "replay":
            execution = controller.replay(
                _required_string(payload, "run_id"),
                idempotency_key=_required_string(payload, "idempotency_key"),
            )
            execution = _monitor(controller, execution)
        else:  # pragma: no cover - _action is exhaustive.
            raise OciLifecycleError("Unsupported OCI controller action")
        return _response(200, None if execution is None else execution.as_dict())
    except OciLifecycleError as error:
        return _response(
            409,
            {
                "schema": "io.dander.oci-controller-error/v1",
                "failure_code": "controller_rejected",
                "message": str(error),
            },
        )


def _monitor(
    controller: OciLifecycleController,
    execution: OciExecution,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> OciExecution:
    current = execution
    while not current.terminal:
        sleep(5)
        observed = controller.reconcile(current.run_id)
        if observed is None:
            raise OciLifecycleError("OCI execution disappeared during reconciliation")
        current = observed
    return current


def _payload(data: BinaryIO | None) -> dict[str, object]:
    if data is None:
        return {}
    content = data.read(65_537)
    if len(content) > 65_536:
        raise OciLifecycleError("OCI controller request is too large")
    if not content:
        return {}
    try:
        document = json.loads(content)
    except json.JSONDecodeError as error:
        raise OciLifecycleError("OCI controller request is invalid JSON") from error
    if not isinstance(document, dict):
        raise OciLifecycleError("OCI controller request must be a JSON object")
    return document


def _action(payload: Mapping[str, object]) -> str:
    action = payload.get("action")
    if action is None and ("eventType" in payload or payload.get("cloudEventsVersion") is not None):
        return "reconcile"
    if action is None:
        return "start"
    if action not in {"start", "reconcile", "cancel", "replay"}:
        raise OciLifecycleError("OCI controller action is unsupported")
    return str(action)


def _scheduled_key(pipeline_id: str) -> str:
    # Resource Scheduler invokes functions no more often than hourly. The UTC-hour bucket makes
    # delivery retries idempotent while the active-run lock enforces maximum parallelism one.
    hour = datetime.now(UTC).strftime("%Y-%m-%dT%H")
    return f"schedule:{pipeline_id}:{hour}"


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise OciLifecycleError(f"OCI Function configuration {name} is missing")
    return value


def _required_string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise OciLifecycleError(f"OCI controller request {name} is missing")
    return value


def _response(status: int, document: object) -> object:
    body = json.dumps(document, sort_keys=True, separators=(",", ":"))
    try:
        from fdk import response  # type: ignore[import-not-found]
    except ImportError:
        return {"status": status, "body": body}
    return response.Response(
        status_code=status,
        response_data=body,
        headers={"Content-Type": "application/json"},
    )


__all__ = ["handler"]
