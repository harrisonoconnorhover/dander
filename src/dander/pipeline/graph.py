"""Declarative pipeline-graph model and its YAML/JSON serialization.

A pipeline graph is the durable, declarative primitive behind both a future drag-drop UI and
fully code-authored pipelines: a list of ``nodes`` (data objects/tasks) and a list of ``edges``
(how they connect). This module owns the model **shape** and stable round-trip serialization
only — uniqueness checks, dangling-edge detection, self-loops, DAG/cycle detection, adjacency,
and topological ordering are deliberately out of scope here (see DANDER-3, which builds on these
models).
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    ValidationInfo,
    field_validator,
    model_validator,
)

from dander.pipeline.node_config import (
    NodeConfig,
    SourceNodeConfig,
    TargetNodeConfig,
    resolve_node_config,
)
from dander.warehouse import ProviderExtension  # noqa: TC001 - Pydantic resolves it

if TYPE_CHECKING:
    from pathlib import Path


class GenericTestKind(StrEnum):
    """The closed set of generic data-quality test kinds a `FieldTest` may declare.

    A `StrEnum` (not a bare `Literal`), matching the `TransformationKind`/`JoinType`/
    `TriggerKind` convention elsewhere in this module: a named, importable type for a graph test
    runner to branch on, while serializing to/from a plain string value stably in YAML and JSON.
    An out-of-set value fails validation with a clear error.

    Attributes:
        NOT_NULL: The field must never be null. No additional payload.
        UNIQUE: The field's values must be unique within its node. No additional payload.
        ACCEPTED_VALUES: The field's value must be one of a declared closed set. Payload: a
            non-empty `values` list.
        RELATIONSHIPS: The field's value must resolve against another node's field (a
            referential-integrity check, dbt-style). Payload: a `to` node id and a `field` name
            on that node.
    """

    NOT_NULL = "not_null"
    UNIQUE = "unique"
    ACCEPTED_VALUES = "accepted_values"
    RELATIONSHIPS = "relationships"


class FieldTest(BaseModel):
    """A single declarative generic data-quality test attached to a `NodeField`.

    This model is opaque and inert: it records test *intent* only — **no test is ever executed
    here**. The transform engine executes equivalent tests declared in model sidecars, but a
    bridge from these graph-level `FieldTest` values to that runner is not implemented. Whether
    a `relationships` test's `to`/`field` resolves against another node's declared field is
    likewise not enforced here (see `dander.pipeline.graph_ops.validate_field_wiring`).

    Modeled with the same shape as `Transformation`/`Trigger` (a `kind` discriminator plus
    kind-specific payload fields validated by a single `@model_validator`), rather than a
    discriminated union of one model per kind, for internal consistency with the rest of this
    module and a single importable type for the downstream layer to branch on.

    Attributes:
        kind: The test discriminator. Required — no default, since no single kind is a sensible
            fallback and a missing/invalid `kind` should fail loudly at the Pydantic boundary.
        values: The accepted-values list for the `ACCEPTED_VALUES` kind. Typed `list[Any]`
            (matching `Transformation.constant`) since accepted tokens may be str/int/bool, not
            only strings. Required non-empty when `kind` is `ACCEPTED_VALUES`; must be empty for
            every other kind. Never a real/sensitive value — synthetic tokens only
            (`steering/01-security.md`).
        to: Referenced **node id** (name only, never a value) for the `RELATIONSHIPS` kind.
            Required when `kind` is `RELATIONSHIPS`; must be unset otherwise. Resolution against
            a real node is deferred (see class docstring).
        field: Referenced **field name** (name only, never a value) on the `to` node for the
            `RELATIONSHIPS` kind. Required when `kind` is `RELATIONSHIPS`; must be unset
            otherwise. Resolution against a real field is deferred (see class docstring).
        metadata: Free-form tags/labels only (never data/secrets), consistent with
            `NodeField.metadata` / `Node.config`.
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: GenericTestKind
    values: list[Any] = Field(default_factory=list)
    to: str | None = None
    field: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_kind_params(self) -> FieldTest:
        """Enforce the payload each `kind` requires/forbids.

        Every check tests the field **value** (`not self.values`, `self.to is not None`,
        `self.field is None`/`not self.field.strip()`) rather than `model_fields_set`. None of
        these params has a meaningful "explicit empty/null" value distinct from "not provided"
        (unlike `Transformation.constant`, whose legitimate `null` forced the
        `model_fields_set` dance there) — an empty `values` list and `None` `to`/`field` are the
        neutral defaults, and `model_dump` always re-emits every field (including those
        defaults), so a presence-based check would spuriously trip on reload. Value-based checks
        stay lossless across a dump -> load cycle.

        Raises:
            ValueError: If `kind` is `ACCEPTED_VALUES` and `values` is empty, or `to`/`field` is
                set; if `kind` is `RELATIONSHIPS` and `to` or `field` is missing/empty, or
                `values` is set; or if `kind` is `NOT_NULL`/`UNIQUE` and `values` is non-empty or
                `to`/`field` is set.
        """
        if self.kind is GenericTestKind.ACCEPTED_VALUES:
            if not self.values:
                raise ValueError(
                    "FieldTest(kind=accepted_values) requires a non-empty 'values' list."
                )
            if self.to is not None:
                raise ValueError("FieldTest(kind=accepted_values) must not set 'to'.")
            if self.field is not None:
                raise ValueError("FieldTest(kind=accepted_values) must not set 'field'.")
        elif self.kind is GenericTestKind.RELATIONSHIPS:
            if self.to is None or not self.to.strip():
                raise ValueError("FieldTest(kind=relationships) requires a non-empty 'to' node id.")
            if self.field is None or not self.field.strip():
                raise ValueError("FieldTest(kind=relationships) requires a non-empty 'field' name.")
            if self.values:
                raise ValueError("FieldTest(kind=relationships) must not set 'values'.")
        else:  # NOT_NULL, UNIQUE
            if self.values:
                raise ValueError(f"FieldTest(kind={self.kind.value}) must not set 'values'.")
            if self.to is not None:
                raise ValueError(f"FieldTest(kind={self.kind.value}) must not set 'to'.")
            if self.field is not None:
                raise ValueError(f"FieldTest(kind={self.kind.value}) must not set 'field'.")

        return self


