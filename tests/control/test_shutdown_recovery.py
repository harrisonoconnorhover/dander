"""Retry shutdown after slow Control workers finish, without closing their dependencies early."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from dander.control.application import ControlOperationDependencyError, RunAddress
from dander.control.orchestration import (
    ExecutionBackend,
    ExecutionBackendError,
    RunOutcome,
    RunStore,
    RunStoreError,
    StoredRunPage,
)
from dander.control.postgresql_scheduler import (
    PostgreSQLScheduler,
    PostgreSQLSchedulerLeader,
    PostgreSQLScheduleSubmissionSource,
)
from dander.control.run_composition import ControlRunComposition, ControlRunCompositionError
from dander.control.run_lifecycle import (
    ControlRunLifecycle,
    ExecutionBackendRegistry,
    ExecutionPlanRegistry,
)
from dander.control.schedule_consumer import ControlScheduleConsumer, ScheduledRunSubmissionResolver
from dander.control.startup_factory import ControlStartup
from tests.control import test_run_lifecycle as runs
from tests.control import test_schedule_consumer as schedules

if TYPE_CHECKING:
    from concurrent.futures import Future

    from dander.control.graph_store import GraphRecord
    from dander.control.postgresql_control_database import PostgreSQLControlDatabase
    from dander.control.postgresql_schedule_queue import PostgreSQLScheduleQueue
    from dander.control.schedule_consumer import QueuedScheduleMessage


@dataclass
class Pool:
    close_count: int = 0

    def close(self) -> None:
        self.close_count += 1


@pytest.mark.parametrize("blocked_worker", ["reconciler", "consumer", "producer"])
def test_shutdown_timeout_retains_dependencies_and_retries_after_worker_stops(
    blocked_worker: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()
    graph_store, graph = runs._graph_store()
    plan = runs._plan(graph)
    backend = runs._Backend()
    store = runs._Store()
    lifecycle = ControlRunLifecycle(
        cast("RunStore", store),
        ExecutionPlanRegistry((plan,)),
        ExecutionBackendRegistry({"fargate": cast("ExecutionBackend", backend)}),
        graph_store,
        shutdown_grace_seconds=0.01,
    )
    queue = schedules._Queue(())
    queue_closed = Pool()
    monkeypatch.setattr(queue, "close", queue_closed.close)
    resolver = ScheduledRunSubmissionResolver(
        ExecutionPlanRegistry((plan,)), graph_store, (schedules._spec(plan),)
    )
    consumer = ControlScheduleConsumer(queue, resolver, lifecycle, shutdown_grace_seconds=0.01)
    producer: PostgreSQLScheduler | None = None
    leader_closed = Pool()

    if blocked_worker == "reconciler":

        def blocked_list(*, cursor: str | None, limit: int) -> StoredRunPage:
            entered.set()
            assert release.wait(5)
            return StoredRunPage(items=(), next_cursor=None)

        # The lifecycle binds its recovery query once when it is constructed.
        monkeypatch.setattr(lifecycle, "_list_recovery", blocked_list)
    else:
        if blocked_worker == "consumer":

            def blocked_receive() -> tuple[QueuedScheduleMessage, ...]:
                entered.set()
                assert release.wait(5)
                return ()

            monkeypatch.setattr(queue, "receive", blocked_receive)
            lifecycle.install_submission_source(consumer)
        else:
            from tests.control.test_postgresql_scheduler import _trigger

            producer = PostgreSQLScheduler(
                cast("PostgreSQLControlDatabase", object()),
                cast("PostgreSQLScheduleQueue", queue),
                (_trigger(),),
                shutdown_grace_seconds=0.01,
            )

            def blocked_tick() -> int:
                entered.set()
                assert release.wait(5)
                return 0

            monkeypatch.setattr(producer, "tick_once", blocked_tick)
            monkeypatch.setattr(
                producer, "_leader", cast("PostgreSQLSchedulerLeader", leader_closed)
            )
            lifecycle.install_submission_source(
                PostgreSQLScheduleSubmissionSource(producer, consumer)
            )
    pool = Pool()
    startup = ControlStartup(
        graph_store,
        cast("ControlRunComposition", SimpleNamespace(lifecycle=lifecycle)),
        pool,
    )
    lifecycle.start_reconciler()
    try:
        assert entered.wait(2)
        with pytest.raises(ControlRunCompositionError, match="could not close"):
            startup.close()
        assert not lifecycle.ready()
        assert (
            pool.close_count,
            store.close_count,
            backend.close_count,
            queue_closed.close_count,
        ) == (0, 0, 0, 0)
        with pytest.raises(ControlOperationDependencyError, match="closed"):
            lifecycle.start(runs._submission(graph, plan))
        release.set()
        worker = (
            lifecycle
            if blocked_worker == "reconciler"
            else consumer
            if producer is None
            else producer
        )
        assert worker._thread is not None  # noqa: SLF001
        worker._thread.join(2)  # noqa: SLF001
        startup.close()
        startup.close()
        assert (pool.close_count, store.close_count, backend.close_count) == (1, 1, 1)
        assert queue_closed.close_count == (0 if blocked_worker == "reconciler" else 1)
        assert leader_closed.close_count == (1 if blocked_worker == "producer" else 0)
    finally:
        release.set()
        if lifecycle._thread is not None:  # noqa: SLF001
            lifecycle._thread.join(2)  # noqa: SLF001
        startup.close()


@pytest.mark.parametrize("failing_resource", ["backend", "store", "pool"])
def test_cleanup_failure_retries_only_resources_not_already_closed(
    failing_resource: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph_store, graph = runs._graph_store()
    backend = runs._Backend()
    store = runs._Store()
    lifecycle = runs._lifecycle(graph_store, runs._plan(graph), store, backend)
    pool = Pool()
    resources: dict[str, runs._Backend | runs._Store | Pool] = {
        "backend": backend,
        "store": store,
        "pool": pool,
    }
    resource = resources[failing_resource]
    original_close = resource.close
    attempts = 0

    def failing_close() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error_type = {
                "backend": ExecutionBackendError,
                "store": RunStoreError,
                "pool": RuntimeError,
            }[failing_resource]
            raise error_type("sensitive provider detail")
        original_close()

    monkeypatch.setattr(resource, "close", failing_close)
    startup = ControlStartup(
        graph_store,
        cast("ControlRunComposition", SimpleNamespace(lifecycle=lifecycle)),
        pool,
    )
    with pytest.raises(ControlRunCompositionError, match="could not close") as raised:
        startup.close()
    assert "sensitive" not in str(raised.value)
    assert pool.close_count == 0
    startup.close()
    startup.close()
    assert attempts == 2
    assert (backend.close_count, store.close_count, pool.close_count) == (1, 1, 1)


@pytest.mark.parametrize("operation", ["start", "replay"])
def test_shutdown_waits_for_an_active_mutation_before_closing_dependencies(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph_store, graph = runs._graph_store()
    plan = runs._plan(graph)
    backend = runs._Backend()
    store = runs._Store()
    lifecycle = ControlRunLifecycle(
        cast("RunStore", store),
        ExecutionPlanRegistry((plan,)),
        ExecutionBackendRegistry({"fargate": cast("ExecutionBackend", backend)}),
        graph_store,
        clock=lambda: runs.NOW,
        shutdown_grace_seconds=0.01,
    )
    pool = Pool()
    startup = ControlStartup(
        graph_store,
        cast("ControlRunComposition", SimpleNamespace(lifecycle=lifecycle)),
        pool,
    )
    entered, release = threading.Event(), threading.Event()
    future: Future[object]
    with ThreadPoolExecutor(1) as executor:
        if operation == "start":
            backend.submit_entered = entered
            backend.submit_release = release
            future = executor.submit(lifecycle.start, runs._submission(graph, plan))
        else:
            started = lifecycle.start(runs._submission(graph, plan))
            handle = next(iter(backend.effects.values()))
            backend.observations[handle.execution_id] = runs._terminal(RunOutcome.FAILED)
            lifecycle.reconcile_once()
            original_get = graph_store.get

            def blocked_get(project: str, graph_id: str) -> GraphRecord:
                entered.set()
                assert release.wait(5)
                assert store.close_count == 0 and pool.close_count == 0
                return original_get(project, graph_id)

            monkeypatch.setattr(graph_store, "get", blocked_get)
            future = executor.submit(
                lifecycle.replay, RunAddress(started.run_id), idempotency_key="replay-key-shutdown"
            )
        try:
            assert entered.wait(1)
            with pytest.raises(ControlRunCompositionError, match="could not close"):
                startup.close()
            assert (pool.close_count, backend.close_count, store.close_count) == (0, 0, 0)
            release.set()
            if operation == "replay":
                with pytest.raises(ControlOperationDependencyError, match="closed"):
                    future.result(timeout=2)
            else:
                future.result(timeout=2)
            startup.close()
            startup.close()
            assert (pool.close_count, backend.close_count, store.close_count) == (1, 1, 1)
            assert len(backend.effects) == 1
        finally:
            release.set()
            startup.close()
