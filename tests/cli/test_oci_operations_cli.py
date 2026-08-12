"""Thin CLI coverage for manifest-bound OCI Container Instances operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import dander.cli.oci_command as oci_command
from dander.cli.main import app
from dander.providers.oci_container_instances import (
    OCI_EXECUTION_SCHEMA,
    OciExecution,
    OciInvocation,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch

_FUNCTION = "ocid1.fnfunc.oc1.iad." + "f" * 32
_RUN_ID = "oci-" + "a" * 24


def _execution() -> OciExecution:
    return OciExecution(
        schema=OCI_EXECUTION_SCHEMA,
        run_id=_RUN_ID,
        pipeline_id="warehouse_fixture",
        idempotency_key="unit",
        image="iad.ocir.io/unit/dander/runtime@sha256:" + "a" * 64,
        state="succeeded",
        attempt=1,
        max_attempts=2,
        created_at="2026-08-12T12:00:00Z",
        updated_at="2026-08-12T12:01:00Z",
        deadline_at="2026-08-12T12:15:00Z",
        exit_code=0,
    )


class _Operations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.execution = _execution()

    def start(self) -> OciInvocation:
        self.calls.append(("start", None))
        return OciInvocation("start", _RUN_ID)

    def latest(self) -> OciExecution:
        self.calls.append(("latest", None))
        return self.execution

    def describe(self, run_id: str) -> OciExecution:
        self.calls.append(("describe", run_id))
        return self.execution

    def logs(self, run_id: str, *, attempt: int | None) -> bytes:
        self.calls.append(("logs", (run_id, attempt)))
        return b"runtime complete\n"

    def cancel(self, run_id: str) -> OciExecution:
        self.calls.append(("cancel", run_id))
        return self.execution

    def replay(self, run_id: str) -> OciInvocation:
        self.calls.append(("replay", run_id))
        return OciInvocation("replay", _RUN_ID)


def _install_fake(monkeypatch: MonkeyPatch) -> tuple[_Operations, dict[str, object]]:
    operations = _Operations()
    binding: dict[str, object] = {}

    def factory(**kwargs: object) -> _Operations:
        binding.update(kwargs)
        return operations

    monkeypatch.setattr(oci_command, "_oci_operations", factory)
    return operations, binding


def _base_args(tmp_path: Path) -> list[str]:
    return [
        "--deployment",
        "oci_postgres",
        "--pipeline",
        "warehouse_fixture",
        "--function-id",
        _FUNCTION,
        "--config",
        str(tmp_path / "dander.yaml"),
        "--oci-profile",
        "DANDER",
    ]


def test_status_and_logs_bind_to_the_selected_manifest_pipeline(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    operations, binding = _install_fake(monkeypatch)

    status = CliRunner().invoke(
        app,
        ["oci", "status", *_base_args(tmp_path), "--run-id", _RUN_ID],
    )
    logs = CliRunner().invoke(
        app,
        ["oci", "logs", *_base_args(tmp_path), "--run-id", _RUN_ID, "--attempt", "1"],
    )

    assert status.exit_code == 0, status.output
    assert logs.exit_code == 0, logs.output
    assert '"state": "succeeded"' in status.output
    assert logs.output == "runtime complete\n"
    assert operations.calls == [
        ("describe", _RUN_ID),
        ("logs", (_RUN_ID, 1)),
    ]
    assert binding == {
        "config": tmp_path / "dander.yaml",
        "deployment": "oci_postgres",
        "pipeline": "warehouse_fixture",
        "function_id": _FUNCTION,
        "oci_profile": "DANDER",
        "name": "dander",
    }


@pytest.mark.parametrize(
    ("command", "extra", "expected"),
    [
        ("run", [], ("start", None)),
        ("cancel", ["--run-id", _RUN_ID], ("cancel", _RUN_ID)),
        ("replay", ["--run-id", _RUN_ID], ("replay", _RUN_ID)),
    ],
)
def test_mutating_operations_require_confirmation_and_invoke_one_action(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    command: str,
    extra: list[str],
    expected: tuple[str, object],
) -> None:
    operations, _binding = _install_fake(monkeypatch)

    refused = CliRunner().invoke(
        app,
        ["oci", command, *_base_args(tmp_path), *extra],
        input="n\n",
    )
    accepted = CliRunner().invoke(
        app,
        ["oci", command, *_base_args(tmp_path), *extra],
        input="y\n",
    )

    assert refused.exit_code == 1
    assert accepted.exit_code == 0, accepted.output
    assert operations.calls == [expected]
