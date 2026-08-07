"""BigQuery write patterns — explicit, idempotent load strategies.

dlt-sourced data may use dlt's own write dispositions; custom-sourced (enterprise) data and the
transform engine's materializations use these patterns. Every pattern is safely re-runnable. See
``steering/02-engineering.md`` (idempotency) and ``steering/languages/sql.md`` (write patterns).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from dander.concurrency import FencingToken
    from dander.warehouse import CanonicalField, RelationRef, RelationSchema


class WriteMode(StrEnum):
    """Supported load strategies."""

    SCD1 = "scd1"  # MERGE on business key (overwrite in place)
    SCD2 = "scd2"  # versioned rows (valid_from / valid_to / is_current)
    SNAPSHOT = "snapshot"  # partitioned, append-only
    INCREMENTAL = "incremental"  # watermark-bounded append/merge
    REPLACE = "replace"  # full-table replacement through a load job


class SchemaEvolution(StrEnum):
    """How a writer handles declared columns absent from an existing target."""

    STRICT = "strict"
    ADDITIVE = "additive"


class WriteTransport(StrEnum):
    """Physical ingestion path used before a logical write pattern."""

    LOAD_JOB = "load_job"
    STORAGE_WRITE = "storage_write"


@dataclass(frozen=True)
class WriteField:
    """One declared target column, including nested and repeated BigQuery fields."""

    name: str
    data_type: str
    mode: str = "NULLABLE"
    fields: tuple[WriteField, ...] = field(default_factory=tuple)

    def to_canonical(self) -> CanonicalField:
        """Map this legacy BigQuery writer field to canonical schema v1."""
        from dander.warehouse import canonical_field_from_bigquery

        return canonical_field_from_bigquery(self)


@dataclass(frozen=True)
class WriteTarget:
    """Fully-qualified BigQuery destination for a write."""

    project: str
    dataset: str
    table: str
    business_key: tuple[str, ...] = field(default_factory=tuple)
    schema: tuple[WriteField, ...] = field(default_factory=tuple)
    fence: FencingToken | None = None

    @property
    def relation_ref(self) -> RelationRef:
        """Return the target coordinates without rendering BigQuery SQL."""
        from dander.warehouse import RelationRef

        return RelationRef(catalog=self.project, namespace=self.dataset, name=self.table)

    @property
    def canonical_schema(self) -> RelationSchema:
        """Return the target schema through the one-way compatibility mapper."""
        from dander.warehouse import canonical_schema_from_bigquery

        return canonical_schema_from_bigquery(self.schema)


class WritePattern(ABC):
    """Loads a batch of records into a BigQuery target using one ``WriteMode``."""

    mode: WriteMode
    supports_batched_writes = False
    accepts_streaming_input = False

    @abstractmethod
    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        """Write ``records`` to ``target`` idempotently; return the number of rows affected."""
