"""Small version-aware OCI Object Storage fake for GraphStore contract tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class FakeOCIServiceError(RuntimeError):
    """OCI-shaped service error whose private details must not escape the adapter."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__("fake detail must not escape")
        self.status = status
        self.code = code
        self.message = "fake detail must not escape"


@dataclass
class _Object:
    data: bytes
    etag: str
    content_type: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class _Version:
    etag: str
    deleted: bool


@dataclass(frozen=True)
class FakeObjectSummary:
    name: str


@dataclass(frozen=True)
class FakeListData:
    objects: list[FakeObjectSummary]
    next_start_with: str | None


@dataclass(frozen=True)
class FakeResponse:
    headers: dict[str, str]
    data: object = None


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


class FakeStreamResponse:
    def __init__(self, data: bytes) -> None:
        self.raw = FakeBody(data)

    def close(self) -> None:
        self.raw.close()


class FakeOCIBackend:
    def __init__(self) -> None:
        self.objects: dict[str, _Object] = {}
        self.versions: dict[str, list[_Version]] = {}
        self.next_etag = 1
        self.put_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.bodies: list[FakeBody] = []
        self.before_put: Callable[[str, str | None, str | None, bytes], None] | None = None
        self.next_put_error: FakeOCIServiceError | None = None
        self.next_head_error: FakeOCIServiceError | None = None
        self.next_get_error: FakeOCIServiceError | None = None
        self.next_delete_error: FakeOCIServiceError | None = None
        self.next_list_error: FakeOCIServiceError | None = None
        self.page_size_override: int | None = None
        self._lock = threading.RLock()

    def new_etag(self) -> str:
        etag = f"fake-etag-{self.next_etag:08d}"
        self.next_etag += 1
        return etag


