"""OCI Functions lifecycle dispatch contract without provider access."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import replace
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

import pytest

import dander.providers.oci_container_instances.function_handler as function_handler
from dander.providers.oci_container_instances.controller import (
    OCI_EXECUTION_SCHEMA,
    OciExecution,
    OciLifecycleError,
)

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _execution(*, state: str = "running") -> OciExecution:
    return OciExecution(
        schema=OCI_EXECUTION_SCHEMA,
        run_id="oci-" + "a" * 24,
        pipeline_id="jobs",
        idempotency_key="manual:unit",
        image="ocir.us-ashburn-1.oci.oraclecloud.com/unit/dander@sha256:" + "a" * 64,
        state=state,  # type: ignore[arg-type]
        attempt=1,
        max_attempts=2,
        created_at="2026-08-12T12:00:00Z",
        updated_at="2026-08-12T12:00:01Z",
        deadline_at="2026-08-12T12:15:00Z",
    )


class _Repository:
    def get_projection(self, key: str) -> dict[str, object]:
        assert key == "projections/jobs.json"
        return {"pipeline_id": "jobs"}


class _Controller:
    calls: list[tuple[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        assert kwargs["projection"] == {"pipeline_id": "jobs"}

    def start(self, *, idempotency_key: str) -> OciExecution:
        self.calls.append(("start", idempotency_key))
        return _execution()

    def reconcile(self, run_id: str | None = None) -> OciExecution | None:
        self.calls.append(("reconcile", run_id))
        if run_id is None:
            return None
        return replace(_execution(), state="succeeded", exit_code=0)

    def cancel(self, run_id: str) -> OciExecution:
        self.calls.append(("cancel", run_id))
        return replace(_execution(), state="cancelled", failure_code="interrupted_run")

    def replay(self, run_id: str, *, idempotency_key: str) -> OciExecution:
        self.calls.append(("replay", (run_id, idempotency_key)))
        return _execution()


@pytest.fixture(autouse=True)
def _install_function_boundary(monkeypatch: MonkeyPatch) -> None:
    _Controller.calls = []
    monkeypatch.setenv("DANDER_OCI_NAMESPACE", "unitnamespace")
    monkeypatch.setenv("DANDER_OCI_RUN_BUCKET", "unit-runs")
    monkeypatch.setenv("DANDER_OCI_PIPELINE", "jobs")
    monkeypatch.setenv("DANDER_OCI_PROJECTION_KEY", "projections/jobs.json")
    monkeypatch.setattr(
        function_handler,
        "build_resource_principal_adapters",
        lambda **_kwargs: (_Repository(), object()),
    )
    monkeypatch.setattr(function_handler, "OciLifecycleController", _Controller)
    monkeypatch.setattr(
        "dander.providers.oci_container_instances.function_handler.time.sleep",
        lambda _seconds: None,
    )


def _document(result: object) -> tuple[int, object]:
    assert isinstance(result, dict)
    return int(result["status"]), json.loads(str(result["body"]))


def test_manual_start_monitors_to_terminal_with_exact_idempotency_key() -> None:
    result = function_handler.handler(
        object(),
        io.BytesIO(b'{"action":"start","idempotency_key":"manual:unit"}'),
    )

    status, document = _document(result)
    assert status == 200
    assert isinstance(document, dict) and document["state"] == "succeeded"
    assert _Controller.calls == [
        ("start", "manual:unit"),
        ("reconcile", "oci-" + "a" * 24),
    ]


def test_empty_resource_scheduler_request_defaults_to_start() -> None:
    result = function_handler.handler(object(), io.BytesIO(b""))

    status, document = _document(result)
    assert status == 200
    assert isinstance(document, dict) and document["state"] == "succeeded"
    assert _Controller.calls[0][0] == "start"
    assert str(_Controller.calls[0][1]).startswith("schedule:jobs:")


def test_lifecycle_event_reconciles_once_without_starting_or_polling() -> None:
    result = function_handler.handler(
        object(),
        io.BytesIO(b'{"cloudEventsVersion":"0.1","eventType":"unit"}'),
    )

    status, document = _document(result)
    assert status == 200
    assert document is None
    assert _Controller.calls == [("reconcile", None)]


def test_rejected_payload_is_bounded_and_does_not_echo_input() -> None:
    result = function_handler.handler(
        object(),
        io.BytesIO(b'{"action":"customer-secret-value"}'),
    )

    status, document = _document(result)
    assert status == 409
    assert isinstance(document, dict)
    assert document["failure_code"] == "controller_rejected"
    assert "customer-secret-value" not in json.dumps(document)

    with pytest.raises(OciLifecycleError, match="too large"):
        function_handler._payload(io.BytesIO(b"x" * 65_537))


def test_fdk_response_receives_the_invocation_context(monkeypatch: MonkeyPatch) -> None:
    context = object()
    calls: list[tuple[object, dict[str, object]]] = []

    class _Response:
        def __init__(self, ctx: object, **kwargs: object) -> None:
            calls.append((ctx, kwargs))

    fdk = ModuleType("fdk")
    fdk.response = SimpleNamespace(Response=_Response)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fdk", fdk)

    result = function_handler.handler(
        context,
        io.BytesIO(b'{"action":"customer-secret-value"}'),
    )

    assert isinstance(result, _Response)
    assert calls == [
        (
            context,
            {
                "status_code": 409,
                "response_data": (
                    '{"failure_code":"controller_rejected","message":'
                    '"OCI controller action is unsupported",'
                    '"schema":"io.dander.oci-controller-error/v1"}'
                ),
                "headers": {"Content-Type": "application/json"},
            },
        )
    ]
