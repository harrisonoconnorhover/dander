"""Small ETag-aware Azure Blob fake used only by GraphStore contract tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from dander.control.azure_blob_graph_store import _BlobClientPort, _PagedPort


class FakeAzureError(RuntimeError):
    """Azure-shaped error whose private detail must never escape the adapter."""

    def __init__(self, error_code: str, status_code: int) -> None:
        super().__init__("fake detail must not escape")
        self.error_code = error_code
        self.status_code = status_code


@dataclass(frozen=True)
class FakeContentSettings:
    content_type: str


@dataclass
class _Object:
    data: bytes
    etag: str
    content_type: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class FakeProperties:
    name: str
    etag: str
    size: int
    metadata: dict[str, str]
    content_settings: FakeContentSettings


class FakeDownloader:
    def __init__(self, data: bytes, properties: FakeProperties) -> None:
        self._data = data
        self.properties = properties
        self.readall_calls = 0

    def readall(self) -> bytes:
        self.readall_calls += 1
        return self._data


class FakeAzureBackend:
    def __init__(self) -> None:
        self.objects: dict[str, _Object] = {}
        self.next_etag = 1
        self.upload_calls: list[dict[str, object]] = []
        self.properties_calls: list[dict[str, object]] = []
        self.download_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.downloaders: list[FakeDownloader] = []
        self.before_upload: Callable[[str, bool, str | None, bytes], None] | None = None
        self.next_upload_error: FakeAzureError | None = None
        self.next_properties_error: FakeAzureError | None = None
        self.next_download_error: FakeAzureError | None = None
        self.next_delete_error: FakeAzureError | None = None
        self.next_list_error: FakeAzureError | None = None
        self.page_size_override: int | None = None
        self.unquote_list_etags = False
        self._lock = threading.RLock()

    def new_etag(self) -> str:
        etag = f'"fake-etag-{self.next_etag:08d}"'
        self.next_etag += 1
        return etag


class FakeBlobClient:
    def __init__(self, backend: FakeAzureBackend, name: str) -> None:
        self._backend = backend
        self._name = name

    def get_blob_properties(self, **kwargs: object) -> FakeProperties:
        self._backend.properties_calls.append({"blob": self._name, **kwargs})
        with self._backend._lock:
            if self._backend.next_properties_error is not None:
                error = self._backend.next_properties_error
                self._backend.next_properties_error = None
                raise error
            item = self._backend.objects.get(self._name)
            if item is None:
                raise FakeAzureError("BlobNotFound", 404)
            return _properties(self._name, item)

    def download_blob(self, **kwargs: object) -> FakeDownloader:
        self._backend.download_calls.append({"blob": self._name, **kwargs})
        with self._backend._lock:
            if self._backend.next_download_error is not None:
                error = self._backend.next_download_error
                self._backend.next_download_error = None
                raise error
            item = self._backend.objects.get(self._name)
            if item is None:
                raise FakeAzureError("BlobNotFound", 404)
            if kwargs.get("match_condition") != "if_not_modified":
                raise AssertionError("fake Azure downloads must use IfNotModified")
            if item.etag != _required_str(kwargs, "etag"):
                raise FakeAzureError("ConditionNotMet", 412)
            if kwargs.get("offset") != 0 or kwargs.get("max_concurrency") != 1:
                raise AssertionError("fake Azure downloads must be bounded and serial")
            length = kwargs.get("length")
            if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
                raise AssertionError("invalid fake Azure download length")
            downloader = FakeDownloader(item.data[:length], _properties(self._name, item))
            self._backend.downloaders.append(downloader)
            return downloader

    def upload_blob(self, data: bytes, **kwargs: object) -> Mapping[str, object]:
        self._backend.upload_calls.append({"blob": self._name, "data": data, **kwargs})
        if not isinstance(data, bytes) or kwargs.get("length") != len(data):
            raise AssertionError("invalid fake Azure upload body")
        metadata = kwargs.get("metadata")
        if not isinstance(metadata, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()
        ):
            raise AssertionError("invalid fake Azure metadata")
        content_settings = kwargs.get("content_settings")
        if getattr(content_settings, "content_type", None) != "application/json":
            raise AssertionError("invalid fake Azure content settings")
        overwrite = kwargs.get("overwrite")
        if not isinstance(overwrite, bool):
            raise AssertionError("fake Azure uploads require an overwrite choice")
        expected_etag = kwargs.get("etag")
        if expected_etag is not None and not isinstance(expected_etag, str):
            raise AssertionError("invalid fake Azure upload ETag")
        if self._backend.before_upload is not None:
            self._backend.before_upload(self._name, overwrite, expected_etag, data)
        with self._backend._lock:
            if self._backend.next_upload_error is not None:
                error = self._backend.next_upload_error
                self._backend.next_upload_error = None
                raise error
            current = self._backend.objects.get(self._name)
            if not overwrite:
                if expected_etag is not None or "match_condition" in kwargs:
                    raise AssertionError("fake Azure creates must rely on overwrite=False")
                if current is not None:
                    raise FakeAzureError("BlobAlreadyExists", 409)
            else:
                if kwargs.get("match_condition") != "if_not_modified":
                    raise AssertionError("fake Azure updates must use IfNotModified")
                if current is None:
                    raise FakeAzureError("BlobNotFound", 404)
                if current.etag != expected_etag:
                    raise FakeAzureError("ConditionNotMet", 412)
            etag = self._backend.new_etag()
            self._backend.objects[self._name] = _Object(
                data=data,
                etag=etag,
                content_type="application/json",
                metadata=dict(metadata),
            )
            return {"etag": etag}

    def delete_blob(self, **kwargs: object) -> None:
        self._backend.delete_calls.append({"blob": self._name, **kwargs})
        if "delete_snapshots" in kwargs or "version_id" in kwargs:
            raise AssertionError("fake Azure deletes must target only the current base blob")
        with self._backend._lock:
            if self._backend.next_delete_error is not None:
                error = self._backend.next_delete_error
                self._backend.next_delete_error = None
                raise error
            current = self._backend.objects.get(self._name)
            if current is None:
                raise FakeAzureError("BlobNotFound", 404)
            if kwargs.get("match_condition") != "if_not_modified":
                raise AssertionError("fake Azure deletes must use IfNotModified")
            if current.etag != _required_str(kwargs, "etag"):
                raise FakeAzureError("ConditionNotMet", 412)
            del self._backend.objects[self._name]


class FakePageIterator:
    def __init__(
        self,
        entries: list[FakeProperties],
        *,
        continuation_token: str | None,
        page_size: int,
    ) -> None:
        self._entries = entries
        self._offset = _offset(continuation_token)
        self._page_size = page_size
        self._returned = False
        self.continuation_token: str | None = continuation_token

    def __iter__(self) -> FakePageIterator:
        return self

    def __next__(self) -> Iterable[FakeProperties]:
        if self._returned or self._offset >= len(self._entries):
            self.continuation_token = None
            raise StopIteration
        self._returned = True
        end = min(len(self._entries), self._offset + self._page_size)
        page = self._entries[self._offset : end]
        self.continuation_token = str(end) if end < len(self._entries) else None
        return page


class FakePaged:
    def __init__(self, entries: list[FakeProperties], page_size: int) -> None:
        self._entries = entries
        self._page_size = page_size

    def by_page(self, continuation_token: str | None = None) -> FakePageIterator:
        return FakePageIterator(
            self._entries,
            continuation_token=continuation_token,
            page_size=self._page_size,
        )


class FakeAzureContainerClient:
    def __init__(self, backend: FakeAzureBackend | None = None) -> None:
        self.backend = backend or FakeAzureBackend()

    def get_blob_client(self, blob: str) -> _BlobClientPort:
        return cast("_BlobClientPort", FakeBlobClient(self.backend, blob))

    def list_blobs(self, **kwargs: object) -> _PagedPort:
        self.backend.list_calls.append(dict(kwargs))
        if self.backend.next_list_error is not None:
            error = self.backend.next_list_error
            self.backend.next_list_error = None
            raise error
        prefix = _required_str(kwargs, "name_starts_with")
        start_from = kwargs.get("start_from")
        if start_from is not None and not isinstance(start_from, str):
            raise AssertionError("invalid fake Azure start_from")
        results_per_page = kwargs.get("results_per_page")
        if (
            isinstance(results_per_page, bool)
            or not isinstance(results_per_page, int)
            or results_per_page <= 0
        ):
            raise AssertionError("invalid fake Azure results_per_page")
        if kwargs.get("include") != ["metadata"]:
            raise AssertionError("fake Azure list must request metadata")
        with self.backend._lock:
            names = sorted(
                name
                for name in self.backend.objects
                if name.startswith(prefix) and (start_from is None or name >= start_from)
            )
            entries = [
                _properties(
                    name,
                    self.backend.objects[name],
                    unquote_etag=self.backend.unquote_list_etags,
                )
                for name in names
            ]
        page_size = self.backend.page_size_override or results_per_page
        return cast("_PagedPort", FakePaged(entries, min(page_size, results_per_page)))


def _properties(name: str, item: _Object, *, unquote_etag: bool = False) -> FakeProperties:
    return FakeProperties(
        name=name,
        etag=item.etag.strip('"') if unquote_etag else item.etag,
        size=len(item.data),
        metadata=dict(item.metadata),
        content_settings=FakeContentSettings(item.content_type),
    )


def _required_str(values: Mapping[str, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str):
        raise AssertionError(f"missing fake Azure {name}")
    return value


def _offset(value: str | None) -> int:
    if value is None:
        return 0
    try:
        offset = int(value)
    except ValueError as error:
        raise AssertionError("invalid fake Azure continuation token") from error
    if offset < 0:
        raise AssertionError("invalid fake Azure continuation token")
    return offset
