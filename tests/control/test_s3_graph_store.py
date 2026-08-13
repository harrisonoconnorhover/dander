"""S3-specific native-condition, pagination, and restart tests."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from dander.control import (
    GraphStoreConflictError,
    GraphStoreCorruptionError,
    GraphStoreError,
    GraphStoreNotFoundError,
    S3GraphStore,
)
from dander.control.bundle import PACKAGED_BUNDLE_DIRECTORY
from dander.control.models import PipelineGraphDocument
from dander.pipeline.graph import graph_to_payload
from tests.control.s3_fakes import FakeS3Backend, FakeS3Client, FakeS3Error

if TYPE_CHECKING:
    from collections.abc import Mapping


def _document(name: str = "s3_graph") -> PipelineGraphDocument:
    payload = json.loads(
        (PACKAGED_BUNDLE_DIRECTORY / "fixtures/pipeline-graph.json").read_text(encoding="utf-8")
    )
    payload["name"] = name
    return PipelineGraphDocument.model_validate(payload)


def _store(backend: FakeS3Backend | None = None) -> S3GraphStore:
    return S3GraphStore("unit-bucket", client=FakeS3Client(backend))


def test_s3_module_import_does_not_import_aws_sdk() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import dander.control.s3_graph_store; "
                "assert 'boto3' not in sys.modules; assert 'botocore' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_s3_rejects_directory_bucket_bindings() -> None:
    with pytest.raises(GraphStoreCorruptionError, match="general-purpose"):
        S3GraphStore("unit--use1-az1--x-s3", client=FakeS3Client())


def test_s3_uses_exact_quoted_etag_conditions_for_every_mutation() -> None:
    backend = FakeS3Backend()
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

    graph_puts = [call for call in backend.put_calls if "/graphs/native.json" in str(call["Key"])]
    graph_deletes = [
        call for call in backend.delete_calls if "/graphs/native.json" in str(call["Key"])
    ]
    assert graph_puts[0]["IfNoneMatch"] == "*"
    assert graph_puts[1]["IfMatch"] == created.revision
    assert graph_puts[2]["IfMatch"] == updated.revision
    assert graph_deletes == [
        {
            "Bucket": "unit-bucket",
            "Key": graph_puts[2]["Key"],
            "IfMatch": backend.delete_calls[-1]["IfMatch"],
        }
    ]
    assert str(graph_puts[1]["IfMatch"]).startswith('"')
    assert str(graph_deletes[0]["IfMatch"]).startswith('"')


def test_s3_limit_one_pagination_is_exclusive_and_body_free() -> None:
    backend = FakeS3Backend()
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
    assert all(call["MaxKeys"] == 3 for call in backend.list_calls)
    assert any("StartAfter" in call for call in backend.list_calls)
    assert backend.get_calls == []
    assert backend.head_calls


def test_s3_concurrent_identical_creates_converge_on_one_exact_result() -> None:
    backend = FakeS3Backend()
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

    def create(store: S3GraphStore) -> object:
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


def test_s3_concurrent_identical_deletes_converge_on_one_exact_receipt() -> None:
    backend = FakeS3Backend()
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

    def delete(store: S3GraphStore) -> object:
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


def test_s3_reads_are_etag_pinned_bounded_and_closed() -> None:
    backend = FakeS3Backend()
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
    assert all("IfMatch" in call for call in backend.get_calls)
    assert all(str(call["Range"]).startswith("bytes=0-") for call in backend.get_calls)
    assert backend.bodies and all(body.closed for body in backend.bodies)
    assert all(body.read_amounts[0] is not None for body in backend.bodies)

    graph_name = next(name for name in backend.objects if name.endswith("/graphs/bounded.json"))
    backend.objects[graph_name].data += b"x" * (6 * 1024 * 1024)
    with pytest.raises(GraphStoreCorruptionError, match="exceeds"):
        store.get("default", "bounded")


@pytest.mark.parametrize(
    ("code", "status"),
    (("NoSuchKey", 404), ("ConditionalRequestConflict", 409), ("PreconditionFailed", 412)),
)
def test_s3_conditional_write_errors_are_conflicts(code: str, status: int) -> None:
    backend = FakeS3Backend()
    store = _store(backend)
    created = store.create(
        "default",
        "write_error",
        _document(),
        idempotency_key="create-write-error-0001",
    )
    backend.next_put_error = FakeS3Error(code, status)

    with pytest.raises(GraphStoreConflictError) as captured:
        store.put(
            "default",
            "write_error",
            _document("changed"),
            expected_revision=created.revision,
        )

    assert "fake detail" not in str(captured.value)


def test_s3_conditional_read_race_retries_instead_of_reporting_absence() -> None:
    backend = FakeS3Backend()
    store = _store(backend)
    expected = store.create(
        "default",
        "read_race",
        _document(),
        idempotency_key="create-read-race-0001",
    )
    backend.next_get_error = FakeS3Error("NoSuchKey", 404)

    assert store.get("default", "read_race") == expected


class _FailingHeadClient(FakeS3Client):
    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        self.backend.head_calls.append(dict(kwargs))
        raise FakeS3Error("NoSuchBucket", 404)


def test_s3_missing_bucket_is_not_misclassified_as_missing_graph() -> None:
    store = S3GraphStore("unit-bucket", client=_FailingHeadClient())

    with pytest.raises(GraphStoreError) as captured:
        store.get("default", "missing_graph")

    assert type(captured.value) is GraphStoreError
    assert "fake detail" not in str(captured.value)


class _FailingDeleteClient(FakeS3Client):
    def __init__(self, backend: FakeS3Backend, code: str, status: int) -> None:
        super().__init__(backend)
        self._error = FakeS3Error(code, status)

    def delete_object(self, **kwargs: object) -> Mapping[str, object]:
        self.backend.delete_calls.append(dict(kwargs))
        raise self._error


@pytest.mark.parametrize(
    ("code", "status"),
    (("NoSuchKey", 404), ("ConditionalRequestConflict", 409), ("PreconditionFailed", 412)),
)
def test_s3_conditional_delete_errors_are_conflicts(code: str, status: int) -> None:
    backend = FakeS3Backend()
    created = _store(backend).create(
        "default",
        "delete_error",
        _document(),
        idempotency_key="create-delete-error-0001",
    )
    store = S3GraphStore(
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


def test_s3_unexpected_provider_errors_are_sanitized() -> None:
    backend = FakeS3Backend()
    store = _store(backend)
    store.create(
        "default",
        "provider_error",
        _document(),
        idempotency_key="create-provider-error-0001",
    )
    backend.next_get_error = FakeS3Error("InternalError", 500)

    with pytest.raises(GraphStoreError) as captured:
        store.get("default", "provider_error")

    assert "fake detail" not in str(captured.value)


class _SimulatedCrashError(RuntimeError):
    """Abrupt stop after a durable fake-S3 boundary."""


class _InterruptingS3GraphStore(S3GraphStore):
    def arm(self, fail_at: str) -> None:
        self._fail_at = fail_at
        self._failed = False

    def _checkpoint(self, stage: str) -> None:
        if stage == getattr(self, "_fail_at", None) and not self._failed:
            self._failed = True
            raise _SimulatedCrashError(stage)


@pytest.mark.parametrize("later_action", ("put", "delete"))
def test_s3_crashed_create_replays_exact_original_after_later_mutation(
    later_action: str,
) -> None:
    backend = FakeS3Backend()
    interrupted = _InterruptingS3GraphStore("unit-bucket", client=FakeS3Client(backend))
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
def test_s3_delete_recovers_each_durable_boundary(fail_at: str) -> None:
    backend = FakeS3Backend()
    setup = _store(backend)
    created = setup.create(
        "default",
        "recover_delete",
        _document(),
        idempotency_key="create-for-delete-0001",
    )
    interrupted = _InterruptingS3GraphStore("unit-bucket", client=FakeS3Client(backend))
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


def test_s3_pending_delete_replay_does_not_remove_a_later_recreation() -> None:
    backend = FakeS3Backend()
    setup = _store(backend)
    created = setup.create(
        "default",
        "recreated",
        _document("original"),
        idempotency_key="create-before-delete-0001",
    )
    interrupted = _InterruptingS3GraphStore("unit-bucket", client=FakeS3Client(backend))
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


def test_s3_object_names_and_journals_do_not_persist_raw_idempotency_keys() -> None:
    backend = FakeS3Backend()
    store = _store(backend)
    raw_key = "create-private-key-0001"
    store.create("default", "private", _document(), idempotency_key=raw_key)

    assert all(raw_key not in name for name in backend.objects)
    assert all(raw_key.encode() not in item.data for item in backend.objects.values())


def test_s3_expected_owner_is_sent_without_becoming_public_state() -> None:
    backend = FakeS3Backend()
    store = S3GraphStore(
        "unit-bucket",
        client=FakeS3Client(backend),
        expected_bucket_owner="123456789012",
    )
    store.create(
        "default",
        "owner",
        _document(),
        idempotency_key="create-owner-0001",
    )

    calls = backend.put_calls + backend.head_calls + backend.get_calls
    assert calls
    assert all(call["ExpectedBucketOwner"] == "123456789012" for call in calls)
    assert not hasattr(store, "expected_bucket_owner")
