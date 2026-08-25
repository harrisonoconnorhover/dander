"""Hosted Control API contract, isolation, and multi-graph behavior."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest
from fastapi.testclient import TestClient

from dander.control import GraphRecord, InMemoryGraphStore, RootedLocalGraphStore
from dander.control.application import (
    ControlApplication,
    RunAddress,
    RunLifecyclePort,
    RunSubmissionResolver,
)
from dander.control.http import create_control_app, decode_revision_etag, encode_revision_etag
from dander.control.models import (
    LogPageResponse,
    MutationResult,
    RunPageResponse,
    RunState,
    RunStatusResponse,
)
from dander.control.orchestration import RunSubmission, RunTrigger, TriggerKind

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from httpx import Response

GRAPH = {"name": "hosted_graph", "nodes": [], "edges": []}
GRAPH_TWO = {"name": "hosted_graph_two", "nodes": [], "edges": []}
CREATE_HEADERS = {"Idempotency-Key": "create-key-0001"}


@pytest.fixture(params=["memory", "local"])
def client(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    store = (
        InMemoryGraphStore()
        if request.param == "memory"
        else RootedLocalGraphStore(tmp_path / "graphs")
    )
    return TestClient(create_control_app(ControlApplication(store, projects=("demo-project",))))


def _create(
    client: TestClient,
    graph: str,
    document: object = GRAPH,
    *,
    key: str = "create-key-0001",
) -> Response:
    return cast(
        "Response",
        client.post(
            "/v1/projects/demo-project/graphs",
            json={"graph": graph, "document": document},
            headers={"Idempotency-Key": key},
        ),
    )


def test_multigraph_crud_pagination_and_conditional_mutations(client: TestClient) -> None:
    with client:
        assert client.get("/v1/projects").json() == {"projects": [{"id": "demo-project"}]}
        first = _create(client, "alpha-graph")
        second = _create(client, "beta-graph", GRAPH_TWO, key="create-key-0002")
        assert first.status_code == 201
        assert second.status_code == 201
        assert decode_revision_etag(first.headers["etag"])
        assert "revision" not in first.json()

        page = client.get("/v1/projects/demo-project/graphs?limit=1")
        assert page.status_code == 200
        assert [item["graph"] for item in page.json()["items"]] == ["alpha-graph"]
        assert "document" not in page.json()["items"][0]
        next_page = client.get(
            "/v1/projects/demo-project/graphs",
            params={"limit": 1, "cursor": page.json()["next_cursor"]},
        )
        assert [item["graph"] for item in next_page.json()["items"]] == ["beta-graph"]

        updated = client.put(
            "/v1/projects/demo-project/graphs/alpha-graph",
            json=GRAPH_TWO,
            headers={"If-Match": first.headers["etag"]},
        )
        assert updated.status_code == 200
        assert updated.headers["etag"] != first.headers["etag"]
        stale = client.put(
            "/v1/projects/demo-project/graphs/alpha-graph",
            json=GRAPH,
            headers={"If-Match": first.headers["etag"]},
        )
        assert stale.status_code == 409

        deleted = client.delete(
            "/v1/projects/demo-project/graphs/alpha-graph",
            headers={
                "If-Match": updated.headers["etag"],
                "Idempotency-Key": "delete-key-0001",
            },
        )
        replay = client.delete(
            "/v1/projects/demo-project/graphs/alpha-graph",
            headers={
                "If-Match": updated.headers["etag"],
                "Idempotency-Key": "delete-key-0001",
            },
        )
        assert deleted.status_code == replay.status_code == 204


def test_create_replay_etag_validation_and_correlation(client: TestClient) -> None:
    with client:
        created = _create(client, "alpha-graph")
        replay = _create(client, "alpha-graph")
        assert replay.status_code == 201
        assert replay.json() == created.json()
        assert replay.headers["etag"] == created.headers["etag"]

        validated = client.post(
            "/v1/projects/demo-project/graphs/alpha-graph/validate",
            headers={"If-Match": created.headers["etag"], "X-Correlation-ID": "corr-123"},
        )
        assert validated.status_code == 200
        assert validated.json()["valid"] is True
        assert validated.headers["x-correlation-id"] == "corr-123"

        malformed = client.post(
            "/v1/projects/demo-project/graphs/alpha-graph/validate",
            headers={"If-Match": 'W/"weak"'},
        )
        assert malformed.status_code == 400
        assert malformed.json()["error"]["code"] == "invalid_revision"
        assert "input" not in malformed.text.casefold()


def test_mutation_audit_metadata_excludes_bodies_and_idempotency_keys(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with client, caplog.at_level(logging.INFO, logger="dander.control.audit"):
        response = _create(client, "alpha-graph", key="do-not-log-this-key")

    assert response.status_code == 201
    event = next(record for record in caplog.records if record.message == "control_mutation")
    assert event.http_method == "POST"  # type: ignore[attr-defined]
    assert event.route_template == "/v1/projects/{project}/graphs"  # type: ignore[attr-defined]
    assert event.status_code == 201  # type: ignore[attr-defined]
    assert "do-not-log-this-key" not in caplog.text
    assert "hosted_graph" not in caplog.text


def test_capabilities_are_honest_and_unwired_operations_fail_closed(client: TestClient) -> None:
    with client:
        capabilities = client.get("/v1/capabilities")
        assert capabilities.status_code == 200
        assert set(capabilities.json()["operations"]) == {
            "graph.read",
            "graph.edit",
            "graph.delete",
            "graph.validate",
        }
        created = _create(client, "alpha-graph")
        preview = client.post(
            "/v1/projects/demo-project/graphs/alpha-graph/deployment-preview",
            headers={"If-Match": created.headers["etag"]},
        )
        assert preview.status_code == 501
        assert preview.json()["error"]["code"] == "operation_unavailable"


def test_graph_body_is_bounded_before_json_validation(client: TestClient) -> None:
    with client:
        response = client.post(
            "/v1/projects/demo-project/graphs",
            content=b"{}",
            headers={
                **CREATE_HEADERS,
                "Content-Type": "application/json",
                "Content-Length": str(5 * 1024 * 1024 + 2049),
            },
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "graph_too_large"


def test_run_start_rejects_a_body_before_operation_dispatch(client: TestClient) -> None:
    with client:
        _create(client, "alpha-graph")
        response = client.post(
            "/v1/projects/demo-project/graphs/alpha-graph/runs",
            content=b'{"ignored":"payload"}',
            headers={"If-Match": '"bm90LXJlYWQ"', "Idempotency-Key": "start-key-0001"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "request_invalid"


def test_graph_delete_rejects_a_body_before_mutation(client: TestClient) -> None:
    with client:
        created = _create(client, "alpha-graph")
        response = client.request(
            "DELETE",
            "/v1/projects/demo-project/graphs/alpha-graph",
            content=b'{"ignored":"payload"}',
            headers={
                "If-Match": created.headers["etag"],
                "Idempotency-Key": "delete-key-0001",
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "request_invalid"
        assert client.get("/v1/projects/demo-project/graphs/alpha-graph").status_code == 200


def test_etag_round_trips_arbitrary_opaque_revision_without_header_injection() -> None:
    revision = 'provider/"native\nrevision?=yes'
    etag = encode_revision_etag(revision)
    assert "\n" not in etag
    assert decode_revision_etag(etag) == revision

    for invalid in ('W/"abc"', "*", '"*"', '"one", "two"', '"bad padding="'):
        with pytest.raises(ValueError):
            decode_revision_etag(invalid)


@dataclass
class _Lifecycle:
    closed: bool = False
    starts: list[RunSubmission] = field(default_factory=list)
    mutations: list[tuple[str, str]] = field(default_factory=list)

    def start(self, submission: RunSubmission) -> RunStatusResponse:
        self.starts.append(submission)
        return _status("run-one")

    def get(self, address: RunAddress) -> RunStatusResponse:
        return _status(address.run_id)

    def list(self, *, cursor: str | None, limit: int) -> RunPageResponse:
        assert cursor is None
        assert limit == 50
        return RunPageResponse(items=(_status("run-one"),), next_cursor=None)

    def logs(self, address: RunAddress, *, cursor: str | None, limit: int) -> LogPageResponse:
        assert cursor is None
        assert limit == 25
        return LogPageResponse(records=(), next_cursor=None)

    def cancel(self, address: RunAddress, *, idempotency_key: str) -> MutationResult:
        self.mutations.append(("cancel", idempotency_key))
        return MutationResult(
            operation="cancel",
            accepted=True,
            run_id=address.run_id,
            state=RunState.CANCELING,
        )

    def replay(self, address: RunAddress, *, idempotency_key: str) -> MutationResult:
        self.mutations.append(("replay", idempotency_key))
        return MutationResult(
            operation="replay",
            accepted=True,
            run_id=address.run_id,
            resulting_run_id="run-two",
            state=RunState.QUEUED,
        )

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class _SubmissionResolver:
    environment: str = "production"
    plan_id: str = "default-plan"
    plan_revision: str = "a" * 64

    def resolve(
        self,
        record: GraphRecord,
        *,
        idempotency_key: str,
        requested_at: datetime,
    ) -> RunSubmission:
        return RunSubmission(
            environment=self.environment,
            project=record.project,
            graph=record,
            plan_id=self.plan_id,
            plan_revision=self.plan_revision,
            trigger=RunTrigger(kind=TriggerKind.API, trigger_id="control-api"),
            idempotency_key=idempotency_key,
            requested_at=requested_at,
        )


def _status(run_id: str) -> RunStatusResponse:
    return RunStatusResponse(run_id=run_id, state=RunState.QUEUED)


def test_normalized_lifecycle_receives_decoded_revision_and_explicit_idempotency() -> None:
    lifecycle = _Lifecycle()
    store = InMemoryGraphStore(revision_factory=lambda: 'native/"revision')
    application = ControlApplication(
        store,
        lifecycle=cast("RunLifecyclePort", lifecycle),
        submission_resolver=cast("RunSubmissionResolver", _SubmissionResolver()),
        projects=("demo-project",),
    )
    client = TestClient(create_control_app(application))
    with client:
        created = _create(client, "alpha-graph")
        start = client.post(
            "/v1/projects/demo-project/graphs/alpha-graph/runs",
            headers={
                "If-Match": created.headers["etag"],
                "Idempotency-Key": "start-key-0001",
            },
        )
        assert start.status_code == 202
        assert len(lifecycle.starts) == 1
        assert lifecycle.starts[0].graph.revision == 'native/"revision'
        assert lifecycle.starts[0].environment == "production"
        assert lifecycle.starts[0].idempotency_key == "start-key-0001"
        requested_offset = lifecycle.starts[0].requested_at.utcoffset()
        assert requested_offset is not None
        assert requested_offset.total_seconds() == 0
        assert client.get("/v1/runs").json()["items"][0]["run_id"] == "run-one"
        assert client.get("/v1/runs/run-one/logs?limit=25").status_code == 200
        assert (
            client.post(
                "/v1/runs/run-one/cancel",
                headers={"Idempotency-Key": "cancel-key-0001"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/v1/runs/run-one/replay",
                headers={"Idempotency-Key": "replay-key-0001"},
            ).status_code
            == 200
        )
        assert lifecycle.mutations == [
            ("cancel", "cancel-key-0001"),
            ("replay", "replay-key-0001"),
        ]
    assert lifecycle.closed is True


@pytest.mark.parametrize("operation", ["cancel", "replay"])
def test_run_mutations_reject_a_body_before_operation_dispatch(operation: str) -> None:
    lifecycle = _Lifecycle()
    application = ControlApplication(
        InMemoryGraphStore(),
        lifecycle=cast("RunLifecyclePort", lifecycle),
        submission_resolver=cast("RunSubmissionResolver", _SubmissionResolver()),
    )
    client = TestClient(create_control_app(application))

    with client:
        response = client.post(
            f"/v1/runs/run-one/{operation}",
            content=b'{"ignored":"payload"}',
            headers={"Idempotency-Key": f"{operation}-key-0001"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "request_invalid"
        assert lifecycle.mutations == []


def test_health_readiness_and_generic_failure_never_echo_exception_text() -> None:
    def broken_readiness() -> bool:
        raise RuntimeError("secret-value-must-not-escape")

    client = TestClient(
        create_control_app(ControlApplication(InMemoryGraphStore(), readiness=broken_readiness))
    )
    with client:
        assert client.get("/healthz").json() == {"status": "ok"}
        failed = client.get("/readyz")
        assert failed.status_code == 500
        assert failed.json()["error"]["code"] == "internal_error"
        assert "secret-value" not in failed.text
