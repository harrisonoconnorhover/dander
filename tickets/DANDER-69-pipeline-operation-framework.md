---
id: DANDER-69
title: Add PipelineOperation framework and OperationKind node wiring
status: done
component: python
epic: pipeline-operations
depends_on: []
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Satisfied on the current trunk (`teammate/main`, adopted as local `main`): `src/dander/pipeline/
operations.py` (`OperationKind`, `OperationSpec`, wired onto `TransformNodeConfig.operations`,
compiled to explicit BigQuery CTEs). Its own module docstring credits this exact ticket line:
"The operation vocabulary originates in Josh Wagner's generic pipeline-operation work at
`WagnerJ-Dev/dander@574d2f0`." No further action needed. See `docs/decisions.md`, "2026-08-05 —
Pipeline operations execute after raw ingestion."

## Context

Layer 1 (DANDER-64..68) makes connectors declare what they *can do*. Layer 2 is the complementary
half: **connector-agnostic, declarative per-node operations** that shape a record stream or run SQL
around a write, configured in pipeline/node YAML rather than in connector code. This is the
"config-driven over code-driven" rule in `steering/02-engineering.md` — a new transformation should
be a config entry, not new code.

This ticket adds the **Layer 2 foundation**: an `OperationKind` `StrEnum` (matching the `StrEnum`
convention used by `NodeType`, `WriteMode`, `TransformationKind`) and a `PipelineOperation` ABC
with an `apply()` method that transforms an in-flight record stream. It also wires an ordered list
of operations into the typed node config (`src/dander/pipeline/node_config.py`, alongside
`SourceNodeConfig`/`TransformNodeConfig`/`TargetNodeConfig` and the `NodeConfig`/`resolve_node_config`
mechanism) so operations are declared in node YAML and validated at load time. Concrete operations
(DANDER-70..72) depend on this and register against it.

Scope guard: this ticket defines the ABC, the enum, the config wiring, and the dispatch/registry
seam only — it ships **no** concrete operation. It must not duplicate the generic row *validation*
tests (`not_null`/`unique`/`accepted_values`/`relationships`) that already exist as
`GenericTestMetadata` in `src/dander/transform/config.py` (and `FieldTest` in
`src/dander/pipeline/graph.py`); operations are stream *transformations*, and the design should note
where validation remains that separate concern. Deferred Common/Advanced operations (`change_case`,
`regex_replace`, `cast_type`, `parse_date`, `mask_field`, `hash_field`, `json_extract`,
`flatten_nested`, etc.) are backlog, not part of this batch.

## Acceptance Criteria

- [ ] A new module (`src/dander/pipeline/operations.py`, or the best-fit location alongside the
      existing `pipeline` modules) defines an `OperationKind` `StrEnum` and a `PipelineOperation`
      ABC.
- [ ] `PipelineOperation` declares an abstract `apply()` that takes and returns an iterable/iterator
      of `Mapping[str, Any]` records, consistent with the record shape from `Source.extract()`.
- [ ] A registry/dispatch maps each `OperationKind` to its concrete class in one place, so
      DANDER-70..72 add an operation by adding an enum member + class + one registry entry without
      editing the framework's dispatch logic.
- [ ] Node config carries an **ordered** list of declarative operations, added to
      `src/dander/pipeline/node_config.py`, validated at load time (an unknown `OperationKind` fails
      at the Pydantic boundary; order is preserved and applied in declared order).
- [ ] Operations declared in node YAML round-trip losslessly through the graph
      serialization/`resolve_node_config` path with no regression to existing node configs.
- [ ] No `PipelineOperation` config holds a secret or credential value; it references data by field
      name only (`steering/01-security.md`).
- [ ] The design notes that generic row validation stays in `GenericTestMetadata` and is not
      reimplemented as an operation.
