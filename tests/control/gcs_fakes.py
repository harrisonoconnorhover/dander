"""Small generation-aware GCS fake used only by GraphStore contract tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeNotFoundError(RuntimeError):
    """Object does not exist."""


class FakePreconditionError(RuntimeError):
    """Generation condition did not match."""


@dataclass
class _Object:
    data: bytes
    generation: int
    content_type: str
    metadata: dict[str, str] | None


class FakeGCSBackend:
    def __init__(self) -> None:
        self.objects: dict[str, _Object] = {}
        self.next_generation = 1
        self.download_calls: list[tuple[str, int | None, int | None, int | None]] = []
        self.upload_calls: list[tuple[str, int]] = []
        self.delete_calls: list[tuple[str, int]] = []
        self.list_calls: list[tuple[str, str | None, int]] = []
        self.before_upload: Callable[[str, int, bytes], None] | None = None
        self._lock = threading.RLock()


class FakeBlob:
    def __init__(
        self,
        backend: FakeGCSBackend,
        name: str,
        *,
        generation: int | None = None,
        size: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self._backend = backend
        self.name = name
        self.generation = generation
        self.size = size
        self.metadata = dict(metadata) if metadata is not None else None

    def reload(self, *, timeout: float | None = None) -> None:
        del timeout
        with self._backend._lock:
            item = self._backend.objects.get(self.name)
            if item is None:
                raise FakeNotFoundError("fake detail must not escape")
            self.generation = item.generation
            self.size = len(item.data)
            self.metadata = dict(item.metadata) if item.metadata is not None else None

    def download_as_bytes(
        self,
        *,
        start: int | None = None,
        end: int | None = None,
        if_generation_match: int | None = None,
        timeout: float | None = None,
    ) -> bytes:
        del timeout
        self._backend.download_calls.append((self.name, if_generation_match, start, end))
        with self._backend._lock:
            item = self._backend.objects.get(self.name)
            if item is None:
                raise FakeNotFoundError("fake detail must not escape")
            if if_generation_match is not None and item.generation != if_generation_match:
                raise FakePreconditionError("fake detail must not escape")
            first = start or 0
            last = len(item.data) if end is None else end + 1
            return item.data[first:last]

    def upload_from_string(
        self,
        data: bytes,
        *,
        content_type: str,
        if_generation_match: int,
        timeout: float | None = None,
    ) -> None:
        del timeout
        self._backend.upload_calls.append((self.name, if_generation_match))
        if self._backend.before_upload is not None:
            self._backend.before_upload(self.name, if_generation_match, data)
        with self._backend._lock:
            current = self._backend.objects.get(self.name)
            if if_generation_match == 0:
                if current is not None:
                    raise FakePreconditionError("fake detail must not escape")
            elif current is None or current.generation != if_generation_match:
                raise FakePreconditionError("fake detail must not escape")
            generation = self._backend.next_generation
            self._backend.next_generation += 1
            self._backend.objects[self.name] = _Object(
                data=bytes(data),
                generation=generation,
                content_type=content_type,
                metadata=dict(self.metadata) if self.metadata is not None else None,
            )
        self.generation = generation
        self.size = len(data)

    def delete(
        self,
        *,
        if_generation_match: int,
        timeout: float | None = None,
    ) -> None:
        del timeout
        self._backend.delete_calls.append((self.name, if_generation_match))
        with self._backend._lock:
            current = self._backend.objects.get(self.name)
            if current is None:
                raise FakeNotFoundError("fake detail must not escape")
            if current.generation != if_generation_match:
                raise FakePreconditionError("fake detail must not escape")
            del self._backend.objects[self.name]


class FakeBucket:
    def __init__(self, backend: FakeGCSBackend, name: str) -> None:
        self._backend = backend
        self.name = name

    def blob(self, blob_name: str) -> FakeBlob:
        return FakeBlob(self._backend, blob_name)


class FakeGCSClient:
    def __init__(self, backend: FakeGCSBackend | None = None) -> None:
        self.backend = backend or FakeGCSBackend()

    def bucket(self, bucket_name: str) -> FakeBucket:
        return FakeBucket(self.backend, bucket_name)

    def list_blobs(
        self,
        bucket_or_name: object,
        *,
        max_results: int,
        prefix: str,
        start_offset: str | None,
        timeout: float | None = None,
    ) -> list[FakeBlob]:
        del bucket_or_name, timeout
        self.backend.list_calls.append((prefix, start_offset, max_results))
        with self.backend._lock:
            names = sorted(
                name
                for name in self.backend.objects
                if name.startswith(prefix) and (start_offset is None or name >= start_offset)
            )[:max_results]
            return [
                FakeBlob(
                    self.backend,
                    name,
                    generation=self.backend.objects[name].generation,
                    size=len(self.backend.objects[name].data),
                    metadata=self.backend.objects[name].metadata,
                )
                for name in names
            ]