class FakeOCIObjectStorageClient:
    def __init__(self, backend: FakeOCIBackend | None = None) -> None:
        self.backend = backend or FakeOCIBackend()

    def put_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        put_object_body: bytes,
        **kwargs: object,
    ) -> FakeResponse:
        call = _call(namespace_name, bucket_name, object_name, kwargs)
        self.backend.put_calls.append(call)
        metadata = kwargs.get("opc_meta")
        if not isinstance(put_object_body, bytes) or not isinstance(metadata, dict):
            raise AssertionError("invalid fake OCI put")
        if not all(
            isinstance(name, str) and isinstance(value, str) for name, value in metadata.items()
        ):
            raise AssertionError("invalid fake OCI metadata")
        if_none_match = kwargs.get("if_none_match")
        if_match = kwargs.get("if_match")
        if if_none_match is not None and not isinstance(if_none_match, str):
            raise AssertionError("invalid fake OCI create condition")
        if if_match is not None and not isinstance(if_match, str):
            raise AssertionError("invalid fake OCI update condition")
        if self.backend.before_put is not None:
            self.backend.before_put(object_name, if_none_match, if_match, put_object_body)
        with self.backend._lock:
            if self.backend.next_put_error is not None:
                error = self.backend.next_put_error
                self.backend.next_put_error = None
                raise error
            current = self.backend.objects.get(object_name)
            if if_none_match == "*":
                if current is not None:
                    raise FakeOCIServiceError(412, "NoEtagMatch")
            elif if_match is not None:
                if current is None:
                    raise FakeOCIServiceError(404, "NotAuthorizedOrNotFound")
                if current.etag != if_match:
                    raise FakeOCIServiceError(412, "NoEtagMatch")
            else:
                raise AssertionError("fake OCI writes must be conditional")
            etag = self.backend.new_etag()
            self.backend.objects[object_name] = _Object(
                data=put_object_body,
                etag=etag,
                content_type=_required_str(kwargs, "content_type"),
                metadata=dict(metadata),
            )
            self.backend.versions.setdefault(object_name, []).append(
                _Version(etag=etag, deleted=False)
            )
            return FakeResponse(headers={"etag": etag})

    def head_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        self.backend.head_calls.append(_call(namespace_name, bucket_name, object_name, kwargs))
        _reject_version_id(kwargs)
        with self.backend._lock:
            if self.backend.next_head_error is not None:
                error = self.backend.next_head_error
                self.backend.next_head_error = None
                raise error
            item = self.backend.objects.get(object_name)
            if item is None:
                raise FakeOCIServiceError(404, "NotAuthorizedOrNotFound")
            return FakeResponse(headers=_headers(item))

    def get_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        self.backend.get_calls.append(_call(namespace_name, bucket_name, object_name, kwargs))
        _reject_version_id(kwargs)
        with self.backend._lock:
            if self.backend.next_get_error is not None:
                error = self.backend.next_get_error
                self.backend.next_get_error = None
                raise error
            item = self.backend.objects.get(object_name)
            if item is None:
                raise FakeOCIServiceError(404, "NotAuthorizedOrNotFound")
            if item.etag != _required_str(kwargs, "if_match"):
                raise FakeOCIServiceError(412, "NoEtagMatch")
            data = _range(item.data, kwargs.get("range"))
            stream = FakeStreamResponse(data)
            self.backend.bodies.append(stream.raw)
            return FakeResponse(headers=_headers(item, size=len(data)), data=stream)

    def delete_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        self.backend.delete_calls.append(_call(namespace_name, bucket_name, object_name, kwargs))
        _reject_version_id(kwargs)
        with self.backend._lock:
            if self.backend.next_delete_error is not None:
                error = self.backend.next_delete_error
                self.backend.next_delete_error = None
                raise error
            item = self.backend.objects.get(object_name)
            if item is None:
                raise FakeOCIServiceError(404, "NotAuthorizedOrNotFound")
            if item.etag != _required_str(kwargs, "if_match"):
                raise FakeOCIServiceError(412, "NoEtagMatch")
            marker_etag = self.backend.new_etag()
            self.backend.versions.setdefault(object_name, []).append(
                _Version(etag=marker_etag, deleted=True)
            )
            del self.backend.objects[object_name]
            return FakeResponse(headers={"opc-request-id": "fake"})

    def list_objects(
        self,
        namespace_name: str,
        bucket_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        self.backend.list_calls.append(_call(namespace_name, bucket_name, None, kwargs))
        prefix = _required_str(kwargs, "prefix")
        start_after = kwargs.get("start_after")
        start = kwargs.get("start")
        if start_after is not None and not isinstance(start_after, str):
            raise AssertionError("invalid fake OCI start_after")
        if start is not None and not isinstance(start, str):
            raise AssertionError("invalid fake OCI start")
        if start_after is not None and start is not None:
            raise AssertionError("fake OCI list cannot combine start and start_after")
        limit = kwargs.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise AssertionError("invalid fake OCI limit")
        with self.backend._lock:
            if self.backend.next_list_error is not None:
                error = self.backend.next_list_error
                self.backend.next_list_error = None
                raise error
            names = sorted(
                name
                for name in self.backend.objects
                if name.startswith(prefix)
                and (start_after is None or name > start_after)
                and (start is None or name >= start)
            )
            actual_limit = min(limit, self.backend.page_size_override or limit)
            selected = names[:actual_limit]
            next_start = names[len(selected)] if len(names) > len(selected) else None
            return FakeResponse(
                headers={"opc-request-id": "fake"},
                data=FakeListData(
                    objects=[FakeObjectSummary(name=name) for name in selected],
                    next_start_with=next_start,
                ),
            )


def _call(
    namespace_name: str,
    bucket_name: str,
    object_name: str | None,
    kwargs: Mapping[str, object],
) -> dict[str, object]:
    result: dict[str, object] = {
        "namespace_name": namespace_name,
        "bucket_name": bucket_name,
        **kwargs,
    }
    if object_name is not None:
        result["object_name"] = object_name
    return result


def _headers(item: _Object, *, size: int | None = None) -> dict[str, str]:
    return {
        "content-length": str(len(item.data) if size is None else size),
        "content-type": item.content_type,
        "etag": item.etag,
        **{f"opc-meta-{name}": value for name, value in item.metadata.items()},
    }


def _reject_version_id(kwargs: Mapping[str, object]) -> None:
    if "version_id" in kwargs:
        raise AssertionError("the GraphStore must not address historical OCI object versions")


def _required_str(values: Mapping[str, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str):
        raise AssertionError(f"missing fake OCI {name}")
    return value


def _range(data: bytes, value: object) -> bytes:
    if not isinstance(value, str) or not value.startswith("bytes=0-"):
        raise AssertionError("invalid fake OCI range")
    try:
        end = int(value.removeprefix("bytes=0-"))
    except ValueError as error:
        raise AssertionError("invalid fake OCI range") from error
    return data[: end + 1]
