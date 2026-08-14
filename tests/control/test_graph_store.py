"""Shared conformance and crash-recovery tests for provider-neutral graph storage."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from dander.control import (
    MAX_GRAPH_DOCUMENT_BYTES,
    AzureBlobGraphStore,
    GCSGraphStore,
    GraphStore,
    GraphStoreAlreadyExistsError,
    GraphStoreConflictError,
    GraphStoreCorruptionError,
    GraphStoreDocumentError,
    GraphStoreIdempotencyConflictError,
    GraphStoreIdentifierError,
    GraphStoreNotFoundError,
    InMemoryGraphStore,
    OCIObjectGraphStore,
    RootedLocalGraphStore,
    S3GraphStore,
    canonicalize_graph_document,
)
from dander.control.bundle import PACKAGED_BUNDLE_DIRECTORY
from dander.control.models import PipelineGraphDocument
from dander.pipeline.graph import graph_to_payload
from tests.control.azure_blob_fakes import FakeAzureContainerClient
from tests.control.gcs_fakes import FakeGCSClient, FakeNotFoundError, FakePreconditionError
from tests.control.oci_object_storage_fakes import FakeOCIObjectStorageClient
from tests.control.s3_fakes import FakeS3Client

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(params=("memory", "local", "gcs", "s3", "azure-blob", "oci-object"))
def graph_store(request: pytest.FixtureRequest, tmp_path: Path) -> GraphStore:
    """Return each initial adapter behind exactly the same conformance tests."""
    if request.param == "memory":
        return InMemoryGraphStore()
    if request.param == "local":
        return RootedLocalGraphStore(tmp_path / "graphs")
    if request.param == "gcs":
        return GCSGraphStore(
            "unit-bucket",
            client=FakeGCSClient(),
            not_found_errors=(FakeNotFoundError,),
            precondition_errors=(FakePreconditionError,),
        )
    if request.param == "azure-blob":
        return AzureBlobGraphStore(
            "https://unitaccount.blob.core.windows.net",
            "unit-container",
            client=FakeAzureContainerClient(),
        )
    if request.param == "oci-object":
        return OCIObjectGraphStore(
            "unit-namespace",
            "unit-bucket",
            client=FakeOCIObjectStorageClient(),
        )
    return S3GraphStore("unit-bucket", client=FakeS3Client())


@pytest.fixture
def graph_document() -> PipelineGraphDocument:
    payload = json.loads(
        (PACKAGED_BUNDLE_DIRECTORY / "fixtures/pipeline-graph.json").read_text(encoding="utf-8")
    )
    return PipelineGraphDocument.model_validate(payload)


def _changed(document: PipelineGraphDocument, name: str) -> PipelineGraphDocument:
    payload = graph_to_payload(document.to_domain())
    payload["name"] = name
    return PipelineGraphDocument.model_validate(payload)


def test_graph_store_conformance_create_read_page_update_conflict_and_delete(
    graph_store: GraphStore,
    graph_document: PipelineGraphDocument,
) -> None:
    created = graph_store.create(
        "default",
        "graph_one",
        graph_document,
        idempotency_key="create-request-0001",
    )
    replayed = graph_store.create(
        "default",
        "graph_one",
        graph_document,
        idempotency_key="create-request-0001",
    )

    assert replayed == created
    assert graph_store.get("default", "graph_one") == created
    assert created.content_sha256 == canonicalize_graph_document(graph_document).content_sha256

    with pytest.raises(GraphStoreIdempotencyConflictError):
        graph_store.create(
            "default",
            "another_graph",
            graph_document,
            idempotency_key="create-request-0001",
        )
    with pytest.raises(GraphStoreAlreadyExistsError):
        graph_store.create(
            "default",
            "graph_one",
            graph_document,
            idempotency_key="create-precondition-0002",
        )

    second = graph_store.create(
        "default",
        "graph_two",
        _changed(graph_document, "second"),
        idempotency_key="create-request-0003",
    )
    first_page = graph_store.list("default", limit=1)
    second_page = graph_store.list("default", cursor=first_page.next_cursor, limit=1)

    assert [item.graph for item in first_page.items] == ["graph_one"]
    assert first_page.next_cursor is not None
    assert not hasattr(first_page.items[0], "document")
    assert [item.graph for item in second_page.items] == ["graph_two"]
    assert second_page.items[0] == second.summary()
    assert second_page.next_cursor is None
    assert graph_store.list("another_project").items == ()

    changed = _changed(graph_document, "updated")
    updated = graph_store.put(
        "default",
        "graph_one",
        changed,
        expected_revision=created.revision,
    )
    assert updated.revision != created.revision
    assert updated.created_at == created.created_at
    assert updated.content_sha256 != created.content_sha256
    with pytest.raises(GraphStoreConflictError):
        graph_store.put(
            "default",
            "graph_one",
            graph_document,
            expected_revision=created.revision,
        )
    with pytest.raises(GraphStoreConflictError):
        graph_store.delete(
            "default",
            "graph_one",
            expected_revision=created.revision,
            idempotency_key="delete-stale-0001",
        )

    receipt = graph_store.delete(
        "default",
        "graph_one",
        expected_revision=updated.revision,
        idempotency_key="delete-request-0002",
    )
    assert (
        graph_store.delete(
            "default",
            "graph_one",
            expected_revision=updated.revision,
            idempotency_key="delete-request-0002",
        )
        == receipt
    )
    assert receipt.content_sha256 == updated.content_sha256
    with pytest.raises(GraphStoreNotFoundError):
        graph_store.get("default", "graph_one")

    # The earlier AlreadyExists failure did not consume this key.
    recreated = graph_store.create(
        "default",
        "graph_one",
        graph_document,
        idempotency_key="create-precondition-0002",
    )
    assert recreated.graph == "graph_one"


def test_graph_store_precondition_failures_do_not_consume_delete_keys(
    graph_store: GraphStore,
    graph_document: PipelineGraphDocument,
) -> None:
    with pytest.raises(GraphStoreNotFoundError):
        graph_store.delete(
            "default",
            "later",
            expected_revision="absent-revision",
            idempotency_key="delete-precondition-0001",
        )
    created = graph_store.create(
        "default",
        "later",
        graph_document,
        idempotency_key="create-later-0001",
    )
    receipt = graph_store.delete(
        "default",
        "later",
        expected_revision=created.revision,
        idempotency_key="delete-precondition-0001",
    )
    assert receipt.revision == created.revision


def test_graph_store_returned_documents_do_not_mutate_persisted_state(
    graph_store: GraphStore,
    graph_document: PipelineGraphDocument,
) -> None:
    created = graph_store.create(
        "default",
        "isolated",
        graph_document,
        idempotency_key="create-isolated-0001",
    )
    created.document.nodes.clear()

    persisted = graph_store.get("default", "isolated")

    assert persisted.document.nodes
    assert (
        persisted.content_sha256 == canonicalize_graph_document(persisted.document).content_sha256
    )


def test_graph_store_rejects_malformed_names_pages_and_cross_project_cursors(
    graph_store: GraphStore,
    graph_document: PipelineGraphDocument,
) -> None:
    with pytest.raises(GraphStoreIdentifierError):
        graph_store.create(
            "../outside",
            "graph",
            graph_document,
            idempotency_key="create-invalid-0001",
        )
    with pytest.raises(GraphStoreIdentifierError):
        graph_store.create(
            "default",
            "graph",
            graph_document,
            idempotency_key="short",
        )
    with pytest.raises(GraphStoreIdentifierError):
        graph_store.list("default", limit=101)

    graph_store.create(
        "default",
        "graph",
        graph_document,
        idempotency_key="create-cursor-0001",
    )
    graph_store.create(
        "default",
        "graph_next",
        graph_document,
        idempotency_key="create-cursor-0002",
    )
    cursor = graph_store.list("default", limit=1).next_cursor
    assert cursor is not None
    with pytest.raises(GraphStoreIdentifierError):
        graph_store.list("different", cursor=cursor)
    with pytest.raises(GraphStoreIdentifierError):
        graph_store.list("default", cursor="not-a-valid-cursor")


def test_canonical_bytes_are_exact_stable_unicode_json_without_a_newline(
    graph_document: PipelineGraphDocument,
) -> None:
    payload = graph_to_payload(graph_document.to_domain())
    payload["name"] = "Café ☃"
    reordered = dict(reversed(tuple(payload.items())))

    first = canonicalize_graph_document(payload)
    second = canonicalize_graph_document(reordered)
    expected = json.dumps(
        graph_to_payload(first.document.to_domain()),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    assert first.data == second.data == expected
    assert first.content_sha256 == second.content_sha256
    assert "Café ☃".encode() in first.data
    assert not first.data.endswith(b"\n")


def test_canonical_bytes_reject_non_finite_values_and_exact_oversize_documents() -> None:
    non_finite = PipelineGraphDocument.model_validate(
        {
            "name": "non_finite",
            "nodes": [
                {
                    "id": "task",
                    "type": "task",
                    "name": "Task",
                    "config": {"value": float("nan")},
                }
            ],
            "edges": [],
        }
    )
    with pytest.raises(GraphStoreDocumentError):
        canonicalize_graph_document(non_finite)

    oversized = PipelineGraphDocument.model_validate(
        {
            "name": "oversized",
            "nodes": [
                {
                    "id": "task",
                    "type": "task",
                    "name": "Task",
                    "config": {"value": "x" * MAX_GRAPH_DOCUMENT_BYTES},
                }
            ],
            "edges": [],
        }
    )
    with pytest.raises(GraphStoreDocumentError, match="exceeds"):
        canonicalize_graph_document(oversized)


def test_canonical_graph_rejects_literal_credentials_without_echoing_values() -> None:
    literal = "inline-value-that-must-never-appear"
    payload = {
        "name": "credential_test",
        "nodes": [
            {
                "id": "task",
                "type": "task",
                "name": "Task",
                "config": {"password": literal},
            }
        ],
        "edges": [],
    }

    with pytest.raises(GraphStoreDocumentError) as captured:
        canonicalize_graph_document(payload)

    assert literal not in str(captured.value)


@pytest.mark.parametrize(
    "reference",
    (
        "secret:source-password",
        "gcp-sm://projects/unit-project/secrets/source-password/versions/latest",
        "azure-kv://https://unit-vault.vault.azure.net/secrets/source-password",
    ),
)
def test_canonical_graph_accepts_recognized_secret_references(reference: str) -> None:
    canonical = canonicalize_graph_document(
        {
            "name": "credential_reference",
            "nodes": [
                {
                    "id": "task",
                    "type": "task",
                    "name": "Task",
                    "config": {"password": reference},
                }
            ],
            "edges": [],
        }
    )

    assert reference.encode() in canonical.data


def test_rooted_local_store_survives_restart_with_revision_and_idempotency(
    tmp_path: Path,
    graph_document: PipelineGraphDocument,
) -> None:
    root = tmp_path / "store"
    first_store = RootedLocalGraphStore(root)
    created = first_store.create(
        "default",
        "persistent",
        graph_document,
        idempotency_key="create-persistent-0001",
    )

    restarted = RootedLocalGraphStore(root)
    assert restarted.get("default", "persistent") == created
    assert (
        restarted.create(
            "default",
            "persistent",
            graph_document,
            idempotency_key="create-persistent-0001",
        )
        == created
    )

    journal_files = tuple((root / ".dander-control" / "idempotency").rglob("*.json"))
    assert len(journal_files) == 1
    assert "create-persistent-0001" not in str(journal_files[0])
    assert b"create-persistent-0001" not in journal_files[0].read_bytes()


class _SimulatedCrashError(RuntimeError):
    """Test-only abrupt stop after one durable local-store boundary."""


class _InterruptingLocalStore(RootedLocalGraphStore):
    def __init__(self, root: Path, fail_at: str) -> None:
        super().__init__(root)
        self._fail_at = fail_at
        self._failed = False

    def _checkpoint(self, stage: str) -> None:
        if stage == self._fail_at and not self._failed:
            self._failed = True
            raise _SimulatedCrashError(stage)


@pytest.mark.parametrize("fail_at", ("after_pending", "after_mutation", "after_completed"))
def test_rooted_local_create_recovers_every_journal_boundary(
    tmp_path: Path,
    graph_document: PipelineGraphDocument,
    fail_at: str,
) -> None:
    root = tmp_path / fail_at
    interrupted = _InterruptingLocalStore(root, fail_at)
    with pytest.raises(_SimulatedCrashError, match=fail_at):
        interrupted.create(
            "default",
            "recover_create",
            graph_document,
            idempotency_key="create-recovery-0001",
        )

    restarted = RootedLocalGraphStore(root)
    recovered = restarted.create(
        "default",
        "recover_create",
        graph_document,
        idempotency_key="create-recovery-0001",
    )
    assert restarted.get("default", "recover_create") == recovered
    assert (
        restarted.create(
            "default",
            "recover_create",
            graph_document,
            idempotency_key="create-recovery-0001",
        )
        == recovered
    )


@pytest.mark.parametrize("fail_at", ("after_pending", "after_mutation", "after_completed"))
def test_rooted_local_delete_recovers_every_journal_boundary(
    tmp_path: Path,
    graph_document: PipelineGraphDocument,
    fail_at: str,
) -> None:
    root = tmp_path / fail_at
    setup = RootedLocalGraphStore(root)
    created = setup.create(
        "default",
        "recover_delete",
        graph_document,
        idempotency_key="create-for-delete-0001",
    )
    interrupted = _InterruptingLocalStore(root, fail_at)
    with pytest.raises(_SimulatedCrashError, match=fail_at):
        interrupted.delete(
            "default",
            "recover_delete",
            expected_revision=created.revision,
            idempotency_key="delete-recovery-0001",
        )

    restarted = RootedLocalGraphStore(root)
    recovered = restarted.delete(
        "default",
        "recover_delete",
        expected_revision=created.revision,
        idempotency_key="delete-recovery-0001",
    )
    assert (
        restarted.delete(
            "default",
            "recover_delete",
            expected_revision=created.revision,
            idempotency_key="delete-recovery-0001",
        )
        == recovered
    )
    with pytest.raises(GraphStoreNotFoundError):
        restarted.get("default", "recover_delete")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_rooted_local_store_rejects_symlink_escape(
    tmp_path: Path,
    graph_document: PipelineGraphDocument,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    graph_parent = root / "projects" / "default"
    graph_parent.mkdir(parents=True)
    (graph_parent / "graphs").symlink_to(outside, target_is_directory=True)
    store = RootedLocalGraphStore(root)

    with pytest.raises(GraphStoreCorruptionError, match="escapes|symlink"):
        store.create(
            "default",
            "graph",
            graph_document,
            idempotency_key="create-symlink-0001",
        )
