"""Amazon S3 implementation of the provider-neutral ``GraphStore`` contract.

The module itself imports no AWS SDK. Constructing the adapter without an injected client is the
explicit provider boundary that lazily imports and creates an S3 client. Object ETags remain
private implementation details and are exposed only as opaque GraphStore revisions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ValidationError

from dander.control.graph_store import (
    MAX_GRAPH_DOCUMENT_BYTES,
    MAX_GRAPH_PAGE_SIZE,
    Clock,
    GraphDeleteReceipt,
    GraphPage,
    GraphRecord,
    GraphStoreAlreadyExistsError,
    GraphStoreConflictError,
    GraphStoreCorruptionError,
    GraphStoreError,
    GraphStoreIdempotencyConflictError,
    GraphStoreNotFoundError,
    GraphSummary,
    _canonical_json_bytes,
    _decode_cursor,
    _encode_cursor,
    _utc_now,
    _validated_graph_key,
    _validated_identifier,
    _validated_max_bytes,
    _validated_page_size,
    canonicalize_graph_document,
)
from dander.control.object_graph_mutations import (
    _MAX_DELETE_JOURNAL_BYTES,
    _JournaledGraphMutations,
)
from dander.control.object_graph_records import (
    _CreateJournal,
    _DeleteFence,
    _DeleteJournal,
    _GraphObjectMetadata,
    _StoredGraph,
)

if TYPE_CHECKING:
    import builtins


_MAX_GRAPH_ENVELOPE_OVERHEAD = 256 * 1024
_MAX_JOURNAL_OVERHEAD = 256 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,510}[A-Za-z0-9]$")
_GENERAL_PURPOSE_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_EXPECTED_OWNER = re.compile(r"^[0-9]{12}$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class _BodyPort(Protocol):
    def read(self, amount: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class _ClientPort(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class _ObjectHead:
    etag: str
    size: int
    metadata: dict[str, str]


class S3GraphStore(_JournaledGraphMutations):
    """Persist canonical graph envelopes in one general-purpose S3 bucket and prefix."""

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "dander-control/v1",
        client: _ClientPort | None = None,
        expected_bucket_owner: str | None = None,
        max_graph_bytes: int = MAX_GRAPH_DOCUMENT_BYTES,
        clock: Clock | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if (
            not isinstance(bucket, str)
            or _GENERAL_PURPOSE_BUCKET.fullmatch(bucket) is None
            or ".." in bucket
            or ".-" in bucket
            or "-." in bucket
            or bucket.endswith("--x-s3")
        ):
            raise GraphStoreCorruptionError(
                "The S3 graph-store binding must name a general-purpose bucket."
            )
        prefix = prefix.strip("/")
        if (
            not prefix
            or _PREFIX.fullmatch(prefix) is None
            or any(part in {"", ".", ".."} for part in prefix.split("/"))
        ):
            raise GraphStoreCorruptionError("The S3 graph-store prefix binding is invalid.")
        if expected_bucket_owner is not None and (
            not isinstance(expected_bucket_owner, str)
            or _EXPECTED_OWNER.fullmatch(expected_bucket_owner) is None
        ):
            raise GraphStoreCorruptionError("The S3 graph-store owner binding is invalid.")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise GraphStoreCorruptionError("The S3 graph-store timeout is invalid.")
        if client is None:
            import boto3  # type: ignore
            from botocore.config import Config  # type: ignore

            client = cast(
                "_ClientPort",
                boto3.client(
                    "s3",
                    config=Config(
                        connect_timeout=float(timeout_seconds),
                        read_timeout=float(timeout_seconds),
                        retries={"max_attempts": 3, "mode": "standard"},
                    ),
                ),
            )
        self._client = client
        self._bucket_name = bucket
        self._prefix = prefix
        self._expected_bucket_owner = expected_bucket_owner
        self._max_graph_bytes = _validated_max_bytes(max_graph_bytes)
        self._max_graph_envelope_bytes = max_graph_bytes + _MAX_GRAPH_ENVELOPE_OVERHEAD
        self._max_create_journal_bytes = max_graph_bytes + _MAX_JOURNAL_OVERHEAD
        self._clock = clock or _utc_now

    @property
    def bucket_name(self) -> str:
        """Return the immutable bucket binding without provider client details."""
        return self._bucket_name

    @property
    def prefix(self) -> str:
        """Return the immutable object-prefix binding."""
        return self._prefix

    def list(self, project: str, *, cursor: str | None = None, limit: int = 50) -> GraphPage:
        project = _validated_identifier(project, "project")
        limit = _validated_page_size(limit)
        after = _decode_cursor(project, cursor)
        graph_prefix = self._graph_prefix(project)
        start_after = self._graph_name(project, after) if after is not None else None
        items: builtins.list[GraphSummary] = []
        while len(items) <= limit:
            entries, truncated = self._list_entries(
                prefix=graph_prefix,
                start_after=start_after,
                max_keys=min(MAX_GRAPH_PAGE_SIZE + 2, limit + 2),
            )
            if not entries:
                if truncated:
                    raise GraphStoreCorruptionError("The S3 graph-store page is invalid.")
                break
            last_seen = start_after
            for entry in entries:
                name = entry.get("Key")
                if not isinstance(name, str):
                    raise GraphStoreCorruptionError("The S3 graph-store page is invalid.")
                last_seen = name
                graph = self._graph_from_name(project, name)
                summary = self._summary_from_key(name, project, graph)
                if summary is not None:
                    items.append(summary)
                    if len(items) > limit:
                        break
            if len(items) > limit or not truncated:
                break
            if last_seen is None or last_seen == start_after:
                raise GraphStoreCorruptionError("The S3 graph-store page did not advance.")
            start_after = last_seen
        selected = tuple(items[:limit])
        next_cursor = _encode_cursor(project, selected[-1].graph) if len(items) > limit else None
        return GraphPage(items=selected, next_cursor=next_cursor)

    def get(self, project: str, graph: str) -> GraphRecord:
        project, graph = _validated_graph_key(project, graph)
        resolved = self._load_resolved(project, graph)
        if resolved is None:
            raise GraphStoreNotFoundError("The graph does not exist.")
        return resolved[0]

    def _resume_create(self, journal: _CreateJournal, journal_etag: str) -> GraphRecord:
        for _ in range(8):
            canonical = canonicalize_graph_document(
                journal.planned_graph.document,
                max_bytes=self._max_graph_bytes,
            )
            if canonical.content_sha256 != journal.planned_graph.content_sha256:
                raise GraphStoreCorruptionError("The S3 create journal is inconsistent.")
            if journal.status == "completed":
                if journal.result_revision is None:
                    raise GraphStoreCorruptionError("The S3 create journal is invalid.")
                return journal.planned_graph.record(
                    canonical,
                    _checked_etag(journal.result_revision),
                )
            loaded = self._read_graph(journal.project, journal.graph)
            if loaded is None:
                try:
                    etag = self._write_model(
                        self._graph_name(journal.project, journal.graph),
                        journal.planned_graph,
                        expected_etag=None,
                        object_metadata=_GraphObjectMetadata.from_stored(journal.planned_graph),
                    )
                except GraphStoreConflictError:
                    loaded = self._read_graph(journal.project, journal.graph)
                    if loaded is None:
                        raise GraphStoreConflictError(
                            "The graph create could not be recovered."
                        ) from None
                else:
                    self._checkpoint("after_graph_create")
                    loaded = (journal.planned_graph, etag)
            current, etag = loaded
            if (
                current.create_key_sha256 != journal.key_sha256
                or current.create_request_sha256 != journal.request_sha256
            ):
                self._delete_object(
                    self._journal_name(journal.project, "create", journal.key_sha256),
                    journal_etag,
                )
                raise GraphStoreAlreadyExistsError("The graph already exists.")
            if current != journal.planned_graph:
                journal, journal_etag = self._reload_create_journal(journal)
                if journal.status == "completed":
                    continue
                raise GraphStoreCorruptionError("The pending S3 create graph is inconsistent.")
            completed = journal.model_copy(update={"status": "completed", "result_revision": etag})
            try:
                self._write_model(
                    self._journal_name(journal.project, "create", journal.key_sha256),
                    completed,
                    expected_etag=journal_etag,
                )
            except GraphStoreConflictError:
                journal, journal_etag = self._reload_create_journal(journal)
                continue
            self._checkpoint("after_create_completed")
            return current.record(canonical, etag)
        raise GraphStoreConflictError("The graph create did not converge.")

    def _reload_create_journal(
        self,
        expected: _CreateJournal,
    ) -> tuple[_CreateJournal, str]:
        loaded = self._read_model(
            self._journal_name(expected.project, "create", expected.key_sha256),
            _CreateJournal,
            self._max_create_journal_bytes,
        )
        if loaded is None:
            raise GraphStoreConflictError("The S3 create journal disappeared.")
        self._validate_create_replay(
            loaded[0],
            expected.project,
            expected.graph,
            expected.key_sha256,
            expected.request_sha256,
        )
        return loaded[0], loaded[1]

    def _resume_delete(
        self,
        journal: _DeleteJournal,
        journal_etag: str,
    ) -> GraphDeleteReceipt:
        for _ in range(12):
            if journal.status == "completed":
                return journal.receipt.receipt()
            loaded = self._read_graph(journal.project, journal.graph)
            if journal.fence_revision is not None:
                if loaded is not None:
                    current, etag = loaded
                    if etag == journal.fence_revision:
                        if not self._owns_fence(current, journal):
                            raise GraphStoreCorruptionError("The S3 delete fence is inconsistent.")
                        try:
                            self._delete_object(
                                self._graph_name(journal.project, journal.graph),
                                etag,
                            )
                        except GraphStoreConflictError:
                            continue
                        self._checkpoint("after_graph_delete")
                    elif current.delete_fence is not None:
                        self._resolve_loaded_graph(current, etag)
                return self._complete_delete(journal, journal_etag)
            if loaded is None:
                try:
                    self._delete_object(
                        self._journal_name(journal.project, "delete", journal.key_sha256),
                        journal_etag,
                    )
                except GraphStoreConflictError:
                    journal, journal_etag = self._reload_delete_journal(journal)
                    continue
                raise GraphStoreNotFoundError("The graph does not exist.")
            current, etag = loaded
            if current.delete_fence is not None:
                if not self._owns_fence(current, journal):
                    self._resolve_loaded_graph(current, etag)
                    try:
                        self._delete_object(
                            self._journal_name(journal.project, "delete", journal.key_sha256),
                            journal_etag,
                        )
                    except GraphStoreConflictError:
                        journal, journal_etag = self._reload_delete_journal(journal)
                        continue
                    raise GraphStoreNotFoundError("The graph does not exist.")
                fence_revision = etag
            else:
                if etag != journal.expected_revision:
                    try:
                        self._delete_object(
                            self._journal_name(journal.project, "delete", journal.key_sha256),
                            journal_etag,
                        )
                    except GraphStoreConflictError:
                        journal, journal_etag = self._reload_delete_journal(journal)
                        continue
                    raise GraphStoreConflictError("The graph revision is stale.")
                fenced = current.model_copy(
                    update={
                        "delete_fence": _DeleteFence(
                            key_sha256=journal.key_sha256,
                            request_sha256=journal.request_sha256,
                            expected_revision=journal.expected_revision,
                        )
                    }
                )
                try:
                    fence_revision = self._write_model(
                        self._graph_name(journal.project, journal.graph),
                        fenced,
                        expected_etag=etag,
                        object_metadata=_GraphObjectMetadata.from_stored(fenced),
                    )
                except GraphStoreConflictError:
                    continue
                self._checkpoint("after_delete_fence")
            with_fence = journal.model_copy(update={"fence_revision": fence_revision})
            try:
                new_journal_etag = self._write_model(
                    self._journal_name(journal.project, "delete", journal.key_sha256),
                    with_fence,
                    expected_etag=journal_etag,
                )
            except GraphStoreConflictError:
                journal, journal_etag = self._reload_delete_journal(journal)
                continue
            journal = with_fence
            journal_etag = new_journal_etag
        raise GraphStoreConflictError("The graph delete did not converge.")

    def _complete_delete(
        self,
        journal: _DeleteJournal,
        journal_etag: str,
    ) -> GraphDeleteReceipt:
        for _ in range(8):
            if journal.status == "completed":
                return journal.receipt.receipt()
            completed = journal.model_copy(update={"status": "completed"})
            try:
                self._write_model(
                    self._journal_name(journal.project, "delete", journal.key_sha256),
                    completed,
                    expected_etag=journal_etag,
                )
            except GraphStoreConflictError:
                journal, journal_etag = self._reload_delete_journal(journal)
                continue
            self._checkpoint("after_delete_completed")
            return journal.receipt.receipt()
        raise GraphStoreConflictError("The graph delete completion did not converge.")

    def _reload_delete_journal(
        self,
        expected: _DeleteJournal,
    ) -> tuple[_DeleteJournal, str]:
        loaded = self._read_model(
            self._journal_name(expected.project, "delete", expected.key_sha256),
            _DeleteJournal,
            _MAX_DELETE_JOURNAL_BYTES,
        )
        if loaded is None:
            raise GraphStoreConflictError("The S3 delete journal disappeared.")
        self._validate_delete_replay(
            loaded[0],
            expected.project,
            expected.graph,
            expected.key_sha256,
            expected.request_sha256,
        )
        return loaded[0], loaded[1]

    def _load_resolved(
        self,
        project: str,
        graph: str,
    ) -> tuple[GraphRecord, _StoredGraph, str] | None:
        loaded = self._read_graph(project, graph)
        if loaded is None:
            return None
        return self._resolve_loaded_graph(*loaded)

    def _summary_from_key(
        self,
        name: str,
        project: str,
        graph: str,
    ) -> GraphSummary | None:
        for _ in range(2):
            head = self._head_object(name)
            if head is None:
                return None
            if head.size > self._max_graph_envelope_bytes:
                raise GraphStoreCorruptionError("An S3 graph-store object exceeds its bound.")
            try:
                metadata = _GraphObjectMetadata.model_validate(head.metadata)
            except ValidationError as error:
                raise GraphStoreCorruptionError(
                    "An S3 graph-store object has invalid metadata."
                ) from error
            if metadata.project != project or metadata.graph != graph:
                raise GraphStoreCorruptionError("The S3 graph metadata is addressed incorrectly.")
            if metadata.delete_key_sha256 is None:
                return metadata.summary(head.etag)
            loaded = self._read_graph(project, graph)
            if loaded is None:
                return None
            if loaded[1] != head.etag:
                continue
            resolved = self._resolve_loaded_graph(*loaded)
            return resolved[0].summary() if resolved is not None else None
        raise GraphStoreConflictError("The S3 graph changed during listing.")

    def _resolve_loaded_graph(
        self,
        stored: _StoredGraph,
        etag: str,
    ) -> tuple[GraphRecord, _StoredGraph, str] | None:
        if stored.delete_fence is not None:
            loaded = self._read_model(
                self._journal_name(stored.project, "delete", stored.delete_fence.key_sha256),
                _DeleteJournal,
                _MAX_DELETE_JOURNAL_BYTES,
            )
            if loaded is None:
                raise GraphStoreCorruptionError("The S3 graph has an orphaned delete fence.")
            self._resume_delete(loaded[0], loaded[1])
            return None
        create_journal = self._read_model(
            self._journal_name(stored.project, "create", stored.create_key_sha256),
            _CreateJournal,
            self._max_create_journal_bytes,
        )
        if create_journal is None:
            raise GraphStoreCorruptionError("The S3 graph has no create journal.")
        self._validate_create_replay(
            create_journal[0],
            stored.project,
            stored.graph,
            stored.create_key_sha256,
            stored.create_request_sha256,
        )
        if create_journal[0].status == "pending":
            self._resume_create(create_journal[0], create_journal[1])
        canonical = canonicalize_graph_document(stored.document, max_bytes=self._max_graph_bytes)
        if canonical.content_sha256 != stored.content_sha256:
            raise GraphStoreCorruptionError("The S3 graph record has an invalid content hash.")
        return stored.record(canonical, etag), stored, etag

    def _read_graph(self, project: str, graph: str) -> tuple[_StoredGraph, str] | None:
        loaded = self._read_model(
            self._graph_name(project, graph),
            _StoredGraph,
            self._max_graph_envelope_bytes,
        )
        if loaded is None:
            return None
        stored, etag, metadata = loaded
        self._validate_graph_address(stored, project, graph)
        if metadata != _metadata_from_model(_GraphObjectMetadata.from_stored(stored)):
            raise GraphStoreCorruptionError("The S3 graph metadata does not match its document.")
        return stored, etag

    @staticmethod
    def _validate_graph_address(stored: _StoredGraph, project: str, graph: str) -> None:
        if stored.project != project or stored.graph != graph:
            raise GraphStoreCorruptionError("The S3 graph record is addressed incorrectly.")

    @staticmethod
    def _owns_fence(stored: _StoredGraph, journal: _DeleteJournal) -> bool:
        return stored.delete_fence == _DeleteFence(
            key_sha256=journal.key_sha256,
            request_sha256=journal.request_sha256,
            expected_revision=journal.expected_revision,
        )

    @staticmethod
    def _validate_create_replay(
        journal: _CreateJournal,
        project: str,
        graph: str,
        key_sha256: str,
        fingerprint: str,
    ) -> None:
        if journal.project != project or journal.key_sha256 != key_sha256:
            raise GraphStoreCorruptionError("The S3 create journal is addressed incorrectly.")
        if journal.graph != graph or journal.request_sha256 != fingerprint:
            raise GraphStoreIdempotencyConflictError(
                "The idempotency key was already used for a different request."
            )

    @staticmethod
    def _validate_delete_replay(
        journal: _DeleteJournal,
        project: str,
        graph: str,
        key_sha256: str,
        fingerprint: str,
    ) -> None:
        if journal.project != project or journal.key_sha256 != key_sha256:
            raise GraphStoreCorruptionError("The S3 delete journal is addressed incorrectly.")
        if journal.graph != graph or journal.request_sha256 != fingerprint:
            raise GraphStoreIdempotencyConflictError(
                "The idempotency key was already used for a different request."
            )

    def _read_model(
        self,
        name: str,
        model_type: type[_ModelT],
        max_bytes: int,
    ) -> tuple[_ModelT, str, dict[str, str]] | None:
        for _ in range(3):
            head = self._head_object(name)
            if head is None:
                return None
            if head.size > max_bytes:
                raise GraphStoreCorruptionError("An S3 graph-store object exceeds its bound.")
            try:
                response = self._client.get_object(
                    **self._request(
                        Key=name,
                        IfMatch=head.etag,
                        Range=f"bytes=0-{max_bytes}",
                    )
                )
            except Exception as error:
                if _is_conditional_read_conflict(error):
                    continue
                raise GraphStoreError("The S3 graph-store read failed.") from error
            response_etag = _checked_etag(response.get("ETag"))
            if response_etag != head.etag:
                continue
            body = response.get("Body")
            if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
                raise GraphStoreCorruptionError("The S3 graph-store body is invalid.")
            stream = cast("_BodyPort", body)
            try:
                data = stream.read(max_bytes + 1)
                extra = stream.read(1)
            except Exception as error:
                raise GraphStoreError("The S3 graph-store body read failed.") from error
            finally:
                try:
                    stream.close()
                except Exception as error:
                    raise GraphStoreError("The S3 graph-store body close failed.") from error
            if not isinstance(data, bytes) or not isinstance(extra, bytes):
                raise GraphStoreCorruptionError("The S3 graph-store body is invalid.")
            if len(data) > max_bytes or extra:
                raise GraphStoreCorruptionError("An S3 graph-store object exceeds its bound.")
            try:
                return model_type.model_validate_json(data), head.etag, head.metadata
            except ValidationError as error:
                raise GraphStoreCorruptionError("An S3 graph-store object is invalid.") from error
        raise GraphStoreConflictError("The S3 graph-store object changed during the read.")

    def _head_object(self, name: str) -> _ObjectHead | None:
        try:
            response = self._client.head_object(**self._request(Key=name))
        except Exception as error:
            if _is_read_not_found(error):
                return None
            raise GraphStoreError("The S3 graph-store metadata read failed.") from error
        etag = _checked_etag(response.get("ETag"))
        size = response.get("ContentLength")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise GraphStoreCorruptionError("The S3 graph-store object size is invalid.")
        metadata = response.get("Metadata")
        if not isinstance(metadata, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()
        ):
            raise GraphStoreCorruptionError("The S3 graph-store object metadata is invalid.")
        return _ObjectHead(etag=etag, size=size, metadata=dict(metadata))

    def _write_model(
        self,
        name: str,
        model: BaseModel,
        *,
        expected_etag: str | None,
        object_metadata: _GraphObjectMetadata | None = None,
    ) -> str:
        data = _canonical_json_bytes(model.model_dump(mode="json"))
        condition = (
            {"IfNoneMatch": "*"}
            if expected_etag is None
            else {"IfMatch": _checked_etag(expected_etag)}
        )
        try:
            response = self._client.put_object(
                **self._request(
                    Key=name,
                    Body=data,
                    ContentLength=len(data),
                    ContentType="application/json",
                    Metadata=(
                        _metadata_from_model(object_metadata) if object_metadata is not None else {}
                    ),
                    **condition,
                )
            )
            return _checked_etag(response.get("ETag"))
        except Exception as error:
            if _is_conditional_write_conflict(error):
                raise GraphStoreConflictError("The S3 graph-store precondition failed.") from error
            if isinstance(error, GraphStoreError):
                raise
            raise GraphStoreError("The S3 graph-store write failed.") from error

    def _delete_object(self, name: str, etag: str) -> None:
        try:
            self._client.delete_object(**self._request(Key=name, IfMatch=_checked_etag(etag)))
        except Exception as error:
            if _is_conditional_delete_conflict(error):
                raise GraphStoreConflictError(
                    "The S3 graph-store delete precondition failed."
                ) from error
            raise GraphStoreError("The S3 graph-store delete failed.") from error

    def _list_entries(
        self,
        *,
        prefix: str,
        start_after: str | None,
        max_keys: int,
    ) -> tuple[builtins.list[Mapping[str, object]], bool]:
        request: dict[str, object] = {"Prefix": prefix, "MaxKeys": max_keys}
        if start_after is not None:
            request["StartAfter"] = start_after
        try:
            response = self._client.list_objects_v2(**self._request(**request))
        except Exception as error:
            raise GraphStoreError("The S3 graph-store list failed.") from error
        contents = response.get("Contents", [])
        truncated = response.get("IsTruncated", False)
        if not isinstance(contents, list) or not isinstance(truncated, bool):
            raise GraphStoreCorruptionError("The S3 graph-store page is invalid.")
        if not all(isinstance(entry, Mapping) for entry in contents):
            raise GraphStoreCorruptionError("The S3 graph-store page is invalid.")
        return list(contents), truncated

    def _request(self, **values: object) -> dict[str, object]:
        request: dict[str, object] = {"Bucket": self._bucket_name, **values}
        if self._expected_bucket_owner is not None:
            request["ExpectedBucketOwner"] = self._expected_bucket_owner
        return request

    def _graph_prefix(self, project: str) -> str:
        return f"{self._prefix}/projects/{project}/graphs/"

    def _graph_name(self, project: str, graph: str | None) -> str:
        if graph is None:
            return self._graph_prefix(project)
        return f"{self._graph_prefix(project)}{graph}.json"

    def _graph_from_name(self, project: str, name: str) -> str:
        prefix = self._graph_prefix(project)
        if not name.startswith(prefix) or not name.endswith(".json"):
            raise GraphStoreCorruptionError("The S3 graph-store object layout is invalid.")
        graph = name[len(prefix) : -5]
        return _validated_identifier(graph, "graph")

    def _journal_name(
        self,
        project: str,
        operation: Literal["create", "delete"],
        key_sha256: str,
    ) -> str:
        return f"{self._prefix}/idempotency/{project}/{operation}/{key_sha256}.json"

    def _checkpoint(self, stage: str) -> None:
        """Test seam invoked after each durable object mutation boundary."""


def _metadata_from_model(model: BaseModel) -> dict[str, str]:
    raw = model.model_dump(mode="json", exclude_none=True)
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw.items()):
        raise GraphStoreCorruptionError("The S3 graph-store metadata is invalid.")
    return cast("dict[str, str]", raw)


def _checked_etag(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise GraphStoreCorruptionError("An S3 object has an invalid ETag.")
    return value


def _error_code(error: BaseException) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    details = response.get("Error")
    if not isinstance(details, Mapping):
        return None
    code = details.get("Code")
    return code if isinstance(code, str) else None


def _error_status(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return None
    status = metadata.get("HTTPStatusCode")
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _is_read_not_found(error: BaseException) -> bool:
    code = _error_code(error)
    if code is not None:
        return code in {"404", "NoSuchKey", "NotFound"}
    return _error_status(error) == 404


def _is_conditional_read_conflict(error: BaseException) -> bool:
    code = _error_code(error)
    if code is not None:
        return code in {
            "404",
            "409",
            "412",
            "ConditionalRequestConflict",
            "NoSuchKey",
            "NotFound",
            "PreconditionFailed",
        }
    return _error_status(error) in {404, 409, 412}


def _is_conditional_write_conflict(error: BaseException) -> bool:
    return _is_conditional_read_conflict(error)


def _is_conditional_delete_conflict(error: BaseException) -> bool:
    return _is_conditional_read_conflict(error)