- [ ] Unit tests cover: an ABC that cannot be instantiated, registry dispatch by `OperationKind`,
      declared-order preservation, and rejection of an unknown kind (no network, per
      `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

This is the Layer 2 mirror of the Layer 1 connector-capability foundation (DANDER-64): a **new
`src/dander/pipeline/operations.py` module** that owns the `OperationKind` `StrEnum`, the
`PipelineOperation` ABC, the declarative `OperationSpec` config model, and a **single-source
registry + dispatch seam** — and ships **no concrete operation**. DANDER-70/71/72 attach
implementations on top of it.

The module divides cleanly along the config-vs-behavior split that `steering/languages/python.md`
mandates and that the codebase already follows (declarative Pydantic models vs. inert runtime
value objects):

- **`OperationKind`** — the closed, importable vocabulary of operation kinds, a `StrEnum` exactly
  like `NodeType`/`WriteMode`/`TransformationKind`/`GenericTestKind`/`ConnectorOperation`. It ships
  with the **Core vocabulary already enumerated** (`TRUNCATE_STRING`, `TRIM_WHITESPACE`,
  `DEFAULT_VALUE`, `RENAME_FIELD`, `DROP_FIELD`, `FILTER_ROWS`, `DEDUPLICATE`) so config authored
  against DANDER-70..72's kinds validates and round-trips **now**, before those concrete classes
  exist. This mirrors DANDER-64, whose `ConnectorOperation` enumerates the Core capabilities up
  front while the concrete Protocols land in DANDER-65..68. Enumerating a *name* is not shipping a
  concrete operation. (See the ambiguity note below on criterion 3's "add an enum member" wording.)

- **`OperationSpec`** — the declarative, per-operation config entry (`kind` + free-form `params`),
  modeled with the same "`kind` discriminator + opaque payload" shape as `Trigger`/`CursorStrategy`/
  `Transformation`. This is what node YAML declares. It is inert: nothing here reads `params` or
  runs an operation. `params` carries **field names and non-secret literals only** — never a
  secret/credential value (`steering/01-security.md`), the same contract as
  `Transformation.arguments` / `Node.config`. `hide_input_in_errors=True` (same rationale as
  `Node`/`WriterConfig`/`RequestSpec`) so a `ValidationError` never echoes a rejected `params`
  block into the exception string.

- **`PipelineOperation`** (ABC) — the behavior contract. One abstract method
  `apply(records) -> Iterator[Mapping[str, Any]]` that takes and returns an iterable/iterator of
  `Mapping[str, Any]`, exactly the record shape `Source.extract()` yields
  (`src/dander/ingestion/source.py`), so operations chain directly onto an extract stream. A second
  abstract classmethod `from_spec(cls, spec: OperationSpec) -> PipelineOperation` is the uniform
  construction seam: each concrete op (DANDER-70..72) validates its own `params` (typically via a
  private Pydantic params model) and returns a configured instance. The ABC itself cannot be
  instantiated (both members abstract).

- **Registry + dispatch** — a module-level `_OPERATION_REGISTRY: dict[OperationKind,
  type[PipelineOperation]]` that ships **empty**, plus a `register_operation(kind)` class decorator
  that sets `cls.kind` and inserts the one entry. This is the "one place, one entry" extension seam:
  DANDER-70..72 add a concrete class decorated with `@register_operation(OperationKind.X)` and touch
  **no framework dispatch logic**. Crucially the framework module never imports the concrete-op
  modules (they import *it* and self-register), so there is no import cycle and no framework edit per
  new op. Dispatch: `operation_for(kind) -> type[PipelineOperation]` raises the typed
  `UnregisteredOperationError(kind)` for a kind with no class yet; `build_operations(specs) ->
  list[PipelineOperation]` resolves and constructs each spec in declared order; a thin
  `apply_operations(records, ops)` chains their `apply()` calls left-to-right for callers.

**Two distinct validation moments** (this is the key design decision that lets the framework ship
before any concrete op and still satisfy every criterion):
1. **Config-load time** (Pydantic boundary): `OperationSpec.kind` must be an `OperationKind` member.
   An unknown kind string fails with a `ValidationError` right there. Registry presence is **not**
   required here, so an `operations` list round-trips losslessly today even though no class is
   registered yet.
2. **Execution-build time** (`operation_for`/`build_operations`): the kind must resolve to a
   registered class, else `UnregisteredOperationError`. This is a separate, typed, non-Pydantic
   error naming only the kind (no secrets, no row data).

### Config wiring (node_config.py)

Add `operations: list[OperationSpec] = Field(default_factory=list)` to the **base `NodeConfig`**, so
every modeled node type (`source`/`transform`/`target`) inherits an ordered operations list —
"connector-agnostic, per-node operations." `list` preserves and applies declared order; Pydantic
validates each member and rejects an unknown `OperationKind` at load. Import `OperationSpec` from
`operations.py` as a real (non-`TYPE_CHECKING`) import, matching the existing `RequestSpec` /
`WriteMode` import note in this module (Pydantic resolves the annotation at schema-build time).
`resolve_node_config` needs **no change** — operations ride along inside the typed config models it
already routes.

Import direction stays acyclic: `operations.py` imports only stdlib + Pydantic; `node_config.py`
imports `OperationSpec` from `operations.py`; `graph.py` imports from `node_config.py`. No cycle.

### Serialization round-trip (graph.py)

Extend `_dump_graph_payload` to **omit an empty `operations` list** from each node's dumped `config`,
following the exact established pattern that already drops join-less `join`, spec-less `request`,
writer-less `writer`, trigger-less `trigger`, cursor-less `cursor`, and visual-less `visual`. Without
this, `model_dump` would emit `operations: []` into every node's config and a pre-DANDER-69 graph
would no longer round-trip byte-identically — the "no regression to existing node configs" criterion.
The pop is scoped: for each node whose `config` is a `NodeConfig` instance with an empty
`operations`, remove the `operations` key from the dumped config dict. A node *with* operations dumps
them in order and reloads identically (lossless round-trip criterion).

### Interfaces / classes

- `OperationKind(StrEnum)` — Core vocabulary members (see above). `operations.py`.
- `OperationSpec(BaseModel)` — `kind: OperationKind`, `params: dict[str, Any] = {}`,
  `metadata: dict[str, Any] = {}` (tags only). `hide_input_in_errors=True`,
  `populate_by_name=True`. `operations.py`.
- `PipelineOperation(ABC)` — `kind: ClassVar[OperationKind]` (set by the registration decorator);
  abstract `apply(self, records: Iterable[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]`;
  abstract classmethod `from_spec(cls, spec: OperationSpec) -> PipelineOperation`. `operations.py`.
- `register_operation(kind: OperationKind)` — class decorator; sets `cls.kind`, inserts
  `_OPERATION_REGISTRY[kind] = cls`, returns `cls`. `operations.py`.
- `operation_for(kind) -> type[PipelineOperation]`; `build_operations(specs) ->
  list[PipelineOperation]`; `apply_operations(records, ops) -> Iterator[...]`. `operations.py`.
- `UnregisteredOperationError(Exception)` — typed dispatch error; message names the kind only.
  `operations.py` (or `pipeline/errors.py` if colocating with the existing graph errors reads
  cleaner — Code agent's call; keep the message secret-free either way).
- `NodeConfig` (edited) — gains `operations: list[OperationSpec]`.

### Files to touch / create

- **create** `src/dander/pipeline/operations.py` — enum, `OperationSpec`, `PipelineOperation` ABC,
  registry + `register_operation` + `operation_for` + `build_operations` + `apply_operations`,
  `UnregisteredOperationError`. Module + class + method docstrings (Google style); module docstring
  states it ships the framework only, no concrete op, and that generic row *validation* stays in
  `GenericTestMetadata`.
- **edit** `src/dander/pipeline/node_config.py` — import `OperationSpec`; add `operations` field to
  `NodeConfig`; extend the class docstring (the operations list is per-node, ordered, and holds no
  secret).
- **edit** `src/dander/pipeline/graph.py` — extend `_dump_graph_payload` (+ its docstring, and the
  `dump_graph_to_yaml`/`dump_graph_to_json` docstrings) to omit an empty `operations` list; touch
  `Node`/module docstrings only if a reference is warranted.
- **edit** `src/dander/pipeline/__init__.py` — export `OperationKind`, `OperationSpec`,
  `PipelineOperation`, `register_operation`, `operation_for`, `build_operations`,
  `apply_operations`, `UnregisteredOperationError`; add to `__all__`.
- **create** `tests/pipeline/test_operations.py` — see Test seams.
- Docs (`src/dander/pipeline/README.md`) note the new Layer 2 seam — a documentation-agent follow-up,
  not required for PASS.

### Separation of concerns — validation stays in GenericTestMetadata

Per the scope guard: `PipelineOperation`s are **stream transformations** (they reshape records).
Generic row **validation** (`not_null`/`unique`/`accepted_values`/`relationships`) is a separate
concern that already lives as `GenericTestMetadata` in `src/dander/transform/config.py` (and the
graph-level `FieldTest` in `src/dander/pipeline/graph.py`) and is **not** reimplemented here. The
`operations.py` module docstring records this boundary explicitly. Note for DANDER-72: its
`filter_rows` is predicate-based *exclusion* (a transformation that drops non-matching rows), which
is deliberately distinct from a validation *assertion* that fails a run — that distinction is
DANDER-72's to document, but the framework does nothing that blurs it.

### Test seams (tests/pipeline/test_operations.py — in-memory only, no network)

Define a **test-local concrete op** (a trivial `PipelineOperation` registered against a real
`OperationKind` member via `register_operation`, e.g. a pass-through or tag-adder) inside the test
module, so the framework is exercised without any shipped concrete op. Cover:
- **ABC cannot be instantiated** — `PipelineOperation()` raises `TypeError` (abstract methods).
- **Registry dispatch by `OperationKind`** — `operation_for(kind)` / `build_operations([spec])`
  returns the registered test op; `apply()` runs over in-memory records.
- **Declared-order preservation** — a `NodeConfig`/node with several `operations` keeps and applies
  them in authored order (assert on `config.operations` order and on `build_operations` output
  order).
- **Unknown kind rejected at the Pydantic boundary** — `OperationSpec(kind="does_not_exist")` /
  loading node YAML with a bogus kind raises `ValidationError`.
- **Unregistered (but valid) kind at dispatch** — `operation_for` on an `OperationKind` member with
  no registered class raises `UnregisteredOperationError` naming the kind.
- **Round-trip** — a graph whose node declares `operations` dumps→reloads losslessly (YAML + JSON);
  a node with **no** operations dumps **without** an `operations` key (no regression), asserted
  against `tmp_path`, matching `test_node_config.py` style.
- Isolate registry mutation (register the test op inside a fixture and pop it in teardown, or use a
  dedicated kind) so the module-level registry doesn't leak across tests.

### Trade-offs

- **Operations on base `NodeConfig` vs. per-subclass.** Chose the base so all three node types share
  one ordered operations list — operations are connector-agnostic and node-positioned, and DANDER-70
  (`pre_sql`/`post_sql`) explicitly treats them as a per-node phase. A single field also keeps the
  serialization-omission logic to one branch.
- **Decorator registry vs. a literal `{kind: class}` dict in the framework.** A literal map would
  force `operations.py` to import every concrete-op module → an import cycle and a framework edit per
  op (violating "no editing the framework's dispatch logic"). The self-registering decorator inverts
  the dependency: ops depend on the framework, never the reverse. Cost: a module-level side effect at
  op import; accepted, since registration *is* the intended effect (the plugin pattern), and the
  framework module itself stays side-effect-free on import.
- **Enum ships the Core vocabulary vs. ships empty.** Chose "ships the vocabulary" to match DANDER-64
  and to make config authored against DANDER-70..72 kinds validate and round-trip immediately, and to
  make this ticket's own round-trip/order tests expressible with real enum members. An empty enum
  would make the `operations` field unsatisfiable and untestable in isolation.
- **Two-stage validation (config vs. dispatch).** Keeps config-load strict on *vocabulary* while
  letting implementations land incrementally; the typed `UnregisteredOperationError` gives a clear,
  secret-free failure for a not-yet-implemented kind instead of a `KeyError`/`AttributeError`.

### Ambiguity flagged (per Design-agent instructions)

Criterion 3 says DANDER-70..72 "add an operation by adding an **enum member** + class + one registry
entry." This design ships the Core `OperationKind` members up front (DANDER-64 precedent), so for the
Core ops DANDER-70..72 add **class + registry entry only** (the enum member already exists); a genuine
enum-member addition is the path for any op **beyond** the shipped Core vocabulary (the deferred
Common/Advanced backlog: `change_case`, `regex_replace`, `cast_type`, …). The registry/decorator seam
supports both paths identically. If the intent is instead that DANDER-69 ship an **empty**
`OperationKind` and every later op add its own member, the `operations` config field cannot be
validated or round-trip-tested in this ticket (no valid kind exists), so the foundation would be
untestable in isolation — hence this design's choice. Calling it out so PR-Review can confirm the
reading rather than treating the Core members as out-of-scope.

## Implementation Notes

Implemented exactly per the Design section — no deviations.

**Created `src/dander/pipeline/operations.py`:**
- `OperationKind(StrEnum)` with the Core vocabulary: `TRUNCATE_STRING`, `TRIM_WHITESPACE`,
  `DEFAULT_VALUE`, `RENAME_FIELD`, `DROP_FIELD`, `FILTER_ROWS`, `DEDUPLICATE`.
- `OperationSpec(BaseModel)` — `kind: OperationKind`, `params: dict[str, Any] = {}`,
  `metadata: dict[str, Any] = {}`; `hide_input_in_errors=True`, `populate_by_name=True`.
- `PipelineOperation(ABC)` — `kind: ClassVar[OperationKind]`; abstract `apply(records:
  Iterable[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]`; abstract classmethod
  `from_spec(cls, spec: OperationSpec) -> PipelineOperation`. Both members abstract, so the ABC
  itself cannot be instantiated (`TypeError`).
- `_OPERATION_REGISTRY: dict[OperationKind, type[PipelineOperation]]` — ships **empty**.
  `register_operation(kind)` class decorator sets `cls.kind` and inserts the registry entry.
  `operation_for(kind)` raises `UnregisteredOperationError(kind)` for a valid-but-unregistered
  kind; `build_operations(specs)` resolves+constructs each spec in declared order;
  `apply_operations(records, ops)` chains `apply()` calls left-to-right.
- `UnregisteredOperationError(Exception)` lives in `operations.py` (not `pipeline/errors.py`),
  mirroring where `dander.ingestion.capabilities.UnsupportedConnectorOperationError` lives
  relative to its own module — kept colocated with the registry it guards rather than the
  graph-structural error hierarchy in `errors.py`, which is about a different concern
  (structural/field-wiring validation of the graph itself).
- Module imports only stdlib + Pydantic; `Iterable`/`Iterator`/`Mapping`/`Sequence`/`Callable`
  are `TYPE_CHECKING`-only (never referenced inside a Pydantic model field, so no
  `PydanticUserError` risk, matching the `capabilities.py`/`source.py` precedent); `Any`/
  `ClassVar` are real imports since `Any` appears inside `OperationSpec`'s field types.

**Edited `src/dander/pipeline/node_config.py`:** added `operations: list[OperationSpec] =
Field(default_factory=list)` to the base `NodeConfig` (real, non-`TYPE_CHECKING` import of
`OperationSpec`, matching the existing `RequestSpec`/`WriteMode` import note). All three modeled
node types (`SourceNodeConfig`/`TransformNodeConfig`/`TargetNodeConfig`) inherit it. Module and
class docstrings updated.

**Edited `src/dander/pipeline/graph.py`:** extended `_dump_graph_payload` to pop the `operations`
key from a node's dumped `config` dict whenever that node's `NodeConfig.operations` is empty —
the same scoped-omission pattern already used for `join`/`request`/`writer`/`trigger`/`cursor`/
`visual`. Updated `_dump_graph_payload`'s, `dump_graph_to_yaml`'s, and `dump_graph_to_json`'s
docstrings to document it. No change to `resolve_node_config` (operations ride along inside the
typed config models it already routes, as the Design anticipated).

**Edited `src/dander/pipeline/__init__.py`:** re-exports `OperationKind`, `OperationSpec`,
`PipelineOperation`, `register_operation`, `operation_for`, `build_operations`,
`apply_operations`, `UnregisteredOperationError`, added to `__all__`.

**Created `tests/pipeline/test_operations.py`** (14 tests, no network): ABC cannot be
instantiated; registry dispatch (`operation_for`/`build_operations`) resolves a fixture-registered
test-local op and its `apply()` runs over in-memory records; `UnregisteredOperationError` for a
valid-but-unregistered kind (`operation_for` and `build_operations`); declared-order preservation
through `build_operations`/`apply_operations` and through `NodeConfig.operations`; unknown kind
rejected at the `OperationSpec` Pydantic boundary and when loading node YAML; YAML+JSON round-trip
of a node's declared `operations` (order-preserving, model-equal after reload); an operations-less
node omits the `operations` key entirely on both YAML and JSON dump (asserted both structurally
and via a raw-text substring check); a pre-DANDER-69 graph with no `operations` anywhere still
loads/round-trips unchanged. Registry mutation is isolated via a `pytest.fixture` that registers a
trivial `_TagAdderOperation` against `OperationKind.TRIM_WHITESPACE` and pops it from
`_OPERATION_REGISTRY` in a `finally` block; `OperationKind.DEDUPLICATE` is used, unregistered, to
prove `UnregisteredOperationError` for a shipped-but-unimplemented kind.

**Docs:** `src/dander/pipeline/README.md` was **not** updated — per the ticket's own Files-to-Touch
note, that's flagged as a documentation-agent follow-up, not required for PASS.

**Tooling:** `ruff check` / `ruff format --check` / `mypy` (strict, `src/` — 74 files, no issues)
all clean on the touched files. `pytest tests/pipeline` — 100% pass (added tests: 14/14). Full
`pytest` run: all tests pass except 6 pre-existing failures in `tests/cli/` unrelated to this
ticket (ANSI/Rich color-code assertions in `test_catalog_cli.py`/`test_cli.py`/
`test_metadata_cli.py`/`test_transform_cli.py`) — verified failing identically on `main` before
this change via `git stash`.

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — PR-Review — **PASS**

Reviewed `src/dander/pipeline/operations.py` (new), and the diffs to `node_config.py`, `graph.py`,
`__init__.py`, plus `tests/pipeline/test_operations.py`. Every acceptance criterion is met and
verifiable; no blocking issues.

**Acceptance criteria — verified**

1. *Module with `OperationKind` StrEnum + `PipelineOperation` ABC* — `src/dander/pipeline/operations.py`
   defines `OperationKind(StrEnum)` (7 Core members) and `PipelineOperation(ABC)`. ✅
2. *Abstract `apply()` over `Mapping[str, Any]` records* — `apply(self, records:
   Iterable[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]`, matching `Source.extract`'s
   `Iterator[Mapping[str, Any]]` (`src/dander/ingestion/source.py:375`) exactly. ✅
3. *Single-place registry/dispatch, no framework edits per new op* — `_OPERATION_REGISTRY` +
   `register_operation(kind)` class decorator + `operation_for`/`build_operations`. The framework
   never imports a concrete-op module, so DANDER-70..72 add class + `@register_operation(...)` only.
   The Design's flagged ambiguity on "add an enum member" is **confirmed as read**: shipping the
   Core vocabulary up front mirrors `ConnectorOperation` (DANDER-64) and is what makes the
   `operations` field validatable/round-trippable in isolation; the decorator seam supports a genuine
   new enum member (deferred Common/Advanced backlog) identically. ✅
4. *Ordered, load-validated node config list* — `NodeConfig.operations: list[OperationSpec] =
   Field(default_factory=list)` on the **base** model, inherited by all three modeled node types;
   unknown kind fails at the Pydantic boundary (`test_operation_spec_rejects_unknown_kind`,
   `test_loading_node_yaml_with_unknown_operation_kind_raises`); order preserved and applied in
   declared order (`test_node_config_operations_preserve_declared_order`,
   `test_build_and_apply_operations_preserve_declared_order`). ✅
5. *Lossless round-trip, no regression* — `_dump_graph_payload` pops an empty `operations` from a
   node's dumped config using the exact scoped-omission pattern already applied to
   `join`/`request`/`writer`/`trigger`/`cursor`/`visual`, so a pre-DANDER-69 graph still dumps
   byte-compatibly (asserted structurally *and* via raw-text `"operations" not in path.read_text()`
   for both YAML and JSON); a node **with** operations reloads model-equal and order-preserved.
   `resolve_node_config` correctly needed no change. Persistence in `graph_service.py` goes through
   `dump_graph_to_json`/`dump_graph_to_yaml`, so the on-disk format is unaffected; its HTTP body
   uses a raw `model_dump` and will now include `operations: []` alongside the `join: null` /
   `request: null` entries it already emits — consistent with the established behavior of that
   endpoint, not a regression. ✅
6. *No secret in operation config* — `params`/`metadata` are documented as field names and
   non-secret literals only; `hide_input_in_errors=True` on `OperationSpec` prevents a rejected
   `params` block from being echoed into a `ValidationError`; `UnregisteredOperationError` names
   only the kind. Grepped the full diff for credential-shaped literals — only prose about *not*
   holding secrets. No new `.env.example` key needed. ✅
7. *Validation stays in `GenericTestMetadata`* — stated explicitly in the module docstring, in
   `NodeConfig.operations`' docstring, and in the `FILTER_ROWS` enum-member doc (exclusion vs. a
   failing assertion). Nothing here fails a run over row content. ✅
8. *Unit tests* — 14 tests, no network, in-memory + `tmp_path` only: ABC not instantiable, registry
   dispatch, declared-order preservation, unknown kind rejected at the Pydantic boundary, plus
   `UnregisteredOperationError` for a valid-but-unregistered kind. Registry mutation is isolated
   via a fixture that pops the test kind in a `finally`; a second kind (`DEDUPLICATE`) is
   deliberately left unregistered module-wide. ✅
9. *No steering violations* — module/class/method Google-style docstrings throughout; fully typed;
   Pydantic v2 for config vs. plain ABC for behavior; imports stdlib + Pydantic only (acyclic:
   `operations.py` ← `node_config.py` ← `graph.py`); `_OPERATION_REGISTRY` ships empty with no
   module-level side effect on import; `raise ... from None` on the `KeyError` in `operation_for`
   (no swallowed error, typed replacement). ✅

**Tooling (run independently, not taken on trust)**
- `ruff check src tests` — clean.
- `ruff format --check` — only `src/dander/security/secret_manager.py` would reformat; that file is
  untouched by this ticket (pre-existing).
- `mypy` (strict, `src/`) — 74 files, no issues.
- `pytest tests/pipeline` — all pass (incl. the 14 new tests).
- Full `pytest` — 6 failures, all in `tests/cli/` (Rich/ANSI escape codes in captured stdout).
  Confirmed pre-existing and unrelated by stashing `src/dander/pipeline` + `tests/pipeline` and
  re-running `tests/cli`: the identical 6 fail without this change.

**Non-blocking observation (for a future ticket, not a fix required here)**
- `register_operation` silently overwrites an existing `_OPERATION_REGISTRY` entry if two classes
  claim the same `OperationKind` — last import wins. This matches the approved Design verbatim and
  cannot trigger today (registry ships empty, one op per kind planned), but once DANDER-70..72 land
  a duplicate-registration guard that raises would be more aligned with the "fail loud" rule in
  `steering/02-engineering.md`.
- `src/dander/pipeline/README.md` is not updated; per the ticket's own Files-to-Touch note this is
  an explicit documentation-agent follow-up and not a PASS requirement.
