"""OCI lifecycle controller correctness without provider access."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from dander.providers.oci_container_instances.controller import (
    OciExecution,
    OciInstanceStatus,
    OciLifecycleController,
    OciLifecycleError,
    StoredExecution,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_IMAGE = "ocir.us-ashburn-1.oci.oraclecloud.com/unit/dander/runtime@sha256:" + "a" * 64


class _Repository:
    def __init__(self) -> None:
        self.active: OciExecution | None = None
        self.history: dict[str, OciExecution] = {}
        self.logs: list[tuple[str, bytes]] = []
        self.version = 0

    def claim(self, execution: OciExecution) -> tuple[StoredExecution, bool]:
        historical = self.history.get(execution.run_id)
        if historical is not None:
            return self._stored(historical), False
        if self.active is not None:
            return self._stored(self.active), False
        self.active = execution
        return self._stored(execution), True

    def get(self, pipeline_id: str, run_id: str | None = None) -> StoredExecution | None:
        assert pipeline_id == "jobs"
        if self.active is not None and (run_id is None or self.active.run_id == run_id):
            return self._stored(self.active)
        if run_id is not None and run_id in self.history:
            return self._stored(self.history[run_id])
        return None

    def save(self, stored: StoredExecution, execution: OciExecution) -> StoredExecution:
        assert self.active is not None
        assert stored.execution.run_id == execution.run_id == self.active.run_id
        self.active = execution
        return self._stored(execution)

    def finish(self, stored: StoredExecution, execution: OciExecution) -> StoredExecution:
        if self.active is None:
            historical = self.history.get(execution.run_id)
            assert historical == execution
            return self._stored(execution)
        result = self.save(stored, execution)
        self.history[execution.run_id] = execution
        self.active = None
        return result

    def save_logs(self, execution: OciExecution, content: bytes) -> None:
        self.logs.append((execution.run_id, content))

    def _stored(self, execution: OciExecution) -> StoredExecution:
        self.version += 1
        return StoredExecution(execution, f"v{self.version}")


class _Gateway:
    def __init__(self) -> None:
        self.created: list[tuple[str, int]] = []
        self.statuses: list[OciInstanceStatus] = []
        self.stopped: list[str] = []
        self.deleted: list[str] = []
        self.fail_create = False

    def create(self, projection: Mapping[str, object], execution: OciExecution) -> str:
        assert projection["pipeline_id"] == execution.pipeline_id
        if self.fail_create:
            raise RuntimeError("provider failure")
        instance_id = f"instance-{execution.attempt}"
        self.created.append((instance_id, execution.attempt))
        return instance_id

    def status(self, instance_id: str) -> OciInstanceStatus:
        assert instance_id.startswith("instance-")
        return self.statuses.pop(0)

    def stop(self, instance_id: str) -> None:
        self.stopped.append(instance_id)

    def delete(self, instance_id: str) -> None:
        self.deleted.append(instance_id)

    def logs(self, container_id: str, *, limit_bytes: int) -> bytes:
        assert limit_bytes == 262_144
        return f"bounded:{container_id}".encode()


def _projection(*, retries: int = 1, deadline: int = 900) -> dict[str, object]:
    return {
        "schema": "io.dander.execution/v1",
        "contract": "io.dander.runtime/v1",
        "pipeline_id": "jobs",
        "profile_id": "oci_postgresql",
        "launcher": "oci_container_instances",
        "image": _IMAGE,
        "command": ["runtime", "execute"],
        "configuration_reference": "/app/dander.yaml",
        "environment": {},
        "secret_bindings": {},
        "workload_identity": "oci-resource-principal://dynamic-group/unit",
        "resources": {
            "cpu_millis": 1000,
            "memory_mib": 2048,
            "deadline_seconds": deadline,
            "runtime_retry_count": 0,
            "launcher_retry_count": retries,
        },
        "network": {"placement": "subnet", "extensions": {}},
        "schedule": {
            "task_count": 1,
            "maximum_parallelism": 1,
            "expression": "0 * * * *",
            "time_zone": "UTC",
            "paused": True,
        },
        "labels": {},
        "observability": {
            "log_destination": "oci_logging",
            "metric_namespace": "oci_computecontainerinstance",
            "alert_target": "oci_notifications",
            "retention_days": 30,
        },
        "extensions": {},
    }


def _controller(
    *,
    repository: _Repository,
    gateway: _Gateway,
    clock: object | None = None,
    retries: int = 1,
    deadline: int = 900,
) -> OciLifecycleController:
    return OciLifecycleController(
        projection=_projection(retries=retries, deadline=deadline),
        repository=repository,
        gateway=gateway,
        clock=clock,  # type: ignore[arg-type]
    )


def test_start_is_idempotent_and_parallelism_is_one() -> None:
    repository = _Repository()
    gateway = _Gateway()
    controller = _controller(repository=repository, gateway=gateway)

    first = controller.start(idempotency_key="manual:one")
    repeated = controller.start(idempotency_key="manual:one")

    assert repeated == first
    assert gateway.created == [("instance-1", 1)]
    with pytest.raises(OciLifecycleError, match="already has active run"):
        controller.start(idempotency_key="manual:two")

    with pytest.raises(OciLifecycleError, match="unsupported characters"):
        controller.start(idempotency_key="private value with spaces")


def test_success_preserves_logs_deletes_instance_and_releases_pipeline() -> None:
    repository = _Repository()
    gateway = _Gateway()
    gateway.statuses = [OciInstanceStatus("succeeded", "container-one", 0)]
    controller = _controller(repository=repository, gateway=gateway)
    started = controller.start(idempotency_key="manual:success")

    finished = controller.reconcile(started.run_id)

    assert finished is not None and finished.state == "succeeded"
    assert gateway.deleted == ["instance-1"]
    assert repository.logs == [(started.run_id, b"bounded:container-one")]
    assert repository.active is None


def test_retryable_exit_creates_fresh_attempt_then_succeeds() -> None:
    repository = _Repository()
    gateway = _Gateway()
    gateway.statuses = [
        OciInstanceStatus("failed", "container-one", 75),
        OciInstanceStatus("succeeded", "container-two", 0),
    ]
    controller = _controller(repository=repository, gateway=gateway, retries=1)
    started = controller.start(idempotency_key="manual:retry")

    retried = controller.reconcile(started.run_id)
    finished = controller.reconcile(started.run_id)

    assert retried is not None and retried.attempt == 2 and retried.state == "running"
    assert finished is not None and finished.state == "succeeded"
    assert gateway.created == [("instance-1", 1), ("instance-2", 2)]
    assert gateway.deleted == ["instance-1", "instance-2"]


def test_retry_exhaustion_has_stable_failure_code() -> None:
    repository = _Repository()
    gateway = _Gateway()
    gateway.statuses = [OciInstanceStatus("failed", "container-one", 75)]
    controller = _controller(repository=repository, gateway=gateway, retries=0)
    started = controller.start(idempotency_key="manual:exhaust")

    finished = controller.reconcile(started.run_id)

    assert finished is not None and finished.state == "failed"
    assert finished.failure_code == "launcher_retry_exhausted"


def test_deadline_stops_and_deletes_before_failing() -> None:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    moments = iter((now, now, now + timedelta(seconds=11), now + timedelta(seconds=11)))
    repository = _Repository()
    gateway = _Gateway()
    controller = _controller(
        repository=repository,
        gateway=gateway,
        clock=lambda: next(moments),
        deadline=10,
    )
    started = controller.start(idempotency_key="manual:deadline")

    finished = controller.reconcile(started.run_id)

    assert finished is not None and finished.failure_code == "launcher_deadline_exceeded"
    assert gateway.stopped == ["instance-1"]
    assert gateway.deleted == ["instance-1"]


def test_cancel_is_terminal_and_replay_creates_a_new_run() -> None:
    repository = _Repository()
    gateway = _Gateway()
    controller = _controller(repository=repository, gateway=gateway)
    started = controller.start(idempotency_key="manual:cancel")

    cancelled = controller.cancel(started.run_id)
    replayed = controller.replay(started.run_id, idempotency_key="replay:one")

    assert cancelled.state == "cancelled"
    assert cancelled.failure_code == "interrupted_run"
    assert replayed.replay_of == started.run_id
    assert replayed.run_id != started.run_id
    assert gateway.stopped == ["instance-1"]
    assert gateway.deleted == ["instance-1"]


def test_create_failure_is_recorded_and_raised() -> None:
    repository = _Repository()
    gateway = _Gateway()
    gateway.fail_create = True
    controller = _controller(repository=repository, gateway=gateway)

    with pytest.raises(OciLifecycleError, match="creation failed"):
        controller.start(idempotency_key="manual:create-failure")

    assert len(repository.history) == 1
    assert next(iter(repository.history.values())).failure_code == "launcher_create_failed"


@pytest.mark.parametrize("deadline", [0, 3301])
def test_controller_rejects_deadlines_it_cannot_own(deadline: int) -> None:
    with pytest.raises(OciLifecycleError, match="1-3300"):
        _controller(repository=_Repository(), gateway=_Gateway(), deadline=deadline)


def test_controller_rejects_unknown_projection_fields_and_secret_value_overlap() -> None:
    projection = _projection()
    projection["unknown"] = "must-not-be-retained"
    with pytest.raises(OciLifecycleError, match="incomplete"):
        OciLifecycleController(
            projection=projection,
            repository=_Repository(),
            gateway=_Gateway(),
        )

    projection = _projection()
    projection["environment"] = {"TOKEN": "plain"}
    projection["secret_bindings"] = {
        "TOKEN": {
            "provider": "oci_vault",
            "reference": "oci-vault://ocid1.vault.oc1.iad.unit/secrets/token",
        }
    }
    with pytest.raises(OciLifecycleError, match="secret bindings"):
        OciLifecycleController(
            projection=projection,
            repository=_Repository(),
            gateway=_Gateway(),
        )
