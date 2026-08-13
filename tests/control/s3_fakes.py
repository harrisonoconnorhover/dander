"""Small ETag-aware S3 fake used only by GraphStore contract tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class FakeS3Error(RuntimeError):
    """S3-shaped error whose private detail must never escape the adapter."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__("fake detail must not escape")
        self.response = {
            "Error": {"Code": code, "Message": "fake detail must not escape"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


@dataclass
class _Object:
    data: bytes
    etag: str
    content_type: str
    metadata: dict[str, str]


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0
        self.closed = False
        self.read_amounts: list[int | None] = []

    def read(self, amount: int | None = None) -> bytes:
        self.read_amounts.append(amount)
        if amount is None:
            amount = len(self._data) - self._offset
        start = self._offset
        self._offset = min(len(self._data), self._offset + amount)
        return self._data[start : self._offset]

    def close(self) -> None:
        self.closed = True


class FakeS3Backend:
    def __init__(self) -> None:
        self.objects: dict[str, _Object] = {}
        self.next_etag = 1
        self.put_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.bodies: list[FakeBody] = []
        self.before_put: Callable[[str, str | None, str | None, bytes], None] | None = None
        self.next_put_error: FakeS3Error | None = None
        self.next_get_error: FakeS3Error | None = None
        self.next_delete_error: FakeS3Error | None = None
        self._lock = threading.RLock()

    def new_etag(self) -> str:
        etag = f'"fake-etag-{self.next_etag:08d}"'
        self.next_etag += 1
        return etag


class FakeS3Client:
    def __init__(self, backend: FakeS3Backend | None = None) -> None:
        self.backend = backend or FakeS3Backend()

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        self.backend.put_calls.append(dict(kwargs))
        key = _required_str(kwargs, "Key")
        body = kwargs.get("Body")
        metadata = kwargs.get("Metadata")
        if not isinstance(body, bytes) or not isinstance(metadata, dict):
            raise AssertionError("invalid fake S3 put")
        if not all(
            isinstance(name, str) and isinstance(value, str) for name, value in metadata.items()
        ):
            raise AssertionError("invalid fake S3 metadata")
        if_none_match = kwargs.get("IfNoneMatch")
        if_match = kwargs.get("IfMatch")
        if if_none_match is not None and not isinstance(if_none_match, str):
            raise AssertionError("invalid fake S3 create condition")
        if if_match is not None and not isinstance(if_match, str):
            raise AssertionError("invalid fake S3 update condition")
        if self.backend.before_put is not None:
            self.backend.before_put(key, if_none_match, if_match, body)
        with self.backend._lock:
            if self.backend.next_put_error is not None:
                error = self.backend.next_put_error
                self.backend.next_put_error = None
                raise error
            current = self.backend.objects.get(key)
            if if_none_match == "*":
                if current is not None:
                    raise FakeS3Error("PreconditionFailed", 412)
            elif if_match is not None:
                if current is None:
                    raise FakeS3Error("NoSuchKey", 404)
                if current.etag != if_match:
                    raise FakeS3Error("PreconditionFailed", 412)
            else:
                raise AssertionError("fake S3 writes must be conditional")
            etag = self.backend.new_etag()
            self.backend.objects[key] = _Object(
                data=body,
                etag=etag,
                content_type=_required_str(kwargs, "ContentType"),
                metadata=dict(metadata),
            )
            return {"ETag": etag}

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        self.backend.head_calls.append(dict(kwargs))
        key = _required_str(kwargs, "Key")
        with self.backend._lock:
            item = self.backend.objects.get(key)
            if item is None:
                raise FakeS3Error("NoSuchKey", 404)
            return {
                "ContentLength": len(item.data),
                "ContentType": item.content_type,
                "ETag": item.etag,
                "Metadata": dict(item.metadata),
            }

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        self.backend.get_calls.append(dict(kwargs))
        key = _required_str(kwargs, "Key")
        with self.backend._lock:
            if self.backend.next_get_error is not None:
                error = self.backend.next_get_error
                self.backend.next_get_error = None
                raise error
            item = self.backend.objects.get(key)
            if item is None:
                raise FakeS3Error("NoSuchKey", 404)
            if_match = _required_str(kwargs, "IfMatch")
            if item.etag != if_match:
                raise FakeS3Error("PreconditionFailed", 412)
            data = _range(item.data, kwargs.get("Range"))
            body = FakeBody(data)
            self.backend.bodies.append(body)
            return {
                "Body": body,
                "ContentLength": len(data),
                "ContentType": item.content_type,
                "ETag": item.etag,
                "Metadata": dict(item.metadata),
            }

    def delete_object(self, **kwargs: object) -> Mapping[str, object]:
        self.backend.delete_calls.append(dict(kwargs))
        key = _required_str(kwargs, "Key")
        with self.backend._lock:
            if self.backend.next_delete_error is not None:
                error = self.backend.next_delete_error
                self.backend.next_delete_error = None
                raise error
            item = self.backend.objects.get(key)
            if item is None:
                raise FakeS3Error("NoSuchKey", 404)
            if item.etag != _required_str(kwargs, "IfMatch"):
                raise FakeS3Error("PreconditionFailed", 412)
            del self.backend.objects[key]
            return {}

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        self.backend.list_calls.append(dict(kwargs))
        prefix = _required_str(kwargs, "Prefix")
        start_after = kwargs.get("StartAfter")
        if start_after is not None and not isinstance(start_after, str):
            raise AssertionError("invalid fake S3 StartAfter")
        max_keys = kwargs.get("MaxKeys")
        if isinstance(max_keys, bool) or not isinstance(max_keys, int):
            raise AssertionError("invalid fake S3 MaxKeys")
        with self.backend._lock:
            names = sorted(
                name
                for name in self.backend.objects
                if name.startswith(prefix) and (start_after is None or name > start_after)
            )
            selected = names[:max_keys]
            return {
                "Contents": [
                    {
                        "Key": name,
                        "ETag": self.backend.objects[name].etag,
                        "Size": len(self.backend.objects[name].data),
                    }
                    for name in selected
                ],
                "IsTruncated": len(names) > len(selected),
                "KeyCount": len(selected),
            }


def _required_str(values: Mapping[str, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str):
        raise AssertionError(f"missing fake S3 {name}")
    return value


def _range(data: bytes, value: object) -> bytes:
    if not isinstance(value, str) or not value.startswith("bytes=0-"):
        raise AssertionError("invalid fake S3 range")
    try:
        end = int(value.removeprefix("bytes=0-"))
    except ValueError as error:
        raise AssertionError("invalid fake S3 range") from error
    return data[: end + 1]
