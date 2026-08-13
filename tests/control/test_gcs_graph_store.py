"""GCS-specific native-condition, pagination, and restart tests."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from dander.control import (
    GCSGraphStore,
    GraphStoreCorruptionError,
    GraphStoreNotFoundError,
)
from dander.control.bundle import PACKAGED_BUNDLE_DIRECTORY
from dander.control.models import PipelineGraphDocument
from dander.pipeline.graph import graph_to_payload
from tests.control.gcs_fakes import (
    FakeGCSBackend,
    FakeGCSClient,
    FakeNotFoundError,
    FakePreconditionError,
)


def _document(name: str = "gcs_graph") -> PipelineGraphDocument:
    payload = json.loads(
        (PACKAGED_BUNDLE_DIRECTORY / "fixtures/pipeline-graph.json").read_text(encoding="utf-8")
    )
    payload["name"] = name
    return PipelineGraphDocument.model_validate(payload)


def _store(
    backend: FakeGCSBackend | None = None,
) -> GCSGraphStore:
    return GCSGraphStore(
        "unit-bucket",
        client=FakeGCSClient(backend),
        not_found_errors=(FakeNotFoundError,),
        precondition_errors=(FakePreconditionError,),
    )


def test_gcs_module_import_does_not_import_google_sdk() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import dander.control.gcs_graph_store; "
                "assert 'google.cloud.storage' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_gcs_uses_generation_zero_create_and_matching_mutation_conditions() -> None:
    backend = FakeGCSBackend()
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

    graph_uploads = [call for call in backend.upload_calls if "/graphs/native.json" in call[0]]
    graph_deletes = [call for call in backend.delete_calls if "/graphs/native.json" in call[0]]
    assert graph_uploads[0][1] == 0
    assert graph_uploads[1][1] == int(created.revision)
    assert graph_uploads[2][1] == int(updated.revision)
    assert graph_deletes == [(graph_uploads[2][0], backend.delete_calls[-1][1])]
    assert backend.delete_calls[-1][1] > int(updated.revision)


def test_gcs_limit_one_pagination_is_exclusive_without_gaps() -> None:
    backend = FakeGCSBackend()
    store = _store(backend)
    expected = ["alpha", "bravo", "charlie", "delta"]
    for index, graph in enumerate(expected):
        store.create(
            "default",
            graph,
            _document(graph),
            idempotency_key=f"create-page-{index:04d}",
        )
    backend.download_calls.clear()

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
    assert all(max_results == 3 for _, _, max_results in backend.list_calls)
    assert any(start_offset is not None for _, start_offset, _ in backend.list_calls)
    assert backend.download_calls == []


def test_gcs_concurrent_identical_creates_converge_on_one_exact_result() -> None:
    backend = FakeGCSBackend()
    start = threading.Barrier(2)
    journal_create = threading.Barrier(2)
    graph_create = threading.Barrier(2)
    journal_complete = threading.Barrier(2)

    def synchronize(name: str, generation: int, data: bytes) -> None:
        payload = json.loads(data)
        if "/idempotency/default/create/" in name and generation == 0:
            journal_create.wait(timeout=5)
        elif name.endswith("/graphs/concurrent.json") and generation == 0:
            graph_create.wait(timeout=5)
        elif "/idempotency/default/create/" in name and payload.get("status") == "completed":
            journal_complete.wait(timeout=5)

    backend.before_upload = synchronize
    stores = (_store(backend), _store(backend))

    def create(store: GCSGraphStore) -> object:
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


def test_gcs_concurrent_identical_deletes_converge_on_one_exact_receipt() -> None:
    backend = FakeGCSBackend()
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

    def synchronize(name: str, generation: int, data: bytes) -> None:
        payload = json.loads(data)
        if "/idempotency/default/delete/" in name and generation == 0:
            journal_create.wait(timeout=5)
        elif name.endswith("/graphs/concurrent_delete.json") and payload.get("delete_fence"):
            graph_fence.wait(timeout=5)
        elif (
            "/idempotency/default/delete/" in name
            and payload.get("fence_generation") is not None
            and payload.get("status") == "pending"
        ):
            journal_fence.wait(timeout=5)

    backend.before_upload = synchronize
    stores = (_store(backend), _store(backend))

    def delete(store: GCSGraphStore) -> object:
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


def test_gcs_reads_are_generation_pinned_and_byte_bounded() -> None:
    backend = FakeGCSBackend()
    store = _store(backend)
    store.create(
        "default",
        "bounded",
        _document(),
        idempotency_key="create-bounded-0001",
    )
    backend.download_calls.clear()

    store.get("default", "bounded")

    assert backend.download_calls
    assert all(generation is not None for _, generation, _, _ in backend.download_calls)
    assert all(start == 0 and end is not None for _, _, start, end in backend.download_calls)

    graph_name = next(name for name in backend.objects if name.endswith("/graphs/bounded.json"))
    backend.objects[graph_name].data += b"x" * (6 * 1024 * 1024)
    with pytest.raises(GraphStoreCorruptionError, match="exceeds"):
        store.get("default", "bounded")


class _SimulatedCrashError(RuntimeError):
    """Abrupt stop after a durable fake-GCS boundary."""


class _InterruptingGCSGraphStore(GCSGraphStore):
    def arm(self, fail_at: str) -> None:
        self._fail_at = fail_at
        self._failed = False

    def _checkpoint(self, stage: str) -> None:
        if stage == getattr(self, "_fail_at", None) and not self._failed:
            self._failed = True
            raise _SimulatedCrashError(stage)


@pytest.mark.parametrize("later_action", ("put", "delete"))
def test_gcs_crashed_create_replays_exact_original_after_later_mutation(
    later_action: str,
) -> None:
    backend = FakeGCSBackend()
    interrupted = _InterruptingGCSGraphStore(
        "unit-bucket",
        client=FakeGCSClient(backend),
        not_found_errors=(FakeNotFoundError,),
        precondition_errors=(FakePreconditionError,),
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
def test_gcs_delete_recovers_each_durable_boundary(fail_at: str) -> None:
    backend = FakeGCSBackend()
    setup = _store(backend)
    created = setup.create(
        "default",
        "recover_delete",
        _document(),
        idempotency_key="create-for-delete-0001",
    )
    interrupted = _InterruptingGCSGraphStore(
        "unit-bucket",
        client=FakeGCSClient(backend),
        not_found_errors=(FakeNotFoundError,),
        precondition_errors=(FakePreconditionError,),
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


def test_gcs_pending_delete_replay_does_not_remove_a_later_recreation() -> None:
    backend = FakeGCSBackend()
    setup = _store(backend)
    created = setup.create(
        "default",
        "recreated",
        _document("original"),
        idempotency_key="create-before-delete-0001",
    )
    interrupted = _InterruptingGCSGraphStore(
        "unit-bucket",
        client=FakeGCSClient(backend),
        not_found_errors=(FakeNotFoundError,),
        precondition_errors=(FakePreconditionError,),
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


def test_gcs_object_names_and_journals_do_not_persist_raw_idempotency_keys() -> None:
    backend = FakeGCSBackend()
    store = _store(backend)
    raw_key = "create-private-key-0001"
    store.create("default", "private", _document(), idempotency_key=raw_key)

    assert all(raw_key not in name for name in backend.objects)
    assert all(raw_key.encode() not in item.data for item in backend.objects.values())
