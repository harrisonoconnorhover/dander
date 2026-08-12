"""Manifest-bound OCI lifecycle operation contract tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

import pytest

import dander.providers.oci_container_instances.operations as operations_module
from dander.providers.oci_container_instances import (
    OCI_EXECUTION_SCHEMA,
    OciContainerInstanceOperations,
    OciExecution,
    OciInvocation,
    OciOperationBinding,
    OciOperationError,
)
from dander.providers.oci_container_instances.controller import StoredExecution

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from dander.providers.oci_container_instances.oci_adapter import OciObjectRunRepository

_COMPARTMENT = "ocid1.compartment.oc1.." + "b" * 32
_FUNCTION = "ocid1.fnfunc.oc1.iad." + "f" * 32
_RUN_ID = "oci-" + "a" * 24


def _execution(
    *,
    state: Literal["pending", "running", "succeeded", "failed", "cancelled"] = "succeeded",
    run_id: str = _RUN_ID,
) -> OciExecution:
    return OciExecution(
        schema=OCI_EXECUTION_SCHEMA,
        run_id=run_id,
        pipeline_id="warehouse_fixture",
        idempotency_key="unit",
        image="iad.ocir.io/unit/dander/runtime@sha256:" + "a" * 64,
        state=state,
        attempt=1,
        max_attempts=2,
        created_at="2026-08-12T12:00:00Z",
        updated_at="2026-08-12T12:01:00Z",
        deadline_at="2026-08-12T12:15:00Z",
        instance_id="ocid1.computecontainerinstance.oc1.iad." + "c" * 32,
        container_id="ocid1.computecontainer.oc1.iad." + "d" * 32,
        exit_code=0,
    )


class _Repository:
    def __init__(self, execution: OciExecution | None = None) -> None:
        self.execution = execution

    def get(
        self,
        pipeline_id: str,
        run_id: str | None = None,
    ) -> StoredExecution | None:
        assert pipeline_id == "warehouse_fixture"
        if self.execution is None or (run_id is not None and run_id != self.execution.run_id):
            return None
        return StoredExecution(self.execution, "etag-unit")

    def get_logs(self, execution: OciExecution, *, attempt: int | None = None) -> bytes:
        assert execution == self.execution
        assert attempt in {None, 1}
        return b"runtime complete\n"


class _Invoker:
    def __init__(self, response: bytes = b"") -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object], bool]] = []

    def invoke(self, function_id: str, payload: dict[str, object], *, detached: bool) -> bytes:
        self.calls.append((function_id, payload, detached))
        return self.response


def _binding() -> OciOperationBinding:
    return OciOperationBinding(
        region="us-ashburn-1",
        namespace="unitnamespace",
        bucket="dander-oci-runs-unit",
        pipeline_id="warehouse_fixture",
        function_id=_FUNCTION,
        project_dir=Path("/tmp/dander-oci-operations-test"),
    )


def _operations(
    repository: _Repository,
    invoker: _Invoker,
) -> OciContainerInstanceOperations:
    return OciContainerInstanceOperations(
        _binding(),
        repository=cast("OciObjectRunRepository", repository),
        invoker=invoker,
        clock=lambda: datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        nonce=lambda: "nonceunit123",
    )


def test_binding_uses_manifest_launcher_and_exact_terraform_bucket_formula(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = SimpleNamespace(
        launcher_provider="oci_container_instances",
        pipelines={"warehouse_fixture": object()},
        validate_references=lambda _path: None,
        resolved_launcher_config=lambda: {
            "registry_namespace": "unitnamespace",
            "compartment_id": _COMPARTMENT,
            "region": "us-ashburn-1",
        },
    )
    monkeypatch.setattr(
        operations_module,
        "load_project_config",
        lambda *_args, **_kwargs: manifest,
    )

    binding = OciOperationBinding.from_project(
        config=tmp_path / "dander.yaml",
        deployment="oci_postgres",
        pipeline_id="warehouse_fixture",
        function_id=_FUNCTION,
    )

    suffix = hashlib.sha256(_COMPARTMENT.encode()).hexdigest()[:8]
    assert binding.bucket == f"dander-oci-runs-{suffix}"
    assert binding.function_id == _FUNCTION
    assert binding.project_dir == tmp_path


def test_detached_start_returns_the_same_deterministic_run_identity_as_controller() -> None:
    invoker = _Invoker()
    operation = _operations(_Repository(), invoker)

    result = operation.start()

    assert isinstance(result, OciInvocation)
    assert result.action == "start"
    assert result.run_id.startswith("oci-")
    assert invoker.calls == [
        (
            _FUNCTION,
            {
                "action": "start",
                "idempotency_key": ("manual:warehouse_fixture:20260812T120000Z:nonceunit123"),
            },
            True,
        )
    ]


def test_status_and_logs_are_read_from_the_exact_manifest_bound_repository() -> None:
    execution = _execution()
    operation = _operations(_Repository(execution), _Invoker())

    assert operation.latest() == execution
    assert operation.describe(_RUN_ID) == execution
    assert operation.logs(_RUN_ID, attempt=1) == b"runtime complete\n"


def test_cancel_parses_only_the_sanitized_controller_execution_contract() -> None:
    execution = _execution(state="cancelled")
    invoker = _Invoker(json.dumps(execution.as_dict()).encode())

    cancelled = _operations(_Repository(execution), invoker).cancel(_RUN_ID)

    assert cancelled == execution
    assert invoker.calls == [(_FUNCTION, {"action": "cancel", "run_id": _RUN_ID}, False)]


def test_replay_requires_terminal_history_and_returns_a_new_caller_known_run_id() -> None:
    invoker = _Invoker()
    operation = _operations(_Repository(_execution()), invoker)

    replay = operation.replay(_RUN_ID)

    assert replay.action == "replay"
    assert replay.run_id != _RUN_ID
    assert invoker.calls[0][1] == {
        "action": "replay",
        "run_id": _RUN_ID,
        "idempotency_key": "replay:warehouse_fixture:20260812T120000Z:nonceunit123",
    }
    assert invoker.calls[0][2] is True


def test_operation_failures_do_not_echo_invalid_provider_responses() -> None:
    operation = _operations(_Repository(_execution()), _Invoker(b"not-json-secret"))

    with pytest.raises(OciOperationError, match="invalid response") as raised:
        operation.cancel(_RUN_ID)

    assert "not-json-secret" not in str(raised.value)
