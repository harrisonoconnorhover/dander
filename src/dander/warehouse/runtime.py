"""Small composable capabilities used by one selected warehouse runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from dander.concurrency import FencingToken, OwnershipGuard, TargetFence
    from dander.telemetry import OperationTelemetry, TelemetryOperation
    from dander.transform import TransformRunResult
    from dander.warehouse.contracts import RelationCodec, RelationRef, RelationSchema
    from dander.writer.base import SchemaEvolution, WriteMode, WritePattern, WriteTransport


@dataclass(frozen=True, slots=True)
class PreparedWarehouseStatement:
    """Provider statement plus opaque provider-native execution options."""

    sql: str
    options: object | None = None

    def __post_init__(self) -> None:
        if not self.sql.strip():
            raise ValueError("prepared warehouse statement must not be blank")


@dataclass(frozen=True, slots=True)
class WarehouseCapabilities:
    """Closed support declaration for one concrete warehouse implementation."""

    provider_id: str
    schema_contract_version: int
    write_modes: frozenset[WriteMode]
    transports: frozenset[WriteTransport]
    supports_transforms: bool
    supports_graphs: bool
    supports_target_fencing: bool

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("warehouse capability provider_id must not be blank")
        if self.schema_contract_version != 1:
            raise ValueError("warehouse schema contract version must be 1")
        if not self.write_modes:
            raise ValueError("warehouse capabilities must declare at least one write mode")


@runtime_checkable
class WarehouseSchemaMapper(Protocol):
    """Map a legacy provider declaration into canonical schema v1."""

    def canonical_schema(self, fields: Sequence[object]) -> RelationSchema:
        """Return the lossless canonical representation of ``fields``."""
        ...


@runtime_checkable
class WarehouseWriterFactory(Protocol):
    """Construct the ingestion writer selected by the execution context."""

    def build_ingestion_writer(
        self,
        *,
        sandbox: bool,
        batch_rows: int,
        schema_evolution: SchemaEvolution,
    ) -> WritePattern:
        """Build one writer without branching in the cloud-neutral CLI."""
        ...


@runtime_checkable
class WarehouseTransformRunner(Protocol):
    """Provider transform runner consumed by ``PipelineExecutor``."""

    def build(
        self,
        models_dir: Path,
        *,
        selected: Iterable[str] | None = None,
        ownership: OwnershipGuard | None = None,
    ) -> TransformRunResult:
        """Build selected transformations and execute their assertions."""
        ...


@runtime_checkable
class WarehouseTransformFactory(Protocol):
    """Construct a provider transform or graph runner."""

    def build_transform_runner(
        self,
        *,
        graph_plan: object | None,
        build_models: bool,
        raw_namespace: str = "raw",
    ) -> WarehouseTransformRunner | None:
        """Return the selected provider runner or ``None`` when transforms are disabled."""
        ...


@runtime_checkable
class WarehouseTargetFence(Protocol):
    """Claim and transactionally verify destination publication ownership."""

    def claim(self, target: RelationRef, fence: FencingToken) -> TargetFence:
        """Claim ``target`` before creating run-scoped staging."""
        ...

    def prepare_dml(self, statement: str, fence: TargetFence) -> PreparedWarehouseStatement:
        """Bind the provider fencing statement and its execution options."""
        ...


@runtime_checkable
class WarehouseTelemetry(Protocol):
    """Normalize one provider job without leaking provider response payloads."""

    def operation(
        self,
        job: object,
        *,
        operation: TelemetryOperation,
        duration_ms: int = 0,
        retry_count: int = 0,
    ) -> OperationTelemetry:
        """Return provider-neutral telemetry for one completed operation."""
        ...


@dataclass(frozen=True, slots=True)
class WarehouseRuntime:
    """Selected warehouse composition consumed by Dander execution."""

    provider_id: str
    relation_codec: RelationCodec
    schema_mapper: WarehouseSchemaMapper
    writers: WarehouseWriterFactory
    transforms: WarehouseTransformFactory
    target_fence: WarehouseTargetFence
    telemetry: WarehouseTelemetry
    capabilities: WarehouseCapabilities

    def __post_init__(self) -> None:
        if self.provider_id != self.relation_codec.provider_id:
            raise ValueError("warehouse relation codec provider does not match runtime")
        if self.provider_id != self.capabilities.provider_id:
            raise ValueError("warehouse capabilities provider does not match runtime")
