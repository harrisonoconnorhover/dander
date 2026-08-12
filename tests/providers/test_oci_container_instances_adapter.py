"""OCI Object Storage lifecycle repository correctness without provider access."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dander.providers.oci_container_instances.controller import (
    OCI_EXECUTION_SCHEMA,
    OciExecution,
    OciLifecycleError,
    execution_run_id,
)
from dander.providers.oci_container_instances.oci_adapter import OciObjectRunRepository


class _ServiceError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"provider-{status}")
        self.status = status


class _ObjectClient:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.version = 0
        self.delete_failure: int | None = None

    def put_object(
        self,
        namespace: str,
        bucket: str,
        key: str,
        content: bytes,
        **kwargs: object,
    ) -> object:
        assert namespace == "unitnamespace"
        assert bucket == "unit-runs"
        existing = self.objects.get(key)
        if kwargs.get("if_none_match") == "*" and existing is not None:
            raise _ServiceError(412)
        if_match = kwargs.get("if_match")
        if if_match is not None and (existing is None or existing[1] != if_match):
            raise _ServiceError(412)
        self.version += 1
        etag = f"etag-{self.version}"
        self.objects[key] = (bytes(content), etag)
        return SimpleNamespace(headers={"etag": etag})

    def get_object(self, namespace: str, bucket: str, key: str) -> object:
        assert namespace == "unitnamespace"
        assert bucket == "unit-runs"
        try:
            content, etag = self.objects[key]
        except KeyError as error:
            raise _ServiceError(404) from error
        return SimpleNamespace(data=SimpleNamespace(content=content), headers={"etag": etag})

    def delete_object(
        self,
        namespace: str,
        bucket: str,
        key: str,
        **kwargs: object,
    ) -> None:
        assert namespace == "unitnamespace"
        assert bucket == "unit-runs"
        if self.delete_failure is not None:
            status = self.delete_failure
            self.delete_failure = None
            raise _ServiceError(status)
        existing = self.objects.get(key)
        if existing is None:
            raise _ServiceError(404)
        if kwargs.get("if_match") != existing[1]:
            raise _ServiceError(412)
        del self.objects[key]


def _execution(key: str = "manual:unit") -> OciExecution:
    return OciExecution(
        schema=OCI_EXECUTION_SCHEMA,
        run_id=execution_run_id("jobs", key),
        pipeline_id="jobs",
        idempotency_key=key,
        image="ocir.us-ashburn-1.oci.oraclecloud.com/unit/dander@sha256:" + "a" * 64,
        state="pending",
        attempt=1,
        max_attempts=2,
        created_at="2026-08-12T12:00:00Z",
        updated_at="2026-08-12T12:00:00Z",
        deadline_at="2026-08-12T12:15:00Z",
    )


def _repository(client: _ObjectClient) -> OciObjectRunRepository:
    return OciObjectRunRepository(
        client=client,
        namespace="unitnamespace",
        bucket="unit-runs",
    )


def test_claim_compare_and_swap_finish_and_history_are_idempotent() -> None:
    client = _ObjectClient()
    repository = _repository(client)
    execution = _execution()

    stored, claimed = repository.claim(execution)
    repeated, repeated_claim = repository.claim(execution)
    running = replace(execution, state="running", updated_at="2026-08-12T12:00:01Z")
    stored = repository.save(stored, running)
    terminal = replace(
        running,
        state="succeeded",
        exit_code=0,
        updated_at="2026-08-12T12:01:00Z",
    )
    finished = repository.finish(stored, terminal)

    assert claimed is True
    assert repeated_claim is False
    assert repeated.execution == execution
    assert repository.get("jobs") is None
    assert repository.get("jobs", execution.run_id) == finished
    assert repository.finish(finished, terminal) == finished


def test_finish_recovers_after_history_was_written_but_active_release_failed() -> None:
    client = _ObjectClient()
    repository = _repository(client)
    execution = _execution("manual:recovery")
    stored, _ = repository.claim(execution)
    terminal = replace(
        execution,
        state="failed",
        failure_code="runtime_failed",
        exit_code=1,
        updated_at="2026-08-12T12:01:00Z",
    )
    client.delete_failure = 500

    with pytest.raises(OciLifecycleError, match="release"):
        repository.finish(stored, terminal)

    partial = repository.get("jobs", terminal.run_id)
    assert partial is not None and partial.execution == terminal
    recovered = repository.finish(partial, terminal)
    assert recovered.execution == terminal
    assert repository.get("jobs") is None


def test_logs_are_tail_bounded_and_projection_reads_are_size_and_shape_checked() -> None:
    client = _ObjectClient()
    repository = _repository(client)
    execution = _execution()
    repository.save_logs(execution, b"a" * 300_000)

    assert repository.get_logs(execution) == b"a" * 262_144

    projection = {"schema": "io.dander.execution/v1", "pipeline_id": "jobs"}
    client.put_object(
        "unitnamespace",
        "unit-runs",
        "projections/jobs.json",
        json.dumps(projection).encode(),
    )
    assert repository.get_projection("projections/jobs.json") == projection
    with pytest.raises(OciLifecycleError, match="key"):
        repository.get_projection("history/jobs.json")


def test_corrupt_execution_records_fail_closed_without_echoing_content() -> None:
    client = _ObjectClient()
    repository = _repository(client)
    document = _execution().as_dict()
    document["state"] = "customer-secret-content"
    client.put_object(
        "unitnamespace",
        "unit-runs",
        "active/jobs.json",
        json.dumps(document).encode(),
    )

    with pytest.raises(OciLifecycleError, match="unsupported contract") as raised:
        repository.get("jobs")

    assert "customer-secret-content" not in str(raised.value)
