"""OCI-specific native-condition, pagination, and restart tests."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

import pytest

from dander.control import (
    MAX_GRAPH_PAGE_SIZE,
    GraphStoreConflictError,
    GraphStoreCorruptionError,
    GraphStoreError,
    GraphStoreNotFoundError,
    OCIObjectGraphStore,
)
from dander.control.bundle import PACKAGED_BUNDLE_DIRECTORY
from dander.control.models import PipelineGraphDocument
from dander.pipeline.graph import graph_to_payload
from tests.control.oci_object_storage_fakes import (
    FakeListData,
    FakeObjectSummary,
    FakeOCIBackend,
    FakeOCIObjectStorageClient,
    FakeOCIServiceError,
    FakeResponse,
)


def _document(name: str = "oci_graph") -> PipelineGraphDocument:
    payload = json.loads(
        (PACKAGED_BUNDLE_DIRECTORY / "fixtures/pipeline-graph.json").read_text(encoding="utf-8")
    )
    payload["name"] = name
    return PipelineGraphDocument.model_validate(payload)


def _store(backend: FakeOCIBackend | None = None) -> OCIObjectGraphStore:
    return OCIObjectGraphStore(
        "unit-namespace",
        "unit-bucket",
        client=FakeOCIObjectStorageClient(backend),
    )


def test_oci_module_import_does_not_import_oci_sdk() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import dander.control.oci_object_graph_store; "
                "assert 'oci' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("namespace", "bucket"),
    (("bad/namespace", "unit-bucket"), ("unit-namespace", "bad/bucket")),
)
def test_oci_rejects_unsafe_bindings(namespace: str, bucket: str) -> None:
    with pytest.raises(GraphStoreCorruptionError, match="binding"):
        OCIObjectGraphStore(namespace, bucket, client=FakeOCIObjectStorageClient())


def test_oci_accepts_the_documented_bucket_name_boundary() -> None:
    store = OCIObjectGraphStore(
        "unit-namespace",
        "_" + ("a" * 255),
        client=FakeOCIObjectStorageClient(),
    )

    assert len(store.bucket_name) == 256


@pytest.mark.parametrize("timeout", (True, 0, float("nan"), float("inf"), "30"))
def test_oci_rejects_invalid_timeouts(timeout: object) -> None:
    with pytest.raises(GraphStoreCorruptionError, match="timeout"):
        OCIObjectGraphStore(
            "unit-namespace",
            "unit-bucket",
            client=FakeOCIObjectStorageClient(),
            timeout_seconds=timeout,  # type: ignore[arg-type]
        )


def test_oci_uses_exact_etag_conditions_and_never_addresses_old_versions() -> None:
    backend = FakeOCIBackend()
    store = _store(backend)
    created = store.create(
        "default",
        "native",
        _document(),
        idempotency_key="create-native-0001",
    )
    changed_payload = graph_to_payload(created.document.to_domain())
    changed_payload["name"] = "changed"
    updated = store.put(
        "default",
        "native",
        PipelineGraphDocument.model_validate(changed_payload),
        expected_revision=created.revision,
    )
    store.delete(
        "default",
        "native",
        expected_revision=updated.revision,
        idempotency_key="delete-native-0001",
    )

    graph_puts = [
        call for call in backend.put_calls if "/graphs/native.json" in str(call["object_name"])
    ]
    graph_deletes = [
        call for call in backend.delete_calls if "/graphs/native.json" in str(call["object_name"])
    ]
    assert graph_puts[0]["if_none_match"] == "*"
    assert graph_puts[1]["if_match"] == created.revision
    assert graph_puts[2]["if_match"] == updated.revision
    assert graph_deletes == [
        {
            "namespace_name": "unit-namespace",
            "bucket_name": "unit-bucket",
            "object_name": graph_puts[2]["object_name"],
            "if_match": backend.delete_calls[-1]["if_match"],
        }
    ]
    graph_name = str(graph_puts[2]["object_name"])
    assert graph_deletes[0]["if_match"] == backend.versions[graph_name][-2].etag
    assert all("version_id" not in call for call in backend.delete_calls)


def test_oci_limit_one_pagination_is_exclusive_and_body_free() -> None:
    backend = FakeOCIBackend()
    store = _store(backend)
    expected = ["alpha", "bravo", "charlie", "delta"]
    for index, graph in enumerate(expected):
        store.create(
            "default",
            graph,
            _document(graph),
            idempotency_key=f"create-page-{index:04d}",
        )
    backend.get_calls.clear()
    backend.head_calls.clear()
    backend.list_calls.clear()
    backend.page_size_override = 1

    actual: list[str] = []
    cursor = None
    while True:
        page = store.list("default", cursor=cursor, limit=1)
        actual.extend(item.graph for item in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert actual == expected
    assert len(actual) == len(set(actual))
    assert all(call["limit"] == 3 for call in backend.list_calls)
    assert any("start_after" in call for call in backend.list_calls)
    assert any("start" in call for call in backend.list_calls)
    assert all(not ({"start", "start_after"} <= call.keys()) for call in backend.list_calls)
    assert backend.get_calls == []
    assert backend.head_calls
    assert len(backend.list_calls) <= len(expected) * 3


class _NonAdvancingListClient(FakeOCIObjectStorageClient):
    def list_objects(
        self,
        namespace_name: str,
        bucket_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        response = super().list_objects(namespace_name, bucket_name, **kwargs)
        page = response.data
        assert isinstance(page, FakeListData) and page.objects
        return FakeResponse(
            headers=response.headers,
            data=FakeListData(
                objects=page.objects,
                next_start_with=page.objects[-1].name,
            ),
        )


def test_oci_rejects_a_nonadvancing_provider_page() -> None:
    backend = FakeOCIBackend()
    setup = _store(backend)
    setup.create(
        "default",
        "first",
        _document(),
        idempotency_key="create-first-page-0001",
    )
    setup.create(
        "default",
        "second",
        _document(),
        idempotency_key="create-second-page-0001",
    )
    backend.page_size_override = 1
    store = OCIObjectGraphStore(
        "unit-namespace",
        "unit-bucket",
        client=_NonAdvancingListClient(backend),
    )

    with pytest.raises(GraphStoreCorruptionError, match="did not advance"):
        store.list("default", limit=2)


class _EndlessListClient(FakeOCIObjectStorageClient):
    def __init__(self) -> None:
        super().__init__()
        self.page_calls = 0

    def list_objects(
        self,
        namespace_name: str,
        bucket_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        self.page_calls += 1
        prefix = kwargs["prefix"]
        assert isinstance(prefix, str)
        name = f"{prefix}graph_{self.page_calls:04d}.json"
        next_name = f"{prefix}graph_{self.page_calls + 1:04d}.json"
        return FakeResponse(
            headers={"opc-request-id": "fake"},
            data=FakeListData(
                objects=[FakeObjectSummary(name=name)],
                next_start_with=next_name,
            ),
        )


def test_oci_bounds_provider_calls_when_every_candidate_disappears() -> None:
    client = _EndlessListClient()
    store = OCIObjectGraphStore("unit-namespace", "unit-bucket", client=client)

    with pytest.raises(GraphStoreConflictError, match="did not converge"):
        store.list("default")

    assert client.page_calls == MAX_GRAPH_PAGE_SIZE + 2


def test_oci_concurrent_identical_creates_converge_on_one_exact_result() -> None:
    backend = FakeOCIBackend()
    start = threading.Barrier(2)
    journal_create = threading.Barrier(2)
    graph_create = threading.Barrier(2)
    journal_complete = threading.Barrier(2)

    def synchronize(
        name: str,
        if_none_match: str | None,
        if_match: str | None,
        data: bytes,
    ) -> None:
        payload = json.loads(data)
        if "/idempotency/default/create/" in name and if_none_match == "*":
            journal_create.wait(timeout=5)
        elif name.endswith("/graphs/concurrent.json") and if_none_match == "*":
            graph_create.wait(timeout=5)
        elif (
            "/idempotency/default/create/" in name
            and if_match is not None
            and payload.get("status") == "completed"
        ):
            journal_complete.wait(timeout=5)

    backend.before_put = synchronize
    stores = (_store(backend), _store(backend))

    def create(store: OCIObjectGraphStore) -> object:
        start.wait(timeout=5)
        return store.create(
            "default",
            "concurrent",
            _document(),
            idempotency_key="create-concurrent-0001",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(create, stores))

    assert results[0] == results[1]
    assert _store(backend).get("default", "concurrent") == results[0]


def test_oci_concurrent_identical_deletes_converge_on_one_exact_receipt() -> None:
    backend = FakeOCIBackend()
    setup = _store(backend)
    created = setup.create(
        "default",
        "concurrent_delete",
        _document(),
        idempotency_key="create-for-concurrent-delete-0001",
    )
    start = threading.Barrier(2)
    journal_create = threading.Barrier(2)
    graph_fence = threading.Barrier(2)
    journal_fence = threading.Barrier(2)

    def synchronize(
        name: str,
        if_none_match: str | None,
        if_match: str | None,
        data: bytes,
    ) -> None:
        payload = json.loads(data)
        if "/idempotency/default/delete/" in name and if_none_match == "*":
            journal_create.wait(timeout=5)
        elif name.endswith("/graphs/concurrent_delete.json") and payload.get("delete_fence"):
            graph_fence.wait(timeout=5)
        elif (
            "/idempotency/default/delete/" in name
            and if_match is not None
            and payload.get("fence_revision") is not None
            and payload.get("status") == "pending"
        ):
            journal_fence.wait(timeout=5)

    backend.before_put = synchronize
    stores = (_store(backend), _store(backend))

    def delete(store: OCIObjectGraphStore) -> object:
        start.wait(timeout=5)
        return store.delete(
            "default",
            "concurrent_delete",
            expected_revision=created.revision,
            idempotency_key="delete-concurrent-0001",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(executor.map(delete, stores))

    assert receipts[0] == receipts[1]
    with pytest.raises(GraphStoreNotFoundError):
        _store(backend).get("default", "concurrent_delete")


def test_oci_reads_are_etag_pinned_bounded_and_closed() -> None:
    backend = FakeOCIBackend()
    store = _store(backend)
    store.create(
        "default",
        "bounded",
        _document(),
        idempotency_key="create-bounded-0001",
    )
    backend.get_calls.clear()
    backend.bodies.clear()

    store.get("default", "bounded")

    assert backend.get_calls
    assert all("if_match" in call for call in backend.get_calls)
    assert all(str(call["range"]).startswith("bytes=0-") for call in backend.get_calls)
    assert backend.bodies and all(body.closed for body in backend.bodies)
    assert all(body.read_amounts[0] is not None for body in backend.bodies)

    graph_name = next(name for name in backend.objects if name.endswith("/graphs/bounded.json"))
    backend.objects[graph_name].data += b"x" * (6 * 1024 * 1024)
    get_count = len(backend.get_calls)
    with pytest.raises(GraphStoreCorruptionError, match="exceeds"):
        store.get("default", "bounded")
    assert len(backend.get_calls) == get_count


class _MalformedGetHeadersClient(FakeOCIObjectStorageClient):
    def __init__(self, backend: FakeOCIBackend) -> None:
        super().__init__(backend)
        self.malformed = False

    def get_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        response = super().get_object(namespace_name, bucket_name, object_name, **kwargs)
        if not self.malformed:
            return response
        return FakeResponse(headers={"etag": ""}, data=response.data)


def test_oci_malformed_get_response_still_closes_its_stream() -> None:
    backend = FakeOCIBackend()
    client = _MalformedGetHeadersClient(backend)
    store = OCIObjectGraphStore("unit-namespace", "unit-bucket", client=client)
    store.create(
        "default",
        "malformed",
        _document(),
        idempotency_key="create-malformed-0001",
    )
    backend.bodies.clear()
    client.malformed = True

    with pytest.raises(GraphStoreCorruptionError, match="ETag"):
        store.get("default", "malformed")

    assert backend.bodies and all(body.closed for body in backend.bodies)


def test_oci_no_etag_match_write_error_is_a_sanitized_conflict() -> None:
    backend = FakeOCIBackend()
    store = _store(backend)
    created = store.create(
        "default",
        "write_error",
        _document(),
        idempotency_key="create-write-error-0001",
    )
    backend.next_put_error = FakeOCIServiceError(412, "NoEtagMatch")

    with pytest.raises(GraphStoreConflictError) as captured:
        store.put(
            "default",
            "write_error",
            _document("changed"),
            expected_revision=created.revision,
        )

    assert "fake detail" not in str(captured.value)


def test_oci_object_disappearance_during_update_is_a_conflict() -> None:
    backend = FakeOCIBackend()
    store = _store(backend)
    created = store.create(
        "default",
        "disappeared",
        _document(),
        idempotency_key="create-disappeared-0001",
    )
    backend.next_put_error = FakeOCIServiceError(404, "NotAuthorizedOrNotFound")

    with pytest.raises(GraphStoreConflictError):
        store.put(
            "default",
            "disappeared",
            _document("changed"),
            expected_revision=created.revision,
        )


def test_oci_list_404_is_not_misclassified_as_an_empty_page() -> None:
    backend = FakeOCIBackend()
    backend.next_list_error = FakeOCIServiceError(404, "NotAuthorizedOrNotFound")

    with pytest.raises(GraphStoreError) as captured:
        _store(backend).list("default")

    assert type(captured.value) is GraphStoreError
    assert "fake detail" not in str(captured.value)


@pytest.mark.parametrize(
    ("status", "code"),
    ((404, "NotAuthorizedOrNotFound"), (412, "NoEtagMatch")),
)
def test_oci_conditional_read_race_retries_instead_of_reporting_absence(
    status: int,
    code: str,
) -> None:
    backend = FakeOCIBackend()
    store = _store(backend)
    expected = store.create(
        "default",
        "read_race",
        _document(),
        idempotency_key="create-read-race-0001",
    )
    backend.next_get_error = FakeOCIServiceError(status, code)

    assert store.get("default", "read_race") == expected


class _FailingHeadClient(FakeOCIObjectStorageClient):
    def head_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        raise FakeOCIServiceError(404, "BucketNotFound")


def test_oci_missing_bucket_is_not_misclassified_as_missing_graph() -> None:
    store = OCIObjectGraphStore(
        "unit-namespace",
        "unit-bucket",
        client=_FailingHeadClient(),
    )

    with pytest.raises(GraphStoreError) as captured:
        store.get("default", "missing_graph")

    assert type(captured.value) is GraphStoreError
    assert "fake detail" not in str(captured.value)


class _FailingDeleteClient(FakeOCIObjectStorageClient):
    def __init__(self, backend: FakeOCIBackend, code: str, status: int) -> None:
        super().__init__(backend)
        self._error = FakeOCIServiceError(status, code)

    def delete_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        raise self._error


@pytest.mark.parametrize(
    ("code", "status"),
    (("NotAuthorizedOrNotFound", 404), ("NoEtagMatch", 412)),
)
def test_oci_conditional_delete_errors_are_conflicts(code: str, status: int) -> None:
    backend = FakeOCIBackend()
    created = _store(backend).create(
        "default",
        "delete_error",
        _document(),
        idempotency_key="create-delete-error-0001",
    )
    store = OCIObjectGraphStore(
        "unit-namespace",
        "unit-bucket",
        client=_FailingDeleteClient(backend, code, status),
    )

    with pytest.raises(GraphStoreConflictError) as captured:
        store.delete(
            "default",
            "delete_error",
            expected_revision=created.revision,
            idempotency_key=f"delete-error-{status}",
        )

    assert "fake detail" not in str(captured.value)


def test_oci_unexpected_provider_errors_are_sanitized() -> None:
    backend = FakeOCIBackend()
    store = _store(backend)
    store.create(
        "default",
        "provider_error",
        _document(),
        idempotency_key="create-provider-error-0001",
    )
    backend.next_get_error = FakeOCIServiceError(500, "InternalError")

    with pytest.raises(GraphStoreError) as captured:
        store.get("default", "provider_error")

    assert "fake detail" not in str(captured.value)


class _SimulatedCrashError(RuntimeError):
    """Abrupt stop after a durable fake-OCI boundary."""


class _InterruptingOCIObjectGraphStore(OCIObjectGraphStore):
    def arm(self, fail_at: str) -> None:
        self._fail_at = fail_at
        self._failed = False

    def _checkpoint(self, stage: str) -> None:
        if stage == getattr(self, "_fail_at", None) and not self._failed:
            self._failed = True
            raise _SimulatedCrashError(stage)


def test_oci_listing_resolves_a_fenced_graph_without_exposing_it() -> None:
    backend = FakeOCIBackend()
    setup = _store(backend)
    hidden = setup.create(
        "default",
        "hidden",
        _document(),
        idempotency_key="create-hidden-0001",
    )
    visible = setup.create(
        "default",
        "visible",
        _document("visible"),
        idempotency_key="create-visible-0001",
    )
    interrupted = _InterruptingOCIObjectGraphStore(
        "unit-namespace",
        "unit-bucket",
        client=FakeOCIObjectStorageClient(backend),
    )
    interrupted.arm("after_delete_fence")
    with pytest.raises(_SimulatedCrashError, match="after_delete_fence"):
        interrupted.delete(
            "default",
            "hidden",
            expected_revision=hidden.revision,
            idempotency_key="delete-hidden-0001",
        )

    page = _store(backend).list("default")

    assert page.items == (visible.summary(),)
    assert page.next_cursor is None


@pytest.mark.parametrize("later_action", ("put", "delete"))
def test_oci_crashed_create_replays_exact_original_after_later_mutation(
    later_action: str,
) -> None:
    backend = FakeOCIBackend()
    interrupted = _InterruptingOCIObjectGraphStore(
        "unit-namespace",
        "unit-bucket",
        client=FakeOCIObjectStorageClient(backend),
    )
    interrupted.arm("after_graph_create")
    original = _document("original")
    with pytest.raises(_SimulatedCrashError, match="after_graph_create"):
        interrupted.create(
            "default",
            "recover_create",
            original,
            idempotency_key="create-recovery-0001",
        )

    restarted = _store(backend)
    first = restarted.get("default", "recover_create")
    if later_action == "put":
        changed = graph_to_payload(first.document.to_domain())
        changed["name"] = "later-update"
        restarted.put(
            "default",
            "recover_create",
            PipelineGraphDocument.model_validate(changed),
            expected_revision=first.revision,
        )
    else:
        restarted.delete(
            "default",
            "recover_create",
            expected_revision=first.revision,
            idempotency_key="delete-after-crash-0001",
        )

    replay = restarted.create(
        "default",
        "recover_create",
        original,
        idempotency_key="create-recovery-0001",
    )
    assert replay == first


@pytest.mark.parametrize(
    "fail_at",
    ("after_delete_fence", "after_graph_delete", "after_delete_completed"),
)
def test_oci_delete_recovers_each_durable_boundary(fail_at: str) -> None:
    backend = FakeOCIBackend()
    setup = _store(backend)
    created = setup.create(
        "default",
        "recover_delete",
        _document(),
        idempotency_key="create-for-delete-0001",
    )
    interrupted = _InterruptingOCIObjectGraphStore(
        "unit-namespace",
        "unit-bucket",
        client=FakeOCIObjectStorageClient(backend),
    )
    interrupted.arm(fail_at)
    with pytest.raises(_SimulatedCrashError, match=fail_at):
        interrupted.delete(
            "default",
            "recover_delete",
            expected_revision=created.revision,
            idempotency_key="delete-recovery-0001",
        )

    restarted = _store(backend)
    receipt = restarted.delete(
        "default",
        "recover_delete",
        expected_revision=created.revision,
        idempotency_key="delete-recovery-0001",
    )
    assert receipt.revision == created.revision
    with pytest.raises(GraphStoreNotFoundError):
        restarted.get("default", "recover_delete")


def test_oci_pending_delete_replay_does_not_remove_a_later_recreation() -> None:
    backend = FakeOCIBackend()
    setup = _store(backend)
    created = setup.create(
        "default",
        "recreated",
        _document("original"),
        idempotency_key="create-before-delete-0001",
    )
    interrupted = _InterruptingOCIObjectGraphStore(
        "unit-namespace",
        "unit-bucket",
        client=FakeOCIObjectStorageClient(backend),
    )
    interrupted.arm("after_graph_delete")
    with pytest.raises(_SimulatedCrashError, match="after_graph_delete"):
        interrupted.delete(
            "default",
            "recreated",
            expected_revision=created.revision,
            idempotency_key="delete-before-recreate-0001",
        )

    restarted = _store(backend)
    replacement = restarted.create(
        "default",
        "recreated",
        _document("replacement"),
        idempotency_key="create-after-delete-0001",
    )
    replay = restarted.delete(
        "default",
        "recreated",
        expected_revision=created.revision,
        idempotency_key="delete-before-recreate-0001",
    )

    assert replay.revision == created.revision
    assert restarted.get("default", "recreated") == replacement


def test_oci_object_names_and_journals_do_not_persist_raw_idempotency_keys() -> None:
    backend = FakeOCIBackend()
    store = _store(backend)
    raw_key = "create-private-key-0001"
    store.create("default", "private", _document(), idempotency_key=raw_key)

    assert all(raw_key not in name for name in backend.objects)
    assert all(raw_key.encode() not in item.data for item in backend.objects.values())


def test_oci_versioned_delete_uses_a_marker_and_preserves_older_versions() -> None:
    backend = FakeOCIBackend()
    store = _store(backend)
    created = store.create(
        "default",
        "versioned",
        _document(),
        idempotency_key="create-versioned-0001",
    )
    store.delete(
        "default",
        "versioned",
        expected_revision=created.revision,
        idempotency_key="delete-versioned-0001",
    )

    graph_name = next(name for name in backend.versions if name.endswith("/graphs/versioned.json"))
    versions = backend.versions[graph_name]
    assert versions[-1].deleted is True
    assert any(not version.deleted for version in versions[:-1])
    assert all("version_id" not in call for call in backend.delete_calls)


def test_oci_default_client_uses_only_resource_principal_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = object()
    captured: dict[str, object] = {}

    def build_client(config: object, **kwargs: object) -> FakeOCIObjectStorageClient:
        captured.update(config=config, **kwargs)
        return FakeOCIObjectStorageClient()

    oci_module = ModuleType("oci")
    oci_module.object_storage = SimpleNamespace(ObjectStorageClient=build_client)  # type: ignore[attr-defined]
    auth_module = ModuleType("oci.auth")
    signers_module = ModuleType("oci.auth.signers")
    signers_module.get_resource_principals_signer = lambda: signer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "oci", oci_module)
    monkeypatch.setitem(sys.modules, "oci.auth", auth_module)
    monkeypatch.setitem(sys.modules, "oci.auth.signers", signers_module)

    store = OCIObjectGraphStore("unit-namespace", "unit-bucket", timeout_seconds=7)

    assert store.namespace == "unit-namespace"
    assert store.bucket_name == "unit-bucket"
    assert captured == {"config": {}, "signer": signer, "timeout": (7.0, 7.0)}
