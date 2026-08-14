"""Azure Blob native-condition, pagination, error, and recovery tests."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from dander.control import (
    AzureBlobGraphStore,
    GraphStoreConflictError,
    GraphStoreCorruptionError,
    GraphStoreError,
    GraphStoreNotFoundError,
)
from dander.control.bundle import PACKAGED_BUNDLE_DIRECTORY
from dander.control.models import PipelineGraphDocument
from dander.pipeline.graph import graph_to_payload
from tests.control.azure_blob_fakes import (
    FakeAzureBackend,
    FakeAzureContainerClient,
    FakeAzureError,
)

_ACCOUNT_URL = "https://unitaccount.blob.core.windows.net"


def _document(name: str = "azure_blob_graph") -> PipelineGraphDocument:
    payload = json.loads(
        (PACKAGED_BUNDLE_DIRECTORY / "fixtures/pipeline-graph.json").read_text(encoding="utf-8")
    )
    payload["name"] = name
    return PipelineGraphDocument.model_validate(payload)


def _store(backend: FakeAzureBackend | None = None) -> AzureBlobGraphStore:
    return AzureBlobGraphStore(
        _ACCOUNT_URL,
        "unit-container",
        client=FakeAzureContainerClient(backend),
    )


def test_azure_blob_module_import_does_not_import_azure_sdk() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import dander.control.azure_blob_graph_store; "
                "assert not any(name == 'azure' or name.startswith('azure.') "
                "for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("account_url", "container"),
    (
        ("http://unitaccount.blob.core.windows.net", "unit-container"),
        ("https://user@unitaccount.blob.core.windows.net", "unit-container"),
        ("https://unitaccount.blob.core.windows.net/path", "unit-container"),
        (_ACCOUNT_URL, "Unit-Container"),
        (_ACCOUNT_URL, "bad--container"),
    ),
)
def test_azure_blob_rejects_unsafe_bindings(account_url: str, container: str) -> None:
    with pytest.raises(GraphStoreCorruptionError, match="binding"):
        AzureBlobGraphStore(account_url, container, client=FakeAzureContainerClient())


def test_azure_blob_uses_exact_native_conditions_and_current_only_delete() -> None:
    backend = FakeAzureBackend()
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

    graph_uploads = [
        call for call in backend.upload_calls if str(call["blob"]).endswith("/graphs/native.json")
    ]
    graph_deletes = [
        call for call in backend.delete_calls if str(call["blob"]).endswith("/graphs/native.json")
    ]
    assert graph_uploads[0]["overwrite"] is False
    assert "etag" not in graph_uploads[0]
    assert graph_uploads[1]["overwrite"] is True
    assert graph_uploads[1]["etag"] == created.revision
    assert graph_uploads[1]["match_condition"] == "if_not_modified"
    assert graph_uploads[2]["etag"] == updated.revision
    assert len(graph_deletes) == 1
    assert graph_deletes[0]["match_condition"] == "if_not_modified"
    assert "delete_snapshots" not in graph_deletes[0]
    assert "version_id" not in graph_deletes[0]


def test_azure_blob_pagination_is_exclusive_body_free_and_follows_short_pages() -> None:
    backend = FakeAzureBackend()
    store = _store(backend)
    expected = ["alpha", "bravo", "charlie", "delta"]
    for index, graph in enumerate(expected):
        store.create(
            "default",
            graph,
            _document(graph),
            idempotency_key=f"create-page-{index:04d}",
        )
    backend.page_size_override = 1
    backend.download_calls.clear()
    backend.properties_calls.clear()
    backend.list_calls.clear()

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
    assert all(call["results_per_page"] == 3 for call in backend.list_calls)
    assert any(call["start_from"] is not None for call in backend.list_calls)
    assert backend.download_calls == []
    assert backend.properties_calls == []


def test_azure_blob_concurrent_identical_creates_converge() -> None:
    backend = FakeAzureBackend()
    start = threading.Barrier(2)
    journal_create = threading.Barrier(2)
    graph_create = threading.Barrier(2)
    journal_complete = threading.Barrier(2)

    def synchronize(name: str, overwrite: bool, etag: str | None, data: bytes) -> None:
        payload = json.loads(data)
        if "/idempotency/default/create/" in name and not overwrite:
            journal_create.wait(timeout=5)
        elif name.endswith("/graphs/concurrent.json") and not overwrite:
            graph_create.wait(timeout=5)
        elif (
            "/idempotency/default/create/" in name
            and etag is not None
            and payload.get("status") == "completed"
        ):
            journal_complete.wait(timeout=5)

    backend.before_upload = synchronize
    stores = (_store(backend), _store(backend))

    def create(store: AzureBlobGraphStore) -> object:
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


def test_azure_blob_concurrent_identical_deletes_converge() -> None:
    backend = FakeAzureBackend()
    created = _store(backend).create(
        "default",
        "concurrent_delete",
        _document(),
        idempotency_key="create-for-concurrent-delete-0001",
    )
    start = threading.Barrier(2)
    journal_create = threading.Barrier(2)
    graph_fence = threading.Barrier(2)
    journal_fence = threading.Barrier(2)

    def synchronize(name: str, overwrite: bool, etag: str | None, data: bytes) -> None:
        payload = json.loads(data)
        if "/idempotency/default/delete/" in name and not overwrite:
            journal_create.wait(timeout=5)
        elif name.endswith("/graphs/concurrent_delete.json") and payload.get("delete_fence"):
            graph_fence.wait(timeout=5)
        elif (
            "/idempotency/default/delete/" in name
            and etag is not None
            and payload.get("fence_revision") is not None
            and payload.get("status") == "pending"
        ):
            journal_fence.wait(timeout=5)

    backend.before_upload = synchronize
    stores = (_store(backend), _store(backend))

    def delete(store: AzureBlobGraphStore) -> object:
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


def test_azure_blob_reads_are_etag_pinned_and_bounded() -> None:
    backend = FakeAzureBackend()
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
    assert all(call["match_condition"] == "if_not_modified" for call in backend.download_calls)
    assert all(call["offset"] == 0 for call in backend.download_calls)
    assert all(call["max_concurrency"] == 1 for call in backend.download_calls)
    assert all(isinstance(call["length"], int) for call in backend.download_calls)
    assert backend.downloaders and all(item.readall_calls == 1 for item in backend.downloaders)

    graph_name = next(name for name in backend.objects if name.endswith("/graphs/bounded.json"))
    backend.objects[graph_name].data += b"x" * (6 * 1024 * 1024)
    with pytest.raises(GraphStoreCorruptionError, match="exceeds"):
        store.get("default", "bounded")


def test_azure_blob_read_race_retries_but_missing_container_fails_closed() -> None:
    backend = FakeAzureBackend()
    store = _store(backend)
    expected = store.create(
        "default",
        "read_race",
        _document(),
        idempotency_key="create-read-race-0001",
    )
    backend.next_download_error = FakeAzureError("BlobNotFound", 404)

    assert store.get("default", "read_race") == expected

    backend.next_properties_error = FakeAzureError("ContainerNotFound", 404)
    with pytest.raises(GraphStoreError) as captured:
        store.get("default", "read_race")
    assert type(captured.value) is GraphStoreError
    assert "fake detail" not in str(captured.value)


@pytest.mark.parametrize(
    ("error_code", "status_code"),
    (("ConditionNotMet", 412), ("BlobNotFound", 404)),
)
def test_azure_blob_conditional_update_errors_are_conflicts(
    error_code: str,
    status_code: int,
) -> None:
    backend = FakeAzureBackend()
    store = _store(backend)
    created = store.create(
        "default",
        "write_error",
        _document(),
        idempotency_key="create-write-error-0001",
    )
    backend.next_upload_error = FakeAzureError(error_code, status_code)

    with pytest.raises(GraphStoreConflictError) as captured:
        store.put(
            "default",
            "write_error",
            _document("changed"),
            expected_revision=created.revision,
        )
    assert "fake detail" not in str(captured.value)


def test_azure_blob_snapshot_policy_failure_is_not_a_revision_conflict() -> None:
    backend = FakeAzureBackend()
    store = _store(backend)
    created = store.create(
        "default",
        "snapshotted",
        _document(),
        idempotency_key="create-snapshotted-0001",
    )
    backend.next_delete_error = FakeAzureError("SnapshotsPresent", 409)

    with pytest.raises(GraphStoreError) as captured:
        store.delete(
            "default",
            "snapshotted",
            expected_revision=created.revision,
            idempotency_key="delete-snapshotted-0001",
        )
    assert type(captured.value) is GraphStoreError
    assert "fake detail" not in str(captured.value)


def test_azure_blob_status_without_a_service_code_is_not_a_false_conflict() -> None:
    backend = FakeAzureBackend()
    error = FakeAzureError("unused", 409)
    delattr(error, "error_code")
    backend.next_upload_error = error

    with pytest.raises(GraphStoreError) as captured:
        _store(backend).create(
            "default",
            "status_only",
            _document(),
            idempotency_key="create-status-only-0001",
        )
    assert type(captured.value) is GraphStoreError
    assert "fake detail" not in str(captured.value)


def test_azure_blob_unexpected_provider_errors_are_sanitized() -> None:
    backend = FakeAzureBackend()
    store = _store(backend)
    store.create(
        "default",
        "provider_error",
        _document(),
        idempotency_key="create-provider-error-0001",
    )
    backend.next_download_error = FakeAzureError("InternalError", 500)

    with pytest.raises(GraphStoreError) as captured:
        store.get("default", "provider_error")
    assert "fake detail" not in str(captured.value)


class _SimulatedCrashError(RuntimeError):
    """Abrupt stop after a durable fake-Azure boundary."""


class _InterruptingAzureBlobGraphStore(AzureBlobGraphStore):
    def arm(self, fail_at: str) -> None:
        self._fail_at = fail_at
        self._failed = False

    def _checkpoint(self, stage: str) -> None:
        if stage == getattr(self, "_fail_at", None) and not self._failed:
            self._failed = True
            raise _SimulatedCrashError(stage)


@pytest.mark.parametrize(
    "fail_at",
    ("after_delete_fence", "after_graph_delete", "after_delete_completed"),
)
def test_azure_blob_delete_recovers_each_durable_boundary(fail_at: str) -> None:
    backend = FakeAzureBackend()
    created = _store(backend).create(
        "default",
        "recover_delete",
        _document(),
        idempotency_key="create-for-delete-0001",
    )
    interrupted = _InterruptingAzureBlobGraphStore(
        _ACCOUNT_URL,
        "unit-container",
        client=FakeAzureContainerClient(backend),
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


def test_azure_blob_pending_delete_does_not_remove_later_recreation() -> None:
    backend = FakeAzureBackend()
    created = _store(backend).create(
        "default",
        "recreated",
        _document("original"),
        idempotency_key="create-before-delete-0001",
    )
    interrupted = _InterruptingAzureBlobGraphStore(
        _ACCOUNT_URL,
        "unit-container",
        client=FakeAzureContainerClient(backend),
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


def test_azure_blob_does_not_persist_raw_idempotency_keys() -> None:
    backend = FakeAzureBackend()
    raw_key = "create-private-key-0001"
    _store(backend).create("default", "private", _document(), idempotency_key=raw_key)

    assert all(raw_key not in name for name in backend.objects)
    assert all(raw_key.encode() not in item.data for item in backend.objects.values())
