"""Canonical graph records shared by the ETag-based object-store adapters.

S3, Azure Blob, and OCI retain their own transport, conditional-write, pagination, and error
handling. These shared records preserve their existing JSON fields and journal validation.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from dander.control.graph_store import (
    CanonicalGraphDocument,
    GraphDeleteReceipt,
    GraphRecord,
    GraphSummary,
    _timestamp,
)
from dander.control.models import PipelineGraphDocument  # noqa: TC001 - Pydantic resolves it


class _DeleteFence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: str = Field(min_length=1, max_length=512)


class _GraphObjectMetadata(BaseModel):
    """Safe bounded summary metadata stored beside one graph object body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    project: str
    graph: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    create_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    create_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    delete_key_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    delete_request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    delete_expected_revision: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_fence_shape(self) -> Self:
        values = (
            self.delete_key_sha256,
            self.delete_request_sha256,
            self.delete_expected_revision,
        )
        if any(value is None for value in values) and any(value is not None for value in values):
            raise ValueError("graph metadata delete fence is incomplete")
        return self

    @classmethod
    def from_stored(cls, stored: _StoredGraph) -> _GraphObjectMetadata:
        fence = stored.delete_fence
        return cls(
            project=stored.project,
            graph=stored.graph,
            content_sha256=stored.content_sha256,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
            create_key_sha256=stored.create_key_sha256,
            create_request_sha256=stored.create_request_sha256,
            delete_key_sha256=fence.key_sha256 if fence is not None else None,
            delete_request_sha256=fence.request_sha256 if fence is not None else None,
            delete_expected_revision=fence.expected_revision if fence is not None else None,
        )

    def summary(self, etag: str) -> GraphSummary:
        return GraphSummary(
            project=self.project,
            graph=self.graph,
            revision=etag,
            content_sha256=self.content_sha256,
            created_at=_timestamp(self.created_at),
            updated_at=_timestamp(self.updated_at),
        )


class _StoredGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    project: str
    graph: str
    document: PipelineGraphDocument
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    create_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    create_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    delete_fence: _DeleteFence | None = None

    @classmethod
    def from_canonical(
        cls,
        *,
        project: str,
        graph: str,
        canonical: CanonicalGraphDocument,
        created_at: str,
        updated_at: str,
        create_key_sha256: str,
        create_request_sha256: str,
    ) -> _StoredGraph:
        return cls(
            project=project,
            graph=graph,
            document=canonical.document,
            content_sha256=canonical.content_sha256,
            created_at=created_at,
            updated_at=updated_at,
            create_key_sha256=create_key_sha256,
            create_request_sha256=create_request_sha256,
        )

    def record(self, canonical: CanonicalGraphDocument, etag: str) -> GraphRecord:
        return GraphRecord(
            project=self.project,
            graph=self.graph,
            document=canonical.document,
            revision=etag,
            content_sha256=self.content_sha256,
            created_at=_timestamp(self.created_at),
            updated_at=_timestamp(self.updated_at),
        )


class _StoredDeleteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str
    graph: str
    revision: str = Field(min_length=1, max_length=512)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    deleted_at: AwareDatetime

    @classmethod
    def from_receipt(cls, receipt: GraphDeleteReceipt) -> _StoredDeleteReceipt:
        return cls(
            project=receipt.project,
            graph=receipt.graph,
            revision=receipt.revision,
            content_sha256=receipt.content_sha256,
            deleted_at=receipt.deleted_at,
        )

    def receipt(self) -> GraphDeleteReceipt:
        return GraphDeleteReceipt(
            project=self.project,
            graph=self.graph,
            revision=self.revision,
            content_sha256=self.content_sha256,
            deleted_at=_timestamp(self.deleted_at),
        )


class _CreateJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["pending", "completed"] = "pending"
    project: str
    graph: str
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    planned_graph: _StoredGraph
    result_revision: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if (self.status == "completed") != (self.result_revision is not None):
            raise ValueError("create journal status is inconsistent")
        if self.planned_graph.delete_fence is not None:
            raise ValueError("planned create graph cannot be fenced")
        if self.planned_graph.project != self.project or self.planned_graph.graph != self.graph:
            raise ValueError("create journal addressing is invalid")
        return self


class _DeleteJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["pending", "completed"] = "pending"
    project: str
    graph: str
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: str = Field(min_length=1, max_length=512)
    receipt: _StoredDeleteReceipt
    fence_revision: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if self.receipt.project != self.project or self.receipt.graph != self.graph:
            raise ValueError("delete journal addressing is invalid")
        if self.receipt.revision != self.expected_revision:
            raise ValueError("delete journal revision is invalid")
        if self.status == "completed" and self.fence_revision is None:
            raise ValueError("completed delete journal has no fence revision")
        return self