class NodeField(BaseModel):
    """A single declared field on a node's schema.

    Describes the shape of one field a node exposes (e.g. one column of a `source` node) —
    never a value. This model carries structural/descriptive metadata only; cross-node
    validation that mappings/joins reference real declared fields is deferred (see DANDER-8).

    Attributes:
        name: Required identifier for the field.
        type: Free-form **raw/source type** token (e.g. a BigQuery-ish ``STRING``/``INT64``) —
            the field's declared type as it arrives from the source. Validation of accepted
            values is deferred, mirroring how `Node.type` is handled.
        cast_to: Optional target/cast type override for this field (raw-vs-target distinction).
            `None` (the default) means no override — the field is used as `type` declares.
            `dander.pipeline.compiler` applies supported scalar casts when it compiles an
            executable graph.
        nullable: Whether the field may be null. Defaults to `True` since most source fields
            are nullable; set `False` to opt into a not-null guarantee.
        description: Optional human-readable documentation for the field.
        tests: Declarative generic data-quality tests attached to this field (see `FieldTest`).
            Defaults to empty — a field with no tests loads and dumps just as a DANDER-4 field
            did. The graph compiler does not execute these tests; the transform engine's
            sidecar-based test contract is a separate, implemented path.
        metadata: Free-form tags/labels only (e.g. a `sensitivity`/`pii` classification,
            ownership). Per `steering/01-security.md`, this must never hold a real field value
            or sample data — labels/tags only.
    """

    name: str
    type: str
    cast_to: str | None = None
    nullable: bool = True
    description: str | None = None
    tests: list[FieldTest] = Field(default_factory=list)
    extensions: tuple[ProviderExtension, ...] = Field(default_factory=tuple)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TriggerKind(StrEnum):
    """The closed set of trigger kinds a `Trigger` may declare.

    A `StrEnum` (not a bare `Literal`), matching the `TransformationKind`/`JoinType` convention
    elsewhere in this module: a named, importable type to branch on later, while serializing
    to/from a plain string value stably in YAML and JSON. An out-of-set value fails validation
    with a clear error.

    Attributes:
        SCHEDULE: A cron-driven trigger. Payload: an opaque `cron` expression string.
        DEPENDENCY: An upstream-dependency trigger, firing when named upstream entities
            complete. Payload: a non-empty `depends_on` list of upstream identifiers.
        MANUAL: A manual/event trigger. Payload: an optional opaque `event` name — unset means
            purely manual/on-demand, set names an external event.
    """

    SCHEDULE = "schedule"
    DEPENDENCY = "dependency"
    MANUAL = "manual"


