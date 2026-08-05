"""Discriminated, per-node-type config models and their routing logic.

`Node.config` (see `dander.pipeline.graph`) is validated against a config model chosen by the
node's `type` rather than accepted as an opaque `dict`. This module owns that discriminated set of
config models plus the pure routing function `Node` delegates to. `SourceNodeConfig` carries the
request/payload spec (DANDER-11); `TransformNodeConfig` carries an optional executable join and
ordered schema-preserving operations; `TargetNodeConfig` carries the target/writer config (write
pattern, destination table,
partitioning/clustering — DANDER-16). Pagination and incremental cursor on the source side remain
unmodeled placeholders. Deliberately does not import `Node`
(`graph.py` imports from here, not the reverse), so there is no import cycle.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Real (non-TYPE_CHECKING) imports: despite `from __future__ import annotations` making these
# annotations lazy strings, Pydantic still resolves them against this module's globals when it
# builds the owning model's schema at class-definition time. Deferring either import to a
# `TYPE_CHECKING` block (ruff's TC001 suggestion) leaves the name undefined at runtime and raises
# `PydanticUserError: '<Model>' is not fully defined` on import; verified with a minimal repro
# before overriding the rule here.
from dander.pipeline.operations import OperationSpec  # noqa: TC001
from dander.pipeline.request_spec import RequestSpec  # noqa: TC001
from dander.writer.base import SchemaEvolution, WriteMode, WriteTransport  # noqa: TC001


class NodeType(StrEnum):
    """The closed set of node kinds that currently have a stricter config schema.

    A `StrEnum` (matching the `TransformationKind`/`JoinType` convention in `graph.py`) so the
    config registry and tests can branch on a named, importable type while it still compares
    equal to its plain string value. This does **not** close `Node.type` itself, which stays a
    free `str` (DANDER-3 owns validating `Node.type`'s accepted values) so `task` and any future,
    as-yet-unmodeled kind still load with free-form `config`.

    Attributes:
        SOURCE: A node that extracts data from a source system.
        TRANSFORM: A node that derives/transforms data from upstream nodes.
        TARGET: A node that writes data to a destination (e.g. BigQuery).
    """

    SOURCE = "source"
    TRANSFORM = "transform"
    TARGET = "target"


class NodeConfig(BaseModel):
    """Common base for every typed, per-node-type config model.

    Carries no fields of its own — it exists so `Node.config` can be annotated on this
    abstraction (interface-first per `steering/02-engineering.md`) and so mismatch detection in
    `resolve_node_config` can test `isinstance(value, NodeConfig)`. `extra="allow"` so config
    content that has no dedicated field yet on any subclass is preserved losslessly rather than
    rejected.

    A `NodeConfig` (and every subclass) must never hold a secret or credential value
    (`steering/01-security.md`) — configs reference secrets indirectly (a Secret Manager resource
    name or env var name), never a literal value.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SourceNodeConfig(NodeConfig):
    """Typed config for a `source`-type node.

    An extensible placeholder beyond `request`: pagination, incremental cursor, and field-mapping
    detail are still unmodeled here (`extra="allow"`, inherited from `NodeConfig`, carries them
    losslessly until a later ticket adds fields for them). Must never hold a secret value — a
    source config references credentials by Secret Manager name / env var name only.

    Attributes:
        connector: Optional connector-YAML name used by the executable hosted graph bridge.
        endpoint: Optional endpoint name within that connector. Older declarative graphs may use
            this key for an API path; runtime binding validates the identifier only when
            `connector` is also present.
        request: Optional declarative request/payload spec (DANDER-11) describing how this source
            node calls its API — HTTP method, headers, query params, and a body template. `None`
            (the default) is a plain, spec-less GET, unchanged from a pre-DANDER-11 source node.
            Header/query-param/body **values** are secret references or field references only
            (`steering/01-security.md`) and are never resolved or sent here — see
            `dander.pipeline.request_spec.RequestSpec`.
    """

    connector: str | None = None
    endpoint: str | None = None
    request: RequestSpec | None = None


class ExecutableJoinType(StrEnum):
    """BigQuery join kinds supported by an executable transform node."""

    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"


class ExecutableJoinKey(BaseModel):
    """One equality key between the named left and right inputs."""

    left: str = Field(min_length=1)
    right: str = Field(min_length=1)


class TransformJoinConfig(BaseModel):
    """An executable two-input join whose output is the owning transform node."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    left_input: str = Field(min_length=1)
    right_input: str = Field(min_length=1)
    type: ExecutableJoinType
    keys: list[ExecutableJoinKey] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_distinct_inputs(self) -> TransformJoinConfig:
        if self.left_input == self.right_input:
            raise ValueError("TransformJoinConfig requires distinct left and right inputs")
        return self


class TransformNodeConfig(NodeConfig):
    """Typed config for a `transform`-type node.

    Attributes:
        join: Optional executable two-input join. Unlike the legacy edge-level `JoinSpec`, this
            names two predecessor nodes and makes the transform node the unambiguous output.
        operations: Ordered schema-preserving operations applied after this node's mappings and
            before any downstream edge reads its declared fields.
    """

    join: TransformJoinConfig | None = None
    operations: list[OperationSpec] = Field(default_factory=list)


class PartitioningType(StrEnum):
    """The closed set of time-unit partitioning granularities a `PartitioningSpec` may declare.

    A `StrEnum` (matching the `WriteMode`/`TransformationKind`/`JoinType` convention elsewhere in
    `dander.pipeline`), so it serializes to/from its plain string value stably in YAML and JSON;
    an out-of-set value fails validation with a clear `ValidationError`. Scope is deliberately
    limited to BigQuery time-unit partitioning — integer-range partitioning is a deferred future
    member (see `steering/02-engineering.md` on avoiding speculative generality).

    Attributes:
        HOUR: Hourly partitions.
        DAY: Daily partitions — the common case and BigQuery's default granularity.
        MONTH: Monthly partitions.
        YEAR: Yearly partitions.
    """

    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class PartitioningSpec(BaseModel):
    """Declarative BigQuery time-unit/ingestion-time partitioning for a target write.

    Inert: nothing here issues DDL or executes a write. `prepare_target_writer` maps this intent
    to the concrete writer contract in `src/dander/writer/base.py`. Scope is time-unit and
    ingestion-time partitioning (the common case, and what the `WriteMode.SNAPSHOT`
    "partitioned, append-only" pattern needs); integer-range partitioning is deferred.

    Attributes:
        field: The partition column name. `None` means ingestion-time partitioning (BigQuery's
            pseudo-column, e.g. `_PARTITIONTIME`) rather than a partition on a declared column.
        granularity: The partitioning granularity. Defaults to `PartitioningType.DAY`.
        require_partition_filter: Whether queries against the partitioned table must include a
            partition filter. Defaults to `False`.
    """

    model_config = ConfigDict(populate_by_name=True)

    field: str | None = None
    granularity: PartitioningType = PartitioningType.DAY
    require_partition_filter: bool = False


class DestinationSpec(BaseModel):
    """Declarative BigQuery destination for a target write, mirroring `WriteTarget`.

    Structurally mirrors `dander.writer.base.WriteTarget` (`project`/`dataset`/`table`/
    `business_key`) 1:1. `compile_target` and `prepare_target_writer` perform the runtime mapping
    after resolving an optional project from deployment context. Unlike `WriteTarget` (a frozen
    dataclass — the internal runtime value object), this is a Pydantic model, per
    `steering/languages/python.md`'s config/runtime split. Dataset/table values here are ordinary
    identifiers, never secrets (`steering/01-security.md`).

    Attributes:
        project: GCP project id hosting the destination dataset. `None` means "resolve from
            deployment context later" — deliberately not required here since this model only
            declares config, never executes a write.
        dataset: BigQuery dataset name. Required, non-empty.
        table: BigQuery table name. Required, non-empty.
        business_key: Ordered column names identifying a logical row for MERGE/versioning writes
            (mirrors `WriteTarget.business_key`, which is a `tuple[str, ...]` internally; this is
            a `list[str]` at the config boundary). Defaults to empty — required non-empty for the
            `WriteMode`s that need one; see `WriterConfig._check_mode_requirements`.
    """

    model_config = ConfigDict(populate_by_name=True)

    project: str | None = None
    dataset: str = Field(min_length=1)
    table: str = Field(min_length=1)
    business_key: list[str] = Field(default_factory=list)


class WriterConfig(BaseModel):
    """Declarative target/writer config: how and where a `target` node writes.

    Inert model + validation only: nothing here calls `WritePattern.write` or any
    `google.cloud` client (`src/dander/writer/base.py` owns write execution). `write_mode` reuses
    `dander.writer.base.WriteMode` directly rather than redeclaring a parallel pipeline-side enum,
    so the two can never drift; `destination` mirrors `WriteTarget`'s shape (see
    `DestinationSpec`). See `steering/00-project-overview.md` (BigQuery Writer module) for the
    write-pattern semantics this config declares intent for.

    Attributes:
        write_mode: The write pattern, reusing `WriteMode` (SCD1/SCD2/SNAPSHOT/INCREMENTAL).
            Required — an out-of-set string fails at the Pydantic boundary.
        destination: The BigQuery destination (dataset/table/business key).
        cursor_field: Watermark/cursor column for an `INCREMENTAL` write (the "track a
            watermark/cursor per source+entity" mandate in `steering/02-engineering.md`). `None`
            for the other modes, which are not watermark-bounded. Required non-empty when
            `write_mode` is `INCREMENTAL`.
        partitioning: Optional partitioning spec. `None` means an unpartitioned destination.
        clustering: Ordered clustering column names. BigQuery caps clustering at 4 columns, so
            this is capped here too (`max_length=4`); duplicate column names are rejected.
        max_batch_rows: Maximum rows sent in one BigQuery load-job request.
        schema_evolution: `strict` by default; `additive` permits only declared scalar target
            fields to be added as nullable columns, never changed or removed.
        transport: `load_job` by default. `storage_write` uses an atomic pending stream and is
            available for keyed SCD1/incremental workloads.

    `hide_input_in_errors=True` is set for the same reason `dander.pipeline.graph.Node` and
    `dander.pipeline.request_spec.RequestSpec` set it: without it, Pydantic's default
    `ValidationError` rendering embeds a `repr()` of the whole rejected input alongside the raised
    `ValueError`, which could echo destination/table identifiers into the exception string even
    though the message itself only names the mode and the missing constraint
    (`steering/01-security.md`).
    """

    model_config = ConfigDict(populate_by_name=True, hide_input_in_errors=True)

    write_mode: WriteMode
    destination: DestinationSpec
    cursor_field: str | None = None
    partitioning: PartitioningSpec | None = None
    clustering: list[str] = Field(default_factory=list, max_length=4)
    max_batch_rows: int = Field(default=10_000, gt=0, le=100_000)
    schema_evolution: SchemaEvolution = SchemaEvolution.STRICT
    transport: WriteTransport = WriteTransport.LOAD_JOB

    @model_validator(mode="after")
    def _check_mode_requirements(self) -> WriterConfig:
        """Enforce the constraints each `write_mode` needs, and clustering uniqueness.

        `SCD1`/`SCD2`/`INCREMENTAL` MERGE, version, or merge-on-key, so each requires a non-empty
        `destination.business_key`; `SNAPSHOT` is append-only and never merges on a key, so it is
        permissive here (a stray `business_key` is harmless, not an error). `INCREMENTAL` is
        additionally watermark-bounded, so it requires a non-empty `cursor_field`. These are
        presence-of-value checks (non-empty list/string), not null-vs-omitted checks, so they stay
        lossless across a `model_dump` -> reload cycle unlike `Transformation.constant` (which
        needs `model_fields_set`): `model_dump` always re-emits `business_key: []` /
        `cursor_field: null` for the modes that don't require them, and reload re-validates
        identically since those modes never checked those fields' presence in the first place.
        Clustering-column uniqueness is checked regardless of `write_mode`, since BigQuery rejects
        duplicate clustering columns outright.

        Raises:
            ValueError: If `write_mode` is `SCD1`/`SCD2`/`INCREMENTAL` and `destination.
                business_key` is empty; if `write_mode` is `INCREMENTAL` and `cursor_field` is
                missing/empty; or if `clustering` contains a duplicate column name. Messages name
                only the mode and the missing constraint, never any config value.
        """
        key_required_modes = (WriteMode.SCD1, WriteMode.SCD2, WriteMode.INCREMENTAL)
        if self.write_mode in key_required_modes and not self.destination.business_key:
            raise ValueError(
                f"WriterConfig(write_mode={self.write_mode.value}) requires a non-empty "
                "'destination.business_key'."
            )
        if self.write_mode is WriteMode.INCREMENTAL and (
            self.cursor_field is None or not self.cursor_field.strip()
        ):
            raise ValueError(
                "WriterConfig(write_mode=incremental) requires a non-empty 'cursor_field'."
            )
        if len(set(self.clustering)) != len(self.clustering):
            raise ValueError("WriterConfig.clustering must not contain duplicate column names.")
        if self.transport is WriteTransport.STORAGE_WRITE and self.write_mode not in (
            WriteMode.SCD1,
            WriteMode.INCREMENTAL,
        ):
            raise ValueError(
                "WriterConfig(transport=storage_write) supports only scd1 or incremental mode."
            )
        return self


class TargetNodeConfig(NodeConfig):
    """Typed config for a `target`-type node.

    Extensible beyond `writer`: the `extra="allow"` inherited from `NodeConfig` still preserves
    any as-yet-unmodeled config content losslessly.

    Attributes:
        writer: Optional declarative target/writer config (DANDER-16) describing how and where
            this target node writes — write pattern, destination, and partitioning/clustering
            (see `WriterConfig` and `src/dander/writer/base.py`). `None` (the default) means a
            target node with no writer config, unchanged from a pre-DANDER-16 target node.
    """

    writer: WriterConfig | None = None


_NODE_CONFIG_MODELS: dict[str, type[NodeConfig]] = {
    NodeType.SOURCE: SourceNodeConfig,
    NodeType.TRANSFORM: TransformNodeConfig,
    NodeType.TARGET: TargetNodeConfig,
}


def resolve_node_config(node_type: str, value: object) -> NodeConfig | dict[str, Any]:
    """Route a raw/typed `config` value to the model matching `node_type`.

    The pure, unit-testable seam behind `Node`'s `config` field validator. `node_type` is
    `Node.type`, a plain `str`; because `NodeType` is a `StrEnum`, looking it up directly in
    `_NODE_CONFIG_MODELS` (keyed by `NodeType` members) works via string equality/hash without an
    explicit enum coercion.

    Args:
        node_type: The owning node's `type` value (e.g. ``"source"``, ``"task"``).
        value: The raw `config` value being validated — typically `None` (absent), a `dict`, or
            (when constructing a `Node` programmatically) an already-typed `NodeConfig` instance.

    Returns:
        For a modeled `node_type` (`source`/`transform`/`target`): the corresponding `NodeConfig`
        subclass instance. For an unmodeled `node_type`: `value` unchanged if it is already a
        `dict`, else an empty `dict` for `None`/absent — the pre-existing free-form behavior.

    Raises:
        ValueError: If `value` is already a `NodeConfig` instance whose concrete class does not
            match the model registered for `node_type`. The message names both class names and
            `node_type` only — never any config value (`steering/01-security.md`).
    """
    model = _NODE_CONFIG_MODELS.get(node_type)

    if model is None:
        if isinstance(value, dict):
            return value
        return {}

    if isinstance(value, NodeConfig):
        if type(value) is model:
            return value
        raise ValueError(
            f"{node_type} node config expects {model.__name__}, got {type(value).__name__}"
        )

    return model.model_validate(value or {})
