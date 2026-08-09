"""Provider-neutral writer contracts with lazy BigQuery compatibility exports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from dander.writer.base import (
    SchemaEvolution,
    WriteField,
    WriteMode,
    WritePattern,
    WriteTarget,
    WriteTransport,
)

if TYPE_CHECKING:
    from dander.writer.bigquery import (
        BigQueryIncrementalWriter,
        BigQueryReplaceWriter,
        BigQueryScd1Writer,
        BigQueryScd2Writer,
        BigQuerySnapshotWriter,
        BigQueryWriteError,
    )
    from dander.writer.storage_write import (
        BigQueryPendingStreamBackend,
        BigQueryStorageIncrementalWriter,
        BigQueryStorageScd1Writer,
        PendingStreamBackend,
    )

_BIGQUERY_EXPORTS = frozenset(
    {
        "BigQueryIncrementalWriter",
        "BigQueryReplaceWriter",
        "BigQueryScd1Writer",
        "BigQueryScd2Writer",
        "BigQuerySnapshotWriter",
        "BigQueryWriteError",
    }
)
_STORAGE_WRITE_EXPORTS = frozenset(
    {
        "BigQueryPendingStreamBackend",
        "BigQueryStorageIncrementalWriter",
        "BigQueryStorageScd1Writer",
        "PendingStreamBackend",
    }
)


def __getattr__(name: str) -> object:
    """Load concrete BigQuery writers only when a compatibility export is requested."""
    module_name: str | None = None
    if name in _BIGQUERY_EXPORTS:
        module_name = "dander.writer.bigquery"
    elif name in _STORAGE_WRITE_EXPORTS:
        module_name = "dander.writer.storage_write"
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


__all__ = [
    "BigQueryIncrementalWriter",
    "BigQueryReplaceWriter",
    "BigQueryScd1Writer",
    "BigQueryScd2Writer",
    "BigQuerySnapshotWriter",
    "BigQueryStorageIncrementalWriter",
    "BigQueryStorageScd1Writer",
    "BigQueryPendingStreamBackend",
    "BigQueryWriteError",
    "SchemaEvolution",
    "PendingStreamBackend",
    "WriteField",
    "WriteMode",
    "WritePattern",
    "WriteTarget",
    "WriteTransport",
]