class Trigger(BaseModel):
    """A declarative trigger/schedule attachable to a pipeline or a node.

    This model is opaque and inert: it is **declarative model only**, recording trigger intent.
    Dander can provision a fixed Cloud Scheduler job for its hosted public pipeline, but that
    scheduler is not generated from this graph model. Nothing here evaluates a trigger; in
    particular, a `cron` expression is stored as an opaque string and is never parsed, validated
    as a cron grammar, or scheduled here.

    Modeled with the same shape as `Transformation` (a `kind` discriminator plus kind-specific
    payload fields validated by a single `@model_validator`), rather than a discriminated union
    of one model per kind, for internal consistency with the rest of this module and a single
    importable type for a graph-aware orchestration layer to branch on.

    Attributes:
        kind: The trigger discriminator. Required — unlike `Transformation`'s `DIRECT` default,
            no trigger kind is a sensible default, so a missing/invalid `kind` fails at the
            Pydantic boundary with a clear error.
        cron: Opaque cron expression for the `SCHEDULE` kind. Never parsed or scheduled here.
            Required and non-empty when `kind` is `SCHEDULE`; must be unset otherwise.
        depends_on: Upstream identifiers for the `DEPENDENCY` kind, named **by name/id only,
            never values** (`steering/01-security.md`) — an upstream pipeline name at graph
            level, or an upstream node id at node level. Existence/resolution of these
            identifiers is deferred to graph-aware orchestration. At least one required when
            `kind` is `DEPENDENCY`; must be empty otherwise.
        event: Optional opaque event name for the `MANUAL` kind. `None` means a purely
            manual/on-demand trigger; a set string names an external event. Must be unset for
            the other kinds.
        metadata: Optional free-form tags/labels only (never data/secrets), consistent with
            `Node.config` / `JoinSpec.metadata`.
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: TriggerKind
    cron: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    event: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_kind_payload(self) -> Trigger:
        """Enforce the payload each `kind` requires/forbids.

        Every check tests the field **value** (`cron is None`/`not cron.strip()`, `not
        depends_on`, `event is None`) rather than `model_fields_set`. None of these payloads has
        a meaningful `null`/empty sentinel distinct from "not provided" (unlike
        `Transformation.constant`, which distinguishes an authored `constant: null` from an
        omitted one), so value-based checks are lossless — and critically, they stay lossless
        across a dump -> load cycle: `model_dump` always re-emits every field, including
        defaults (`cron: null`, `depends_on: []`, `event: null`), so a presence-based check would
        spuriously trip on reload.

        Raises:
            ValueError: If `kind` is `SCHEDULE` and `cron` is missing/empty/whitespace-only, or
                `depends_on`/`event` is set; if `kind` is `DEPENDENCY` and `depends_on` is empty,
                or `cron`/`event` is set; or if `kind` is `MANUAL` and `cron` is set or
                `depends_on` is non-empty.
        """
        if self.kind is TriggerKind.SCHEDULE:
            if self.cron is None or not self.cron.strip():
                raise ValueError("Trigger(kind=schedule) requires a non-empty 'cron' expression.")
            if self.depends_on:
                raise ValueError("Trigger(kind=schedule) must not set 'depends_on'.")
            if self.event is not None:
                raise ValueError("Trigger(kind=schedule) must not set 'event'.")
        elif self.kind is TriggerKind.DEPENDENCY:
            if not self.depends_on:
                raise ValueError("Trigger(kind=dependency) requires a non-empty 'depends_on' list.")
            if self.cron is not None:
                raise ValueError("Trigger(kind=dependency) must not set 'cron'.")
            if self.event is not None:
                raise ValueError("Trigger(kind=dependency) must not set 'event'.")
        else:  # MANUAL
            if self.cron is not None:
                raise ValueError("Trigger(kind=manual) must not set 'cron'.")
            if self.depends_on:
                raise ValueError("Trigger(kind=manual) must not set 'depends_on'.")

        return self


class CursorKind(StrEnum):
    """The closed set of watermark/cursor kinds a `CursorStrategy` may declare.

    A `StrEnum` (not a bare `Literal`), matching the `TransformationKind`/`JoinType`/
    `TriggerKind`/`GenericTestKind` convention elsewhere in this module: a named, importable type
    for orchestration to branch on, while it serializes to/from a plain string value stably in
    YAML and JSON. An out-of-set value fails Pydantic validation with a clear error.

    Attributes:
        TIMESTAMP: A time-ordered cursor (e.g. an ``updated_at`` field) — the resume bound is a
            monotonically increasing point in time. The default kind `CursorStrategy.
            from_incremental_cursor` maps a legacy `Endpoint.incremental_cursor` to.
        SEQUENCE: A monotonically increasing numeric/sequence id (e.g. an auto-increment row id).
        OPAQUE_TOKEN: A source-supplied continuation token treated as opaque — never parsed or
            ordered here, only carried for the source's own pagination/resume contract.
    """

    TIMESTAMP = "timestamp"
    SEQUENCE = "sequence"
    OPAQUE_TOKEN = "opaque_token"


class CursorStrategy(BaseModel):
    """A declarative watermark/cursor strategy attachable to a `Node`.

    Names the field a source/ingestion node advances on to resume incrementally, and the *kind*
    of value that field holds. This model is opaque and inert: it declares cursor *intent* only
    — **no state is ever read, written, or persisted here**. Persisting the last-successful
    cursor value per (source, entity) is the Orchestration/State layer's job, per
    `steering/00-project-overview.md` ("Track a watermark/cursor per source+entity in a control
    table; resume from last success", `steering/02-engineering.md`) and the implemented
    `WatermarkStore` backends in `dander.state.watermark`. Endpoint execution uses those stores;
    general graph-to-endpoint cursor binding is not implemented in this model.

    Modeled with the same shape as `Transformation`/`Trigger`/`FieldTest` (a `kind` discriminator
    plus a free-form payload), for internal consistency with the rest of this module and a single
    importable type for orchestration to branch on.

    Attributes:
        field: The cursor field name the source advances on (e.g. ``updated_at``). Referenced
            **by name only, never a value** (`steering/01-security.md`). Required and non-empty
            (after stripping) — a cursor strategy naming no field is meaningless.
        kind: The cursor discriminator — one of `CursorKind`'s closed set. Required — no default,
            since (unlike `Transformation.kind`, which defaults to `DIRECT`) there is no sensible
            default cursor kind; a strategy with no declared kind is meaningless.
        params: Free-form, kind-specific parameters (e.g. a timestamp format hint), matching the
            `Node.config`/`Transformation.arguments` precedent. Opaque and inert: stored
            as-authored for orchestration, never interpreted here, and —
            per `steering/01-security.md` — never a place for a secret/credential; names and
            parameters only.
        metadata: Free-form tags/labels only (never data/secrets), consistent with
            `JoinSpec.metadata` / `Trigger.metadata`.
    """

    model_config = ConfigDict(populate_by_name=True)

    field: str
    kind: CursorKind
    params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_field_present(self) -> CursorStrategy:
        """Enforce the boundary constraint: a cursor kind requires a non-empty cursor field.

        Uses `.strip()` (not a bare `Field(min_length=1)`) so a whitespace-only `field` is also
        rejected, matching the `EXPRESSION`-requires-`expression` check in `Transformation`.

        Raises:
            ValueError: If `field` is empty or whitespace-only.
        """
        if not self.field.strip():
            raise ValueError("CursorStrategy requires a non-empty 'field' name.")
        return self

    @classmethod
    def from_incremental_cursor(cls, cursor_field: str | None) -> CursorStrategy | None:
        """Map a legacy `Endpoint.incremental_cursor` string to a node-level `CursorStrategy`.

        The documented migration bridge from the narrow, pre-DANDER-18 ingestion-level cursor
        (just a field name, disconnected from the pipeline graph) to the node-level strategy this
        ticket introduces. Takes a plain `str | None` (not an `Endpoint`) so `dander.pipeline`
        gains no import dependency on `dander.ingestion`; the ingestion/orchestration layer calls
        this when it wants a `CursorStrategy` from a legacy endpoint.

        The historical `Source.extract(..., since=...)` contract treats the cursor as a
        monotonic "since" bound, which is timestamp-shaped by default — so a mapped cursor always
        gets `CursorKind.TIMESTAMP`. This is a documented assumption, not a general inference: a
        legacy endpoint whose cursor was actually a sequence/token must author an explicit
        node-level `CursorStrategy` directly instead of relying on this helper.

        Args:
            cursor_field: The legacy `Endpoint.incremental_cursor` value.

        Returns:
            `None` if `cursor_field` is `None` or empty; otherwise a `CursorStrategy` with
            `kind=CursorKind.TIMESTAMP`.
        """
        if not cursor_field:
            return None
        return cls(field=cursor_field, kind=CursorKind.TIMESTAMP)


class Position(BaseModel):
    """A 2-D canvas coordinate for a node.

    A cohesive value object for "where the node sits" on a future drag-drop UI's canvas. Units
    are opaque UI-canvas space; no range validation is performed here — that is a UI concern, out
    of scope for this declarative model.

    Attributes:
        x: Horizontal coordinate. Required within a `Position` — a half-specified coordinate is
            meaningless for placement, so declaring a position means giving both `x` and `y`.
        y: Vertical coordinate. Required within a `Position`, for the same reason as `x`.
    """

    model_config = ConfigDict(populate_by_name=True)

    x: float
    y: float


class NodeVisual(BaseModel):
    """Presentation/layout hints for one node, for the future drag-drop UI.

    This model is purely additive, presentation-only metadata: it is inert and carries no data
    values or execution semantics, and nothing in this codebase ever reads it to make a decision —
    exactly like `Node.trigger`/`Node.cursor` are inert declarative intent in this model. It is
    kept as its own clearly-named concern, separate from the free-form
    `Node.config` (data-shaping intent), so a visual editor's layout state never blurs into a
    node's core identity or data semantics.

    Attributes:
        position: Optional canvas position for this node. `None` means no position has been
            recorded; a UI may still persist a `color`/`icon` without one.
        color: Optional free-form presentation color (e.g. a hex code or design-token name).
            Validation of accepted color formats is deferred — a UI-facing concern, out of scope
            here. Never a place for a secret/credential (`steering/01-security.md`).
        icon: Optional free-form icon reference/name. Validation of accepted icon names is
            deferred, for the same reason as `color`.
    """

    model_config = ConfigDict(populate_by_name=True)

    position: Position | None = None
    color: str | None = None
    icon: str | None = None


class Node(BaseModel):
    """A single node in a pipeline graph (a data object or task).

    Attributes:
        id: Unique identifier for this node within its graph. Uniqueness is *not* enforced
            here (see DANDER-3).
        type: Node kind, e.g. ``source``/``transform``/``target``/``task``. Kept as a free
            string rather than a closed enum since validation of accepted values is deferred
            to DANDER-3. Modeled kinds (see `dander.pipeline.node_config.NodeType`) get a typed
            `config`; an unmodeled/future kind keeps the pre-DANDER-10 free-form `dict` behavior.
        name: Human-readable label.
        config: Node-specific data, discriminated by `type` (DANDER-10). For a modeled `type`
            (``source``/``transform``/``target``) this validates as the matching
            `dander.pipeline.node_config.NodeConfig` subclass — a `source` node rejects a
            `target`-shaped typed config and vice versa. For an unmodeled `type`, `config` stays
            a plain, free-form `dict` (the pre-DANDER-10, backward-compatible path). Accepts
            either the ``config`` or ``params`` key on load (both map to this one attribute);
            dumps under the canonical ``config`` key.
        fields: Ordered field schema the node produces (e.g. the columns a `source` node
            exposes). Defaults to empty — a node with no declared fields loads and dumps just
            as a DANDER-2 node did. Cross-node validation of field references is deferred to
            DANDER-8.
        trigger: Optional declarative per-node trigger/schedule (DANDER-14). `None` (the
            default) means the node carries no trigger and loads/dumps exactly as a pre-
            DANDER-14 node did. Whether a given `TriggerKind` is semantically meaningful on a
            node (vs. only at the pipeline level) is an Orchestration-layer concern, not a
            constraint enforced here — see `Trigger`.
        cursor: Optional declarative watermark/cursor strategy (DANDER-18). `None` (the default)
            means the node declares no incremental cursor and round-trips exactly as a pre-
            DANDER-18 node did. Supersedes the narrow `dander.ingestion.source.Endpoint.
            incremental_cursor` field name — see `CursorStrategy.from_incremental_cursor` for the
            migration bridge. State persistence is out of scope here (see
            `steering/00-project-overview.md`, `steering/02-engineering.md`, and
            `dander.state.watermark.WatermarkStore`) — this model only declares the strategy.
            Endpoint execution has concrete watermark stores, but general graph-to-endpoint
            binding is not implemented. Whether `cursor` is semantically meaningful on a given
            `node.type` is not enforced here, matching the deferred-`Node.type`-validation
            treatment of `Trigger`/`JoinSpec`.
        visual: Optional presentation/layout metadata for the future drag-drop UI named in this
            module's docstring (DANDER-19). `None` (the default) means the node carries no visual
            metadata and loads/dumps exactly as a pre-DANDER-19 node did. Purely additive and
            presentation-only — it never affects data/execution semantics and is deliberately kept
            separate from `config` (data-shaping intent); see `NodeVisual`.

    `hide_input_in_errors=True` is set because Pydantic's default `ValidationError` rendering
    embeds a repr of the rejected input value (`input_value=...`) alongside any raised
    `ValueError` message; without it, the type/config-mismatch error from `_route_config` would
    leak the mismatched config's content into the exception string even though the raised message
    itself only names class names (`steering/01-security.md`: no sensitive data in error
    messages).
    """

    model_config = ConfigDict(populate_by_name=True, hide_input_in_errors=True)

    id: str
    type: str
    name: str
    config: dict[str, Any] | SerializeAsAny[NodeConfig] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("config", "params"),
        validate_default=True,
        union_mode="left_to_right",
    )
    fields: list[NodeField] = Field(default_factory=list)
    trigger: Trigger | None = Field(default=None)
    cursor: CursorStrategy | None = Field(default=None)
    visual: NodeVisual | None = Field(default=None)

    @field_validator("config", mode="before")
    @classmethod
    def _route_config(cls, value: object, info: ValidationInfo) -> NodeConfig | dict[str, Any]:
        """Route the raw/typed `config` value to the model matching this node's `type`.

        Delegates to `dander.pipeline.node_config.resolve_node_config`. Runs after alias
        resolution (so the ``config``/``params`` alias keeps working) and after `type` has
        already been validated, since Pydantic validates fields in declaration order and `type`
        is declared before `config` — so `info.data["type"]` is guaranteed present here.

        Args:
            value: The raw `config` value being validated.
            info: Validation context; `info.data["type"]` is this node's already-validated
                `type`.

        Returns:
            The typed `NodeConfig` instance for a modeled `type`, or the unchanged/empty `dict`
            for an unmodeled `type`.

        Raises:
            ValueError: If `value` is an already-typed `NodeConfig` instance that does not match
                the model for this node's `type` (see `resolve_node_config`).
        """
        return resolve_node_config(info.data["type"], value)


class TransformationKind(StrEnum):
    """The closed set of transformation kinds a `Transformation` may declare.

    A `StrEnum` (not a bare `Literal`) so the Transform/Writer layer and DANDER-8 can branch on a
    named, importable type, while it still serializes to/from its plain string value stably in
    YAML and JSON. Extensible by adding a member later without touching callers.

    Attributes:
        DIRECT: A plain field-to-field copy — no expression, no constant.
        EXPRESSION: The target value is computed by an opaque, declarative expression string.
        CONSTANT: The target value is a fixed literal, independent of any source field.
        CUSTOM_CODE: The target value is computed by an allow-listed function, referenced by its
            registry name/key only (never an inline code string, lambda, or eval-able source).
            Resolved/executed downstream by the Transform/Writer layer, never here.
    """

    DIRECT = "direct"
    EXPRESSION = "expression"
    CONSTANT = "constant"
    CUSTOM_CODE = "custom_code"


_FUNCTION_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
"""Allow-listed shape for a `Transformation.function` registry key: one or more dotted
identifiers (e.g. ``transforms.normalize_phone``). Deliberately an allow-list, not a deny-list of
dangerous characters — it admits only known registry-key shapes rather than trying to enumerate
everything malicious. Anything containing spaces, parentheses, operators, quotes, colons, or
newlines (e.g. ``lambda x: x``, ``eval("x")``, ``a + b``) structurally fails to match, which is
the concrete enforcement of "referenced by name only, no arbitrary-string execution surface"
(`steering/01-security.md`).
"""


class Transformation(BaseModel):
    """A declarative transformation attached to a `FieldMapping`.

    Captures *what kind* of transformation a mapping performs and its declarative payload. This
    model is opaque and inert: an `expression` string is never parsed, compiled, or evaluated
    here, a `constant` literal is never interpreted, and a `CUSTOM_CODE` `function` reference is
    never resolved or invoked — all three are stored as-authored for the Transform/Writer layer
    to execute later, per `steering/00-project-overview.md`. Neither an `expression`, a
    `constant`, nor a `function`/`arguments` payload may embed a secret or credential literal
    (`steering/01-security.md`); a transformation references fields and functions, never values
    that belong in Secret Manager / env.

    Attributes:
        kind: The transformation discriminator. Defaults to `DIRECT` (a plain copy).
        expression: Opaque, declarative expression string for the `EXPRESSION` kind (e.g.
            ``"CONCAT(first_name, ' ', last_name)"``). Never evaluated here. Required and
            non-empty when `kind` is `EXPRESSION`; must be unset otherwise.
        constant: The literal payload for the `CONSTANT` kind. Typed `Any` because a constant is
            arbitrary JSON (str/int/float/bool/null/list/dict), matching the `Node.config`
            precedent. Required (including an explicit `null`) when `kind` is `CONSTANT`; must be
            unset otherwise. Presence — not truthiness — is what is checked, so a legitimate
            constant `null` is distinguishable from "not provided".
        function: The **function-registry key/name only** for the `CUSTOM_CODE` kind (e.g.
            ``"transforms.normalize_phone"``) — never an inline code string, lambda, or eval-able
            source. Typed `str` (never `Callable`), so Pydantic already rejects a callable at the
            boundary; a `field_validator` additionally constrains the value to a dotted-identifier
            shape (see `_FUNCTION_KEY_PATTERN`), which structurally excludes any eval-able source.
            Resolving and invoking the named function belongs entirely to the future
            Transform/Writer layer — nothing is looked up or executed here. Required and non-empty
            when `kind` is `CUSTOM_CODE`; must be unset otherwise.
        arguments: Optional declared arguments passed to the `CUSTOM_CODE` function: a
            name-to-value mapping whose values are literals or field-reference tokens (names,
            never secret values — `steering/01-security.md`). Any source-field reference among
            these should also be listed in `inputs` so DANDER-8 can resolve it. Must be empty for
            the other kinds (an empty `{}` is the default and is always permitted, so round-trips
            stay stable).
        inputs: Zero or more source-field names this transformation references, so a later
            validation pass (DANDER-8) can check they resolve. Names only, never values.
        metadata: Optional free-form tags.
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: TransformationKind = TransformationKind.DIRECT
    expression: str | None = None
    constant: Any = None  # arbitrary JSON literal for the CONSTANT kind; see Attributes above.
    function: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("function")
    @classmethod
    def _check_function_shape(cls, value: str | None) -> str | None:
        """Constrain `function` to an allow-listed, dotted-identifier registry-key shape.

        `None` (unset) passes through unchanged. A set value must be non-empty after stripping
        and must match `_FUNCTION_KEY_PATTERN` — this is the enforcement point for "referenced by
        name only, no inline code/lambda/eval source" (`steering/01-security.md`).

        Args:
            value: The raw `function` value being validated.

        Returns:
            `value` unchanged (or `None`).

        Raises:
            ValueError: If `value` is set but empty/whitespace-only, or does not match the
                allow-listed registry-key pattern.
        """
        if value is None:
            return None
        stripped = value.strip()
        if not stripped or not _FUNCTION_KEY_PATTERN.fullmatch(stripped):
            raise ValueError(
                "Transformation.function must be a dotted registry-key identifier "
                "(e.g. 'transforms.normalize_phone'), never an inline code string, lambda, or "
                "eval-able source."
            )
        return value

    @model_validator(mode="after")
    def _check_kind_payload(self) -> Transformation:
        """Enforce the payload each `kind` requires/forbids.

        The `CONSTANT`-requires-a-literal check uses `model_fields_set` (not truthiness) so an
        authored `constant: null` is distinguishable from an omitted `constant` on first parse.
        The "must not carry a constant" prohibitions for the other kinds instead check
        `self.constant is not None`: `model_dump` always serializes every field (including a
        default `constant: null`), so a `DIRECT`/`EXPRESSION` transformation that round-trips
        through dump -> load would otherwise always show `constant` as "set" on reload and
        spuriously fail this check. A `constant` value of `None` is never meaningful outside the
        `CONSTANT` kind, so checking its value (not its presence) here is lossless. The same
        reasoning applies to `function` (checked by value, `is not None`) and `arguments`
        (checked by truthiness, since its lossless default is the empty `{}` that `model_dump`
        always re-emits).

        Raises:
            ValueError: If `kind` is `EXPRESSION` and `expression` is missing/empty, or
                `constant`/`function`/`arguments` is set; if `kind` is `CONSTANT` and `constant`
                is not present, or `expression`/`function`/`arguments` is set; if `kind` is
                `CUSTOM_CODE` and `function` is missing/empty, or `expression`/`constant` is set;
                or if `kind` is `DIRECT` and any of `expression`, `constant`, `function`, or
                `arguments` is set.
        """
        if self.kind is TransformationKind.EXPRESSION:
            if self.expression is None or not self.expression.strip():
                raise ValueError(
                    "Transformation(kind=expression) requires a non-empty 'expression'."
                )
            if self.constant is not None:
                raise ValueError("Transformation(kind=expression) must not set 'constant'.")
            if self.function is not None:
                raise ValueError("Transformation(kind=expression) must not set 'function'.")
            if self.arguments:
                raise ValueError("Transformation(kind=expression) must not set 'arguments'.")
        elif self.kind is TransformationKind.CONSTANT:
            if "constant" not in self.model_fields_set:
                raise ValueError(
                    "Transformation(kind=constant) requires a 'constant' literal to be set."
                )
            if self.expression is not None:
                raise ValueError("Transformation(kind=constant) must not set 'expression'.")
            if self.function is not None:
                raise ValueError("Transformation(kind=constant) must not set 'function'.")
            if self.arguments:
                raise ValueError("Transformation(kind=constant) must not set 'arguments'.")
        elif self.kind is TransformationKind.CUSTOM_CODE:
            if self.function is None or not self.function.strip():
                raise ValueError(
                    "Transformation(kind=custom_code) requires a non-empty 'function' registry key."
                )
            if self.expression is not None:
                raise ValueError("Transformation(kind=custom_code) must not set 'expression'.")
            if self.constant is not None:
                raise ValueError("Transformation(kind=custom_code) must not set 'constant'.")
        else:  # DIRECT
            if self.expression is not None:
                raise ValueError("Transformation(kind=direct) must not set 'expression'.")
            if self.constant is not None:
                raise ValueError("Transformation(kind=direct) must not set 'constant'.")
            if self.function is not None:
                raise ValueError("Transformation(kind=direct) must not set 'function'.")
            if self.arguments:
                raise ValueError("Transformation(kind=direct) must not set 'arguments'.")

        return self


class FieldMapping(BaseModel):
    """A single field-to-field lineage mapping on an edge, optionally transformed.

    Column-level lineage for a connection: names the source-node field this mapping reads from
    and the target-node field it writes to, both by their field-name string (the `name`
    identifiers declared via `NodeField` in DANDER-4). By default (`transformation=None`, or an
    explicit `Transformation(kind=DIRECT)`) this is a direct-copy (passthrough/rename/project)
    mapping, matching DANDER-5's behavior unchanged. A `transformation` of kind `EXPRESSION` or
    `CONSTANT` (DANDER-6) attaches declarative transform logic — never evaluated here, only
    stored for the Transform/Writer layer. Validating that `source`/`target`/`transformation.
    inputs` actually exist on the edge's connected nodes is deferred (see DANDER-8).

    Attributes:
        source: The source-node field name this mapping reads from (on-disk key: ``source``).
            `None` for a **derived/computed** target field with no single source column; in that
            case `transformation` (kind `EXPRESSION`, `CONSTANT`, or `CUSTOM_CODE`) is required to
            supply the logic, and any referenced source fields are named in
            `transformation.inputs` instead.
        target: The target-node field name this mapping writes to (on-disk key: ``target``).
        transformation: Optional declarative transformation for this mapping. `None` means a
            plain direct copy (DANDER-5 default, backward compatible).
        metadata: Free-form tags/labels only (e.g. a lineage note). Per
            `steering/01-security.md`, this must never hold a real field value or sample data —
            labels/tags only.
    """

    model_config = ConfigDict(populate_by_name=True)

    source: str | None = None
    target: str
    transformation: Transformation | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_derived_mapping_has_transformation(self) -> FieldMapping:
        """Require a transformation when there is no single source field.

        A mapping with `source is None` produces nothing unless it carries the logic to derive
        its value, so it must declare a `transformation` of kind `EXPRESSION`, `CONSTANT`, or
        `CUSTOM_CODE`.

        Raises:
            ValueError: If `source` is `None` and `transformation` is missing, or is present but
                not `EXPRESSION`/`CONSTANT`/`CUSTOM_CODE` kind.
        """
        if self.source is None and (
            self.transformation is None
            or self.transformation.kind
            not in (
                TransformationKind.EXPRESSION,
                TransformationKind.CONSTANT,
                TransformationKind.CUSTOM_CODE,
            )
        ):
            raise ValueError(
                "FieldMapping with source=None (a derived field) requires a "
                "transformation of kind 'expression', 'constant', or 'custom_code'."
            )
        return self


class JoinType(StrEnum):
    """The closed set of join kinds a `JoinSpec` may declare.

    A `StrEnum` (not a bare `Literal`), matching the established convention in `writer/base.py`
    (`WriteMode`) and `transform/model.py` (`Materialization`) — it gives a named, importable
    type for the Transform layer to branch on later while serializing to/from a plain string
    value stably in YAML and JSON. An out-of-set value fails validation with a clear error.

    Attributes:
        INNER: Only rows with matching keys on both sides.
        LEFT: All rows from the left (edge `from`) side; unmatched right-side fields are null.
        RIGHT: All rows from the right (edge `to`) side; unmatched left-side fields are null.
        FULL: All rows from both sides; unmatched fields on either side are null.
    """

    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"


class JoinKeyPair(BaseModel):
    """One equality key pairing in a `JoinSpec`.

    Pairs a field name on the join's left side with a field name on its right side. Referenced
    strictly **by field name**, never by value (`steering/01-security.md`). Field-existence
    checks (whether these names resolve on the joined nodes) are deferred to DANDER-8.

    Attributes:
        left: Field name on the edge's **from** node (the join's left side; see `JoinSpec` and
            `Edge.join` for the left/right orientation).
        right: Field name on the edge's **to** node (the join's right side).
    """

    model_config = ConfigDict(populate_by_name=True)

    left: str
    right: str


class JoinSpec(BaseModel):
    """A declarative join specification on a connection that combines two sources.

    Names the join **type** and the ordered equality key pairs used to combine an edge's two
    endpoints. This model is opaque and inert: no SQL is generated and no join is executed here
    — it records join *intent* only, for the Transform layer to execute later
    (`steering/00-project-overview.md`). Cross-node validation that the key-pair field names
    exist on the joined nodes is deferred to DANDER-8.

    **Left/right orientation:** the join's left side is always the edge's `from` node
    (`Edge.source`) and the right side is always the edge's `to` node (`Edge.target`). Each
    `JoinKeyPair` pairs a field on the left (`from`) node with a field on the right (`to`) node.
    *Left*/*right* is used here (rather than *source*/*target*) deliberately: on `Edge`,
    `source`/`target` already name node **ids**, while here we name field **names** on those
    nodes — keeping the vocabularies distinct avoids conflating the two meanings.

    Attributes:
        type: The join kind — one of `JoinType`'s closed set. Invalid values raise a
            `ValidationError` at the Pydantic boundary.
        keys: Ordered equality key pairs; at least one is required (an empty list raises a
            `ValidationError`). Declaration order is preserved.
        metadata: Free-form tags/labels only (never data/secrets), consistent with
            `Edge.metadata` / `Node.config`.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: JoinType
    keys: list[JoinKeyPair] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    """A directed connection between two node ids.

    The on-disk/API keys are the reserved words ``from``/``to``; since ``from`` cannot be a
    Python attribute name, this model exposes the Python-safe attributes ``source``/``target``
    and maps them to ``from``/``to`` via Pydantic field aliases. Both by-alias and by-attribute-
    name population work; dumps always emit the ``from``/``to`` aliases.

    Attributes:
        source: The originating node id (on-disk key: ``from``).
        target: The destination node id (on-disk key: ``to``).
        metadata: Optional free-form edge metadata.
        mappings: Ordered field-to-field lineage across this connection. Defaults to empty — an
            edge with no mappings loads and dumps just as a DANDER-2/DANDER-4 edge did.
            Cross-node validation that a mapping's `source`/`target` field names exist on the
            connected nodes is deferred to DANDER-8.
        join: Optional declarative join specification for a connection that combines two
            sources. `None` (the default) means a plain edge with no join — unchanged and
            backward compatible with DANDER-2/4/5 graphs. When present, the join's left side is
            this edge's `source` (`from`) node and its right side is this edge's `target` (`to`)
            node; see `JoinSpec` for the full orientation contract. No cross-node field-existence
            validation happens here (see DANDER-8).
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    source: str = Field(alias="from")
    target: str = Field(alias="to")
    metadata: dict[str, Any] = Field(default_factory=dict)
    mappings: list[FieldMapping] = Field(default_factory=list)
    join: JoinSpec | None = Field(default=None)


class PipelineGraph(BaseModel):
    """The full pipeline graph: a named collection of nodes and edges.

    Attributes:
        name: Human-readable graph name.
        nodes: The graph's nodes.
        edges: The graph's edges.
        trigger: Optional declarative pipeline-level trigger/schedule (DANDER-14). `None` (the
            default) means the graph carries no trigger and loads/dumps exactly as a pre-
            DANDER-14 graph did. Declarative only — see `Trigger`; the separately provisioned
            Cloud Scheduler runtime does not consume this model.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    trigger: Trigger | None = Field(default=None)


def load_graph_from_yaml(path: Path) -> PipelineGraph:
    """Load a `PipelineGraph` from a YAML file.

    Args:
        path: Path to a YAML file containing a `nodes:`/`edges:` document.

    Returns:
        The parsed `PipelineGraph`.
    """
    raw = yaml.safe_load(path.read_text())
    return PipelineGraph.model_validate(raw)


def load_graph_from_json(path: Path) -> PipelineGraph:
    """Load a `PipelineGraph` from a JSON file.

    Args:
        path: Path to a JSON file containing a `nodes:`/`edges:` document.

    Returns:
        The parsed `PipelineGraph`.
    """
    return PipelineGraph.model_validate_json(path.read_text())


def _dump_graph_payload(graph: PipelineGraph) -> dict[str, Any]:
    """Build the on-disk payload dict for a graph, omitting only join-less `join`,
    spec-less `request`, writer-less `writer`, trigger-less `trigger`, cursor-less `cursor`, and
    visual-less `visual` keys.

    Backing helper shared by `dump_graph_to_yaml`/`dump_graph_to_json`. A plain
    `graph.model_dump(by_alias=True, mode="json")` would already emit a `join: null` entry for
    every edge with no join (backward-incompatible with DANDER-2/4/5 graphs), a `request: null`
    entry in every `source` node's `config` that declares no request spec (DANDER-11), a
    `writer: null` entry in every `target` node's `config` that declares no writer config
    (DANDER-16), a `trigger: null` entry on every graph/node with no trigger (DANDER-14), a
    `cursor: null` entry on every node with no cursor strategy (DANDER-18), and — since DANDER-19
    — a `visual: null` entry on every node with no visual/layout metadata. A graph-wide
    `exclude_none=True` fixes all of these but is too blunt: it also drops other, *meaningful*
    `None` values elsewhere in the graph — notably an authored `constant: null` on a `CONSTANT`
    `Transformation`, which then fails to reload (`Transformation(kind=constant) requires a
    'constant' literal to be set`). So the omission is scoped here, after the fact, to exactly the
    `join` key of edges whose `Edge.join` is `None`, the `request` key of a `source` node's
    `config` whose `SourceNodeConfig.request` is `None`, the `writer` key of a `target` node's
    `config` whose `TargetNodeConfig.writer` is `None`, the `trigger` key of the graph itself
    (when `graph.trigger is None`) and of each node (when `node.trigger is None`), the `cursor`
    key of each node (when `node.cursor is None`), and the `visual` key of each node (when
    `node.visual is None`) — every other field, including other `None`s (and any inner `None`
    *within* a present `visual` block, e.g. `position: null` when only a color is set), is left
    untouched.

    Args:
        graph: The graph to serialize.

    Returns:
        A plain JSON-compatible dict (nested dicts/lists/primitives) ready for `yaml.safe_dump`
        or `json.dumps`, with a join-less edge's `join` key, a spec-less source node's `request`
        key, a writer-less target node's `writer` key, a trigger-less graph's/node's `trigger`
        key, a cursor-less node's `cursor` key, and a visual-less node's `visual` key absent
        rather than `null`.
    """
    payload = graph.model_dump(by_alias=True, mode="json")
    for edge, dumped_edge in zip(graph.edges, payload["edges"], strict=True):
        if edge.join is None:
            dumped_edge.pop("join", None)
    for node, dumped_node in zip(graph.nodes, payload["nodes"], strict=True):
        config = node.config
        if isinstance(config, SourceNodeConfig) and config.request is None:
            dumped_config = dumped_node.get("config")
            if isinstance(dumped_config, dict):
                dumped_config.pop("request", None)
        if isinstance(config, TargetNodeConfig) and config.writer is None:
            dumped_config = dumped_node.get("config")
            if isinstance(dumped_config, dict):
                dumped_config.pop("writer", None)
        if node.trigger is None:
            dumped_node.pop("trigger", None)
        if node.cursor is None:
            dumped_node.pop("cursor", None)
        if node.visual is None:
            dumped_node.pop("visual", None)
    if graph.trigger is None:
        payload.pop("trigger", None)
    return payload


def graph_to_payload(graph: PipelineGraph) -> dict[str, Any]:
    """Return Dander's canonical JSON-compatible graph document.

    This public transport seam applies the same narrowly scoped omission rules as the YAML/JSON
    writers without touching the filesystem. It is the single serialization source used by the
    Control contract fixtures; callers must still validate graph semantics separately.
    """
    return _dump_graph_payload(graph)


def dump_graph_to_yaml(graph: PipelineGraph, path: Path) -> None:
    """Dump a `PipelineGraph` to a YAML file.

    Edges are serialized with the `from`/`to` keys (never `source`/`target`), matching the
    decided on-disk format. A join-less edge omits its `join` key entirely, a spec-less
    `source` node omits its `config.request` key entirely, a writer-less `target` node omits its
    `config.writer` key entirely, a trigger-less graph/node omits its `trigger` key entirely, a
    cursor-less node omits its `cursor` key entirely, and a visual-less node omits its `visual`
    key entirely (see `_dump_graph_payload`); no other `None` value anywhere in the graph is
    dropped — in particular an authored `constant: null` on a `CONSTANT` transformation is
    preserved.

    Args:
        graph: The graph to serialize.
        path: Destination file path; overwritten if it already exists.
    """
    payload = _dump_graph_payload(graph)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def dump_graph_to_json(graph: PipelineGraph, path: Path, *, indent: int = 2) -> None:
    """Dump a `PipelineGraph` to a JSON file.

    Edges are serialized with the `from`/`to` keys (never `source`/`target`), matching the
    decided on-disk format. A join-less edge omits its `join` key entirely, a spec-less
    `source` node omits its `config.request` key entirely, a writer-less `target` node omits its
    `config.writer` key entirely, a trigger-less graph/node omits its `trigger` key entirely, a
    cursor-less node omits its `cursor` key entirely, and a visual-less node omits its `visual`
    key entirely (see `_dump_graph_payload`); no other `None` value anywhere in the graph is
    dropped — in particular an authored `constant: null` on a `CONSTANT` transformation is
    preserved.

    Args:
        graph: The graph to serialize.
        path: Destination file path; overwritten if it already exists.
        indent: JSON indentation width.
    """
    payload = _dump_graph_payload(graph)
    path.write_text(json.dumps(payload, indent=indent))
