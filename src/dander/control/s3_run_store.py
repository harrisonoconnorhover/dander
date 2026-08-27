"""Amazon S3 implementation of Control's conditional ``RunStore`` contract."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    import builtins

from dander.control.orchestration import (
    AttemptRecord,
    HostedRunState,
    RunClaim,
    RunRecord,
    RunStoreConflictError,
    RunStoreCorruptionError,
    RunStoreError,
    RunStoreIdempotencyConflictError,
    StoredRun,
    StoredRunPage,
)
from dander.control.orchestration_serialization import (
    OrchestrationSerializationError,
    deserialize_attempt_record,
    deserialize_run_record,
    serialize_attempt_record,
    serialize_run_record,
)

_RUN_IDEMPOTENCY_SCHEMA = "io.dander.control.run-idempotency/v1"
_MUTATION_IDEMPOTENCY_SCHEMA = "io.dander.control.mutation-idempotency/v1"
_MAX_RUN_BYTES = 256 * 1024
_MAX_ATTEMPT_BYTES = 128 * 1024
_MAX_IDEMPOTENCY_BYTES = _MAX_RUN_BYTES + 64 * 1024
_MAX_MUTATION_BYTES = 16 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,510}[A-Za-z0-9]$")
_GENERAL_PURPOSE_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_EXPECTED_OWNER = re.compile(r"^[0-9]{12}$")
_PORTABLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _BodyPort(Protocol):
    def read(self, amount: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class _ClientPort(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class _ObjectHead:
    etag: str
    size: int


@dataclass(frozen=True, slots=True)
class _IdempotencyEntry:
    environment: str
    project: str
    key_sha256: str
    run_id: str
    submission_sha256: str
    initial_record: RunRecord

    def serialize(self) -> bytes:
        return _canonical_json(
            {
                "schema": _RUN_IDEMPOTENCY_SCHEMA,
                "environment": self.environment,
                "project": self.project,
                "key_sha256": self.key_sha256,
                "run_id": self.run_id,
                "submission_sha256": self.submission_sha256,
                "initial_record": json.loads(serialize_run_record(self.initial_record)),
            }
        )

    @classmethod
    def deserialize(cls, data: bytes) -> _IdempotencyEntry:
        try:
            if not data or len(data) > _MAX_IDEMPOTENCY_BYTES:
                raise RunStoreCorruptionError("An S3 run idempotency object exceeds its bound.")
            raw = json.loads(data)
            values = _mapping(raw)
            if values.get("schema") != _RUN_IDEMPOTENCY_SCHEMA:
                raise RunStoreCorruptionError("An S3 run idempotency object is invalid.")
            initial = values["initial_record"]
            entry = cls(
                environment=_checked_portable(values["environment"], "environment"),
                project=_checked_portable(values["project"], "project"),
                key_sha256=_checked_sha256(values["key_sha256"], "idempotency key"),
                run_id=_checked_opaque(values["run_id"], "run"),
                submission_sha256=_checked_sha256(values["submission_sha256"], "submission"),
                initial_record=deserialize_run_record(_canonical_json(initial)),
            )
            if (
                entry.initial_record.environment != entry.environment
                or entry.initial_record.project != entry.project
                or entry.initial_record.idempotency_key_sha256 != entry.key_sha256
                or entry.initial_record.run_id != entry.run_id
                or entry.initial_record.submission_sha256 != entry.submission_sha256
            ):
                raise RunStoreCorruptionError("An S3 run idempotency object is inconsistent.")
            expected = _canonical_json(
                {
                    "schema": _RUN_IDEMPOTENCY_SCHEMA,
                    "environment": entry.environment,
                    "project": entry.project,
                    "key_sha256": entry.key_sha256,
                    "run_id": entry.run_id,
                    "submission_sha256": entry.submission_sha256,
                    "initial_record": initial,
                }
            )
            if data != expected:
                raise RunStoreCorruptionError("An S3 run idempotency object is not canonical.")
            return entry
        except RunStoreCorruptionError:
            raise
        except (KeyError, TypeError, ValueError, OrchestrationSerializationError) as error:
            raise RunStoreCorruptionError("An S3 run idempotency object is invalid.") from error


@dataclass(frozen=True, slots=True)
class _MutationEntry:
    key_sha256: str
    operation: str
    run_id: str
    result: Mapping[str, object]

    def serialize(self) -> bytes:
        return _canonical_json(
            {
                "schema": _MUTATION_IDEMPOTENCY_SCHEMA,
                "key_sha256": self.key_sha256,
                "operation": self.operation,
                "run_id": self.run_id,
                "result": self.result,
            }
        )

    @classmethod
    def deserialize(cls, data: bytes) -> _MutationEntry:
        try:
            if not data or len(data) > _MAX_MUTATION_BYTES:
                raise RunStoreCorruptionError(
                    "An S3 mutation idempotency object exceeds its bound."
                )
            values = _mapping(json.loads(data))
            if values.get("schema") != _MUTATION_IDEMPOTENCY_SCHEMA:
                raise RunStoreCorruptionError("An S3 mutation idempotency object is invalid.")
            entry = cls(
                key_sha256=_checked_sha256(values["key_sha256"], "mutation key"),
                operation=_checked_portable(values["operation"], "mutation operation"),
                run_id=_checked_opaque(values["run_id"], "run"),
                result=_mapping(values["result"]),
            )
            if data != entry.serialize():
                raise RunStoreCorruptionError("An S3 mutation idempotency object is not canonical.")
            return entry
        except RunStoreCorruptionError:
            raise
        except (KeyError, TypeError, UnicodeDecodeError, ValueError) as error:
            raise RunStoreCorruptionError(
                "An S3 mutation idempotency object is invalid."
            ) from error


class S3RunStore:
    """Persist conditional run snapshots, lookup claims, and immutable attempts in S3."""

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "dander-control/v1",
        client: _ClientPort | None = None,
        expected_bucket_owner: str | None = None,
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
            raise RunStoreCorruptionError(
                "The S3 run-store binding must name a general-purpose bucket."
            )
        prefix = prefix.strip("/")
        if (
            not prefix
            or _PREFIX.fullmatch(prefix) is None
            or any(part in {"", ".", ".."} for part in prefix.split("/"))
        ):
            raise RunStoreCorruptionError("The S3 run-store prefix binding is invalid.")
        if expected_bucket_owner is not None and (
            not isinstance(expected_bucket_owner, str)
            or _EXPECTED_OWNER.fullmatch(expected_bucket_owner) is None
        ):
            raise RunStoreCorruptionError("The S3 run-store owner binding is invalid.")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise RunStoreCorruptionError("The S3 run-store timeout is invalid.")
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

    @property
    def bucket_name(self) -> str:
        """Return the immutable bucket binding."""
        return self._bucket_name

    @property
    def prefix(self) -> str:
        """Return the immutable object-prefix binding."""
        return self._prefix

    def claim(self, record: RunRecord) -> RunClaim:
        """Claim one scoped idempotency key and repair an interrupted snapshot creation."""
        self._validate_initial_record(record)
        entry = _IdempotencyEntry(
            environment=record.environment,
            project=record.project,
            key_sha256=record.idempotency_key_sha256,
            run_id=record.run_id,
            submission_sha256=record.submission_sha256,
            initial_record=record,
        )
        idempotency_name = self._idempotency_name(
            record.environment,
            record.project,
            record.idempotency_key_sha256,
        )
        try:
            self._write_object(idempotency_name, entry.serialize(), expected_etag=None)
            created = True
            self._checkpoint("after_idempotency_claim")
        except RunStoreConflictError:
            loaded = self._read_object(idempotency_name, _MAX_IDEMPOTENCY_BYTES)
            if loaded is None:
                raise RunStoreConflictError(
                    "The S3 run idempotency claim did not converge."
                ) from None
            entry = _IdempotencyEntry.deserialize(loaded[0])
            self._validate_replay(entry, record)
            created = False

        existing = self.get(entry.run_id)
        if existing is not None:
            self._validate_snapshot_replay(entry, existing.record)
            return RunClaim(stored=existing, created=created)

        try:
            revision = self._write_object(
                self._run_name(entry.run_id),
                serialize_run_record(entry.initial_record),
                expected_etag=None,
            )
            self._checkpoint("after_run_snapshot_create")
            return RunClaim(
                stored=StoredRun(record=entry.initial_record, revision=revision),
                created=created,
            )
        except RunStoreConflictError:
            existing = self.get(entry.run_id)
            if existing is None:
                raise RunStoreConflictError(
                    "The S3 run snapshot create did not converge."
                ) from None
            self._validate_snapshot_replay(entry, existing.record)
            return RunClaim(stored=existing, created=False)

    def get(self, run_id: str) -> StoredRun | None:
        """Read one pinned canonical run snapshot."""
        run_id = _checked_opaque(run_id, "run")
        loaded = self._read_object(self._run_name(run_id), _MAX_RUN_BYTES)
        if loaded is None:
            return None
        try:
            record = deserialize_run_record(loaded[0])
        except OrchestrationSerializationError as error:
            raise RunStoreCorruptionError("An S3 run snapshot is invalid.") from error
        if record.run_id != run_id:
            raise RunStoreCorruptionError("An S3 run snapshot is addressed incorrectly.")
        return StoredRun(record=record, revision=loaded[1])

    def find_idempotency(
        self,
        *,
        environment: str,
        project: str,
        idempotency_key_sha256: str,
    ) -> StoredRun | None:
        """Resolve a durable scoped idempotency claim and repair its missing snapshot."""
        environment = _checked_portable(environment, "environment")
        project = _checked_portable(project, "project")
        key_sha256 = _checked_sha256(idempotency_key_sha256, "idempotency key")
        loaded = self._read_object(
            self._idempotency_name(environment, project, key_sha256),
            _MAX_IDEMPOTENCY_BYTES,
        )
        if loaded is None:
            return None
        entry = _IdempotencyEntry.deserialize(loaded[0])
        if (
            entry.environment != environment
            or entry.project != project
            or entry.key_sha256 != key_sha256
        ):
            raise RunStoreCorruptionError("An S3 run idempotency object is addressed incorrectly.")
        existing = self.get(entry.run_id)
        if existing is not None:
            self._validate_snapshot_replay(entry, existing.record)
            return existing
        try:
            revision = self._write_object(
                self._run_name(entry.run_id),
                serialize_run_record(entry.initial_record),
                expected_etag=None,
            )
            self._checkpoint("after_run_snapshot_recovery")
            return StoredRun(record=entry.initial_record, revision=revision)
        except RunStoreConflictError:
            existing = self.get(entry.run_id)
            if existing is None:
                raise RunStoreConflictError(
                    "The S3 run snapshot recovery did not converge."
                ) from None
            self._validate_snapshot_replay(entry, existing.record)
            return existing

    def save(self, stored: StoredRun, record: RunRecord) -> StoredRun:
        """Replace one run snapshot only at the caller's opaque S3 revision."""
        if record.run_id != stored.record.run_id or not _same_run_identity(stored.record, record):
            raise RunStoreConflictError("A run snapshot cannot change its durable identity.")
        revision = self._write_object(
            self._run_name(record.run_id),
            serialize_run_record(record),
            expected_etag=stored.revision,
        )
        self._checkpoint("after_run_snapshot_save")
        return StoredRun(record=record, revision=revision)

    def append_attempt(self, attempt: AttemptRecord) -> None:
        """Create one immutable attempt record, allowing only byte-identical replay."""
        name = self._attempt_name(attempt.run_id, attempt.attempt_id)
        data = serialize_attempt_record(attempt)
        try:
            self._write_object(name, data, expected_etag=None)
            self._checkpoint("after_attempt_append")
            return
        except RunStoreConflictError:
            loaded = self._read_object(name, _MAX_ATTEMPT_BYTES)
            if loaded is None:
                raise RunStoreConflictError("The S3 attempt append did not converge.") from None
            try:
                existing = deserialize_attempt_record(loaded[0])
            except OrchestrationSerializationError as error:
                raise RunStoreCorruptionError("An S3 attempt record is invalid.") from error
            if serialize_attempt_record(existing) != data:
                raise RunStoreConflictError(
                    "The attempt identity already contains different immutable input."
                ) from None

    def claim_mutation(
        self,
        *,
        key_sha256: str,
        operation: str,
        run_id: str,
        result: bytes,
    ) -> bytes:
        """Durably claim one mutation key and replay its original normalized result."""
        key_sha256 = _checked_sha256(key_sha256, "mutation key")
        operation = _checked_portable(operation, "mutation operation")
        run_id = _checked_opaque(run_id, "run")
        try:
            result_value = _mapping(json.loads(result))
        except (TypeError, UnicodeDecodeError, ValueError) as error:
            raise RunStoreCorruptionError("The mutation result is invalid.") from error
        entry = _MutationEntry(
            key_sha256=key_sha256,
            operation=operation,
            run_id=run_id,
            result=result_value,
        )
        data = entry.serialize()
        if len(data) > _MAX_MUTATION_BYTES:
            raise RunStoreCorruptionError("The mutation result exceeds its durable bound.")
        name = self._mutation_name(key_sha256)
        try:
            self._write_object(name, data, expected_etag=None)
            self._checkpoint("after_mutation_claim")
            return _canonical_json(entry.result)
        except RunStoreConflictError:
            loaded = self._read_object(name, _MAX_MUTATION_BYTES)
            if loaded is None:
                raise RunStoreConflictError(
                    "The S3 mutation idempotency claim did not converge."
                ) from None
            existing = _MutationEntry.deserialize(loaded[0])
            if (
                existing.key_sha256 != key_sha256
                or existing.operation != operation
                or existing.run_id != run_id
            ):
                raise RunStoreIdempotencyConflictError(
                    "The mutation idempotency key belongs to a different operation."
                ) from None
            return _canonical_json(existing.result)

    def list(self, *, cursor: str | None, limit: int) -> StoredRunPage:
        """Return a bounded run page ordered by deterministic snapshot object key."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise RunStoreCorruptionError("The S3 run-store page size is invalid.")
        run_prefix = self._run_prefix()
        start_after = _decode_cursor(cursor, run_prefix) if cursor is not None else None
        entries, truncated = self._list_entries(
            prefix=run_prefix,
            start_after=start_after,
            max_keys=limit + 1,
        )
        if truncated and len(entries) <= limit:
            raise RunStoreCorruptionError("The S3 run-store page is invalid.")
        selected = entries[:limit]
        items: list[StoredRun] = []
        for entry in selected:
            name = entry.get("Key")
            if (
                not isinstance(name, str)
                or not name.startswith(run_prefix)
                or not name.endswith(".json")
            ):
                raise RunStoreCorruptionError("The S3 run-store object layout is invalid.")
            run_id = _checked_opaque(name[len(run_prefix) : -5], "run")
            stored = self.get(run_id)
            if stored is None:
                raise RunStoreConflictError("An S3 run snapshot disappeared during pagination.")
            items.append(stored)
        has_more = len(entries) > limit or truncated
        next_cursor = _encode_cursor(selected[-1]["Key"]) if items and has_more else None
        return StoredRunPage(items=tuple(items), next_cursor=next_cursor)

    def close(self) -> None:
        """Release no resource; the injected or SDK-managed S3 client owns its lifecycle."""

    def _read_object(self, name: str, max_bytes: int) -> tuple[bytes, str] | None:
        for _ in range(3):
            head = self._head_object(name)
            if head is None:
                return None
            if head.size > max_bytes:
                raise RunStoreCorruptionError("An S3 run-store object exceeds its bound.")
            try:
                response = self._client.get_object(
                    **self._request(
                        Key=name,
                        IfMatch=head.etag,
                        Range=f"bytes=0-{max_bytes}",
                    )
                )
            except Exception as error:
                if _is_conditional_conflict(error):
                    continue
                raise RunStoreError("The S3 run-store read failed.") from error
            response_etag = _checked_etag(response.get("ETag"))
            if response_etag != head.etag:
                continue
            body = response.get("Body")
            if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
                raise RunStoreCorruptionError("The S3 run-store body is invalid.")
            stream = cast("_BodyPort", body)
            try:
                data = stream.read(max_bytes + 1)
                extra = stream.read(1)
            except Exception as error:
                raise RunStoreError("The S3 run-store body read failed.") from error
            finally:
                try:
                    stream.close()
                except Exception as error:
                    raise RunStoreError("The S3 run-store body close failed.") from error
            if not isinstance(data, bytes) or not isinstance(extra, bytes):
                raise RunStoreCorruptionError("The S3 run-store body is invalid.")
            if len(data) > max_bytes or extra:
                raise RunStoreCorruptionError("An S3 run-store object exceeds its bound.")
            return data, head.etag
        raise RunStoreConflictError("The S3 run-store object changed during the read.")

    def _head_object(self, name: str) -> _ObjectHead | None:
        try:
            response = self._client.head_object(**self._request(Key=name))
        except Exception as error:
            if _is_not_found(error):
                return None
            raise RunStoreError("The S3 run-store metadata read failed.") from error
        etag = _checked_etag(response.get("ETag"))
        size = response.get("ContentLength")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RunStoreCorruptionError("The S3 run-store object size is invalid.")
        return _ObjectHead(etag=etag, size=size)

    def _write_object(self, name: str, data: bytes, *, expected_etag: str | None) -> str:
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
                    Metadata={},
                    **condition,
                )
            )
            return _checked_etag(response.get("ETag"))
        except Exception as error:
            if _is_conditional_conflict(error):
                raise RunStoreConflictError("The S3 run-store precondition failed.") from error
            if isinstance(error, RunStoreError):
                raise
            raise RunStoreError("The S3 run-store write failed.") from error

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
            raise RunStoreError("The S3 run-store list failed.") from error
        contents = response.get("Contents", [])
        truncated = response.get("IsTruncated", False)
        if not isinstance(contents, list) or not isinstance(truncated, bool):
            raise RunStoreCorruptionError("The S3 run-store page is invalid.")
        if not all(isinstance(entry, Mapping) for entry in contents):
            raise RunStoreCorruptionError("The S3 run-store page is invalid.")
        return cast("builtins.list[Mapping[str, object]]", contents), truncated

    def _request(self, **values: object) -> dict[str, object]:
        request: dict[str, object] = {"Bucket": self._bucket_name, **values}
        if self._expected_bucket_owner is not None:
            request["ExpectedBucketOwner"] = self._expected_bucket_owner
        return request

    def _run_prefix(self) -> str:
        return f"{self._prefix}/runs/"

    def _run_name(self, run_id: str) -> str:
        return f"{self._run_prefix()}{run_id}.json"

    def _attempt_name(self, run_id: str, attempt_id: str) -> str:
        run_id = _checked_opaque(run_id, "run")
        attempt_id = _checked_opaque(attempt_id, "attempt")
        return f"{self._prefix}/attempts/{run_id}/{attempt_id}.json"

    def _idempotency_name(self, environment: str, project: str, key_sha256: str) -> str:
        environment = _checked_portable(environment, "environment")
        project = _checked_portable(project, "project")
        key_sha256 = _checked_sha256(key_sha256, "idempotency key")
        return f"{self._prefix}/idempotency/runs/{environment}/{project}/{key_sha256}.json"

    def _mutation_name(self, key_sha256: str) -> str:
        key_sha256 = _checked_sha256(key_sha256, "mutation key")
        return f"{self._prefix}/idempotency/mutations/{key_sha256}.json"

    def _checkpoint(self, stage: str) -> None:
        """Test seam invoked after each durable object mutation boundary."""

    @staticmethod
    def _validate_initial_record(record: RunRecord) -> None:
        if (
            record.run_state is not HostedRunState.QUEUED
            or record.attempt_count != 0
            or record.backend_handle is not None
        ):
            raise RunStoreConflictError("A run claim must contain a pristine queued snapshot.")

    @staticmethod
    def _validate_replay(entry: _IdempotencyEntry, record: RunRecord) -> None:
        if (
            entry.environment != record.environment
            or entry.project != record.project
            or entry.key_sha256 != record.idempotency_key_sha256
            or entry.run_id != record.run_id
        ):
            raise RunStoreCorruptionError("An S3 run idempotency object is addressed incorrectly.")
        if entry.submission_sha256 != record.submission_sha256:
            raise RunStoreIdempotencyConflictError(
                "The idempotency key belongs to a different logical submission."
            )

    @staticmethod
    def _validate_snapshot_replay(entry: _IdempotencyEntry, record: RunRecord) -> None:
        if (
            entry.run_id != record.run_id
            or entry.environment != record.environment
            or entry.project != record.project
            or entry.key_sha256 != record.idempotency_key_sha256
            or entry.submission_sha256 != record.submission_sha256
        ):
            raise RunStoreCorruptionError("An S3 run snapshot contradicts its idempotency claim.")


def _same_run_identity(first: RunRecord, second: RunRecord) -> bool:
    return (
        first.run_id,
        first.environment,
        first.project,
        first.graph,
        first.graph_revision,
        first.graph_content_sha256,
        first.plan_id,
        first.plan_revision,
        first.trigger,
        first.idempotency_key_sha256,
        first.submission_sha256,
        first.requested_at,
        first.requested_deadline_seconds,
        first.created_at,
    ) == (
        second.run_id,
        second.environment,
        second.project,
        second.graph,
        second.graph_revision,
        second.graph_content_sha256,
        second.plan_id,
        second.plan_revision,
        second.trigger,
        second.idempotency_key_sha256,
        second.submission_sha256,
        second.requested_at,
        second.requested_deadline_seconds,
        second.created_at,
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise RunStoreCorruptionError("An S3 run-store object is invalid.")
    return cast("Mapping[str, object]", value)


def _checked_portable(value: object, label: str) -> str:
    if not isinstance(value, str) or _PORTABLE_ID.fullmatch(value) is None:
        raise RunStoreCorruptionError(f"The S3 run-store {label} identifier is invalid.")
    return value


def _checked_opaque(value: object, label: str) -> str:
    if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
        raise RunStoreCorruptionError(f"The S3 run-store {label} identifier is invalid.")
    return value


def _checked_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RunStoreCorruptionError(f"The S3 run-store {label} identity is invalid.")
    return value


def _checked_etag(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\n" in value:
        raise RunStoreCorruptionError("An S3 run-store object has an invalid ETag.")
    return value


def _encode_cursor(name: object) -> str:
    if not isinstance(name, str):
        raise RunStoreCorruptionError("The S3 run-store page is invalid.")
    return base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str, prefix: str) -> str:
    if not isinstance(cursor, str) or not cursor or len(cursor) > 1024:
        raise RunStoreCorruptionError("The S3 run-store cursor is invalid.")
    try:
        name = base64.b64decode(cursor, altchars=b"-_", validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise RunStoreCorruptionError("The S3 run-store cursor is invalid.") from error
    if not name.startswith(prefix) or not name.endswith(".json"):
        raise RunStoreCorruptionError("The S3 run-store cursor is invalid.")
    _checked_opaque(name[len(prefix) : -5], "run")
    return name


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


def _is_not_found(error: BaseException) -> bool:
    code = _error_code(error)
    if code is not None:
        return code in {"404", "NoSuchKey", "NotFound"}
    return _error_status(error) == 404


def _is_conditional_conflict(error: BaseException) -> bool:
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


__all__ = ["S3RunStore"]
