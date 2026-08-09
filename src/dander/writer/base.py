"""Provider-neutral contracts for explicit, idempotent load strategies.

dlt-sourced data may use dlt's own write dispositions; custom-sourced (enterprise) data and the
transform engine's materializations use these patterns. Every pattern is safely re-runnable. See
``steering/02-engineering.md`` (idempotency) and ``steering/languages/sql.md`` (write patterns).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from dander.warehouse.contracts import RelationRef

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from dander.concurrency import FencingToken, TargetFence
    from dander.warehouse import CanonicalField, RelationSchema


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
    COPY = "copy"


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


@dataclass(frozen=True, init=False)
class WriteTarget:
    """Canonical warehouse destination for a write.

    ``project``/``dataset``/``table`` remain accepted and readable for v1
    compatibility, but the stored coordinate is provider-neutral.
    """

    relation: RelationRef
    business_key: tuple[str, ...] = field(default_factory=tuple)
    schema: tuple[WriteField, ...] = field(default_factory=tuple)
    fence: FencingToken | None = None
    publication_fence: TargetFence | None = None

    def __init__(
        self,
        project: str | None = None,
        dataset: str | None = None,
        table: str | None = None,
        business_key: tuple[str, ...] = (),
        schema: tuple[WriteField, ...] = (),
        fence: FencingToken | None = None,
        publication_fence: TargetFence | None = None,
        *,
        relation: RelationRef | None = None,
    ) -> None:
        if relation is None:
            if project is None or dataset is None or table is None:
                raise TypeError("WriteTarget requires relation or project/dataset/table")
            relation = RelationRef(catalog=project, namespace=dataset, name=table)
        elif project is not None or dataset is not None or table is not None:
            raise TypeError("WriteTarget cannot combine relation with project/dataset/table")
        object.__setattr__(self, "relation", relation)
        object.__setattr__(self, "business_key", business_key)
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "fence", fence)
        object.__setattr__(self, "publication_fence", publication_fence)

    @property
    def project(self) -> str:
        """Return the legacy catalog alias used by BigQuery writers."""
        return self.relation.catalog

    @property
    def dataset(self) -> str:
        """Return the legacy namespace alias used by BigQuery writers."""
        return self.relation.namespace

    @property
    def table(self) -> str:
        """Return the legacy relation-name alias used by BigQuery writers."""
        return self.relation.name

    @property
    def relation_ref(self) -> RelationRef:
        """Return the canonical target coordinates."""
        return self.relation

    @property
    def canonical_schema(self) -> RelationSchema:
        """Return the target schema through the one-way compatibility mapper."""
        from dander.warehouse import canonical_schema_from_bigquery

        return canonical_schema_from_bigquery(self.schema)


class WritePattern(ABC):
    """Loads a batch of records into a warehouse target using one ``WriteMode``."""

    mode: WriteMode
    supports_batched_writes = False
    accepts_streaming_input = False
    requires_publication_fence = False

    @abstractmethod
    def write(self, records: Iterable[Mapping[str, Any]], target: WriteTarget) -> int:
        """Write ``records`` to ``target`` idempotently; return the number of rows affected."""
