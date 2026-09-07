"""Blocking provider work must leave the hosted API event loop responsive."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, cast

import pytest
from fastapi.testclient import TestClient

from dander.control import InMemoryGraphStore, RootedLocalGraphStore
from dander.control.application import ControlApplication
from dander.control.http import create_control_app
from dander.control.models import PipelineGraphDocument

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from httpx import Response


@pytest.mark.parametrize("operation", ["get", "create", "ready"])
def test_blocked_provider_call_does_not_stall_unrelated_requests(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryGraphStore()
    store.create(
        "demo",
        "existing",
        PipelineGraphDocument(name="existing", nodes=(), edges=()),
        idempotency_key="seed-graph-0001",
    )
    application = ControlApplication(store, projects=("demo",))
    entered = threading.Event()
    release = threading.Event()
    target = application if operation == "ready" else store
    original = cast("Callable[..., object]", getattr(target, operation))

    def blocked(*args: object, **kwargs: object) -> object:
        entered.set()
        assert release.wait(timeout=5), "test did not release the blocked provider call"
        return original(*args, **kwargs)

    monkeypatch.setattr(target, operation, blocked)
    with TestClient(create_control_app(application)) as client, ThreadPoolExecutor(3) as pool:
        if operation == "create":
            slow = pool.submit(
                client.post,
                "/v1/projects/demo/graphs",
                json={"graph": "new", "document": {"name": "new", "nodes": [], "edges": []}},
                headers={"Idempotency-Key": "create-graph-0001"},
            )
        else:
            slow = pool.submit(
                client.get,
                "/readyz" if operation == "ready" else "/v1/projects/demo/graphs/existing",
            )
        try:
            assert entered.wait(timeout=2)
            health = pool.submit(client.get, "/healthz")
            projects = pool.submit(client.get, "/v1/projects")
            assert health.result(timeout=1).status_code == 200
            assert projects.result(timeout=1).status_code == 200
            assert not slow.done()
        finally:
            release.set()
        assert slow.result(timeout=2).status_code == (201 if operation == "create" else 200)


@pytest.mark.parametrize("store_kind", ["memory", "local"])
def test_parallel_mutations_preserve_idempotency_and_revision_conflicts(
    store_kind: str, tmp_path: Path
) -> None:
    store = (
        InMemoryGraphStore()
        if store_kind == "memory"
        else RootedLocalGraphStore(tmp_path / "graphs")
    )
    application = ControlApplication(store, projects=("demo",))
    with TestClient(create_control_app(application)) as client, ThreadPoolExecutor(4) as pool:
        created = [
            pool.submit(
                client.post,
                "/v1/projects/demo/graphs",
                json={"graph": "shared", "document": {"name": "shared", "nodes": [], "edges": []}},
                headers={"Idempotency-Key": "shared-create-0001"},
            )
            for _ in range(4)
        ]
        responses = [future.result(timeout=2) for future in created]
        assert {response.status_code for response in responses} == {201}
        assert len({response.headers["etag"] for response in responses}) == 1
        revision = responses[0].headers["etag"]
        rendezvous = threading.Barrier(2)

        def update(name: str) -> Response:
            rendezvous.wait(timeout=2)
            return cast(
                "Response",
                client.put(
                    "/v1/projects/demo/graphs/shared",
                    json={"name": name, "nodes": [], "edges": []},
                    headers={"If-Match": revision},
                ),
            )

        updates = [pool.submit(update, name) for name in ("first", "second")]
        assert sorted(future.result(timeout=2).status_code for future in updates) == [200, 409]
        assert len(store.list("demo").items) == 1
