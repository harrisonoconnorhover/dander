---
id: DANDER-71
title: Add field-level pipeline operations (truncate/trim/default/rename/drop)
status: done
component: python
epic: pipeline-operations
depends_on: [DANDER-69]
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Satisfied on the current trunk (`teammate/main`, adopted as local `main`), with narrowed scope:
`truncate_string`/`trim_whitespace`/`default_value` shipped in `src/dander/pipeline/operations.py`.
`rename`/`drop` were deliberately **not** ported — `docs/decisions.md`, "2026-08-05 — Pipeline
operations execute after raw ingestion" states "Rename/drop remain edge mappings," i.e. that
functionality already exists via the graph's existing field-mapping/edge config and doesn't need a
pipeline-operation form. No further action needed.

## Context

The most common declarative record cleanups act on a single field: bounding an over-long string to
a target length, trimming surrounding whitespace, coalescing a null to a default, renaming a field,
or dropping a field entirely. Per the "config-driven over code-driven" rule in
`steering/02-engineering.md`, these should be YAML-declared node operations, not bespoke connector
code.

This ticket implements a Core bundle of field-level `PipelineOperation`s on top of the DANDER-69
framework. Deferred Common/Advanced field operations (`change_case`, `regex_replace`, `cast_type`,
`parse_date`/`format_date`, `round_numeric`, `concat_fields`, `split_field`, `mask_field`,
`hash_field`, `json_extract`, `pad_string`) are backlog and out of scope here.

## Acceptance Criteria

- [ ] Concrete `PipelineOperation`s exist for each of: `truncate_string`, `trim_whitespace`,
      `default_value` (null coalesce), `rename_field`, `drop_field`, each registered against an
      `OperationKind` member in the DANDER-69 registry.
- [ ] `truncate_string` bounds a named field's string value to a configured max length; a non-string
      / null value passes through unchanged.
- [ ] `trim_whitespace` strips leading/trailing whitespace from a named field's string value; a
      non-string / null value passes through unchanged.
- [ ] `default_value` replaces a null (or missing) named field with a configured non-secret literal
      default, leaving non-null values untouched.
- [ ] `rename_field` moves a value from one field name to another; `drop_field` removes a named
      field. Both are stable when the source field is absent (documented, deterministic behavior).
- [ ] Each operation is configured entirely by field name(s) and non-secret literals — never a
      credential value (`steering/01-security.md`).
- [ ] Operations compose in declared order through the DANDER-69 pipeline (e.g. trim then default)
      and do not mutate records shared with upstream nodes unexpectedly.
- [ ] Unit tests cover each operation's happy path plus its documented edge cases (absent field,
      null value, non-string value) with in-memory records (no network, per
      `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Assumed DANDER-69 contract (dependency)

DANDER-69 is not yet built; this design conforms to the contract that ticket specifies and does
**not** redesign it. The concrete operations here depend on DANDER-69 providing, in
`src/dander/pipeline/operations.py`:

1. `OperationKind(StrEnum)` — the discriminator enum (matching the `NodeType`/`WriteMode`/
   `TransformationKind`/`GenericTestKind` `StrEnum` convention). DANDER-69 defines the empty/seed
   enum; **this ticket adds the five members** (adding an enum member is explicitly the per-op
   extension point in DANDER-69's AC, not a change to dispatch logic).
2. `PipelineOperation(ABC)` — abstract `apply(self, records: Iterable[Mapping[str, Any]]) ->
   Iterator[Mapping[str, Any]]`, record shape identical to `Source.extract()`
   (`Mapping[str, Any]`).
3. A registry seam mapping each `OperationKind` to its concrete `PipelineOperation` class in one
   place, extended by "one enum member + one class + one registry entry" without editing dispatch.
4. Node-config wiring in `src/dander/pipeline/node_config.py` carrying an **ordered** list of
   declarative operations, each a `{kind: OperationKind, ...params}` entry validated at the
   Pydantic load boundary and constructed via the registry, applied in declared order.

The **one seam that depends on 69's exact shape** is registration/construction (decorator vs.
explicit dict entry; whether each op is itself a Pydantic model constructed from its params, or a
class fed a params object). This design assumes the codebase-idiomatic shape — **each concrete
operation is a frozen Pydantic model that both validates/holds its typed params and implements
`apply()`** — mirroring how `FieldTest`/`Transformation`/`Trigger` are single models carrying a
`kind` plus kind-specific fields with an `@model_validator`. If 69 lands a different construction
seam, only the base wiring adapts; the per-operation semantics below are unchanged. **Flag for the
Code agent:** confirm 69's registration mechanism before wiring; if 69 is still unbuilt, land its
framework first (it is the `depends_on`).

### Approach

The five operations are all pure, per-record, single-field stream transforms with no I/O, no
network, and no state — the ideal config-driven-over-code-driven case (`steering/02-engineering.md`).
They share one streaming/copy discipline and differ only in what they do to one record, so the
design factors the common part into an intermediate abstract base and keeps each concrete op tiny.

Non-mutation (`steering/02-engineering.md` idempotency + the AC's "do not mutate records shared
with upstream nodes") is enforced structurally in **one** place: the base's `apply()` shallow-copies
each incoming record (`dict(record)`) before any concrete op touches it, and yields the copy. Upstream
nodes therefore never observe a mutation regardless of what a concrete op does, and the copy is
made uniformly (even on a pass-through) so the invariant can't be forgotten per-op. Copies are
shallow — sufficient because every operation only rebinds/removes top-level keys, never mutates a
nested container in place.

Ordered composition falls out for free: DANDER-69 applies the node's operation list in declared
order, and because each op returns an independent copied stream, `trim_whitespace` then
`default_value` compose exactly as declared (trimmed-to-empty is still non-null, so a following
`default_value` leaves it untouched — a documented, tested interaction).

### Interfaces / classes

New module `src/dander/pipeline/field_operations.py`:

- **`FieldOperation(PipelineOperation, ABC)`** — intermediate base for all single-record field ops
  (this ticket owns it; it is a legitimate DRY/SRP seam under 69's ABC, not a reshaping of 69).
  - Concrete `apply(self, records)` generator: `for record in records: yield self._apply_one(dict(record))`.
    Centralizes streaming + the mandatory shallow copy so no concrete op can skip it.
  - Abstract `_apply_one(self, record: dict[str, Any]) -> Mapping[str, Any]` — mutate the already-copied
    `record` and return it. Concrete ops implement only this.
  - Frozen Pydantic model config (`frozen=True`, `populate_by_name=True`, `extra="forbid"`,
    `hide_input_in_errors=True`). `extra="forbid"` catches typo'd params at load; `hide_input_in_errors`
    prevents `ValidationError` from echoing any authored literal (same rationale as `WriterConfig`/
    `Node`/`RequestSpec`).

Five concrete `FieldOperation` subclasses, each registered against its `OperationKind` member:

| Class | `OperationKind` | Params | `_apply_one` semantics |
|---|---|---|---|
| `TruncateStringOperation` | `TRUNCATE_STRING = "truncate_string"` | `field: str (min_length=1)`, `max_length: int (ge=0)` | If `record[field]` is a `str` longer than `max_length`, replace with `value[:max_length]`. `None`, non-`str`, or absent field → pass through unchanged (no key added). |
| `TrimWhitespaceOperation` | `TRIM_WHITESPACE = "trim_whitespace"` | `field: str (min_length=1)` | If `record[field]` is a `str`, replace with `value.strip()`. `None`, non-`str`, or absent → pass through. |
| `DefaultValueOperation` | `DEFAULT_VALUE = "default_value"` | `field: str (min_length=1)`, `default: Any = None` | If `field` is absent **or** `record[field] is None`, set `record[field] = default`. Otherwise untouched. `default` typed `Any` (like `Transformation.constant`) so a literal may be str/int/float/bool. |
| `RenameFieldOperation` | `RENAME_FIELD = "rename_field"` | `from_field: str` (alias `from`, min_length=1), `to_field: str` (alias `to`, min_length=1) | If `from_field` in record: `record[to_field] = record.pop(from_field)` (overwrites `to_field` if it already exists — documented rename semantics). If `from_field` absent → no-op (deterministic). |
| `DropFieldOperation` | `DROP_FIELD = "drop_field"` | `field: str (min_length=1)` | `record.pop(field, None)` — remove if present; absent → no-op. |

Param validators (`@model_validator(mode="after")`, following the `FieldTest`/`Transformation`
pattern; messages name only field-name params and constraints, never a data value):
- `DefaultValueOperation`: reject when `default` was **not authored** (`"default" not in
  self.model_fields_set` — the `Transformation.constant` `model_fields_set` dance, required because
  `default` legitimately spans falsy literals `0`/`""`/`False`, so a value check can't tell
  "omitted" from "set to a falsy literal") **and** reject an explicit `default: null` (a null
  default cannot coalesce a null — it would be a silent no-op). Net: `default` must be present and
  non-`None`.
- `RenameFieldOperation`: reject `from_field == to_field` (a no-op rename is a config error, mirrors
  `TransformJoinConfig`'s distinct-input check).

### Registration / node-config

- `OperationKind` gains the five members (in DANDER-69's `operations.py`).
- Registry gains five entries mapping each member to its class. Preferred seam: DANDER-69 exposes a
  `@register_operation(OperationKind.X)` decorator that `field_operations.py` applies, so the
  concretes self-register on import and `operations.py` never imports the concretes (no cycle); the
  `pipeline` package `__init__` (or `node_config`) imports `field_operations` once so registration
  runs before any node config is validated. Fallback if 69 ships an explicit dict: add the five
  entries at the bottom of `operations.py`, importing the classes from `field_operations.py`
  (import direction base→concretes stays acyclic: `field_operations` imports the ABC/enum from
  `operations`; `operations` imports the classes only at module bottom, after the ABC is defined).
- No new fields on `node_config.py` beyond 69's operation-list wiring — these ops ride that list.

### Files to touch / create

- **create** `src/dander/pipeline/field_operations.py` — `FieldOperation` base + five concrete ops.
- **edit** `src/dander/pipeline/operations.py` (DANDER-69) — add five `OperationKind` members and
  the five registry entries/decorator wiring; do not touch dispatch logic.
- **edit** `src/dander/pipeline/__init__.py` (or `node_config.py`) — ensure `field_operations` is
  imported so decorator-based registration executes at load time (only if 69 uses the decorator seam).
- **edit** `src/dander/pipeline/README.md` — document the Core field operations and their edge-case
  contracts (per `steering/languages/python.md` package-README rule).
- **create** `tests/pipeline/test_field_operations.py` — unit tests (below).

### Test seams

Pure in-memory records (`list[dict]`), no network, no mocks needed (`steering/02-engineering.md`).
Cover per operation:
- **happy path** — truncate over-length string; trim padded string; default fills a null; rename
  moves a value; drop removes a key.
- **documented edge cases** — absent field (all five: pass-through / no-op); `None` value
  (truncate/trim pass through, default fills); non-`str` value (truncate/trim pass through, e.g. an
  `int`); truncate where `len == max_length` and `< max_length` (no change) and `max_length == 0`
  (empty string); rename onto an existing target (overwrite); rename `from == to` and `default`
  omitted / `default: null` → `ValidationError` at load.
- **non-mutation** — pass a record, assert the original input dict is unchanged after `apply()`
  (identity/content check on the source list).
- **composition/order** — `[trim_whitespace, default_value]` on a whitespace-only field yields an
  empty (non-null) string, not the default; reversed order differs — proves declared order is honored.
- **base is abstract** — `FieldOperation` (and `PipelineOperation`) cannot be instantiated
  (`TypeError`), consistent with DANDER-69's ABC test.

### Trade-offs

- **Frozen Pydantic model as the operation** (vs. a plain class fed a separate params object):
  matches the `FieldTest`/`Transformation`/`Trigger` house pattern, gets load-time validation +
  `extra="forbid"` typo-catching for free, and makes the op config-is-the-object. Cost: assumes
  69's construction seam builds the op from its params — flagged above as the one dependency point.
- **Shallow copy in one shared base** (vs. each op copying, or deep-copying): centralizes the
  non-mutation invariant so it can't be missed; shallow is sufficient because no op mutates nested
  values in place. Deep copy would be wasteful for a top-level-key-only transform.
- **Silent no-op on absent source for rename/drop** (vs. raising): field ops run over heterogeneous
  streams where a field may legitimately be missing on some records; failing loud would make a
  benign, order-independent cleanup brittle. Chosen because the AC requires "stable when the source
  field is absent (documented, deterministic)" — the behavior is documented in each class docstring
  and asserted in tests, so it is deterministic, not accidental swallowing.
- **`default` rejects an explicit `null`** (vs. allowing it): a null default is a functional no-op
  that reads as a mistake; rejecting it at the boundary is fail-loud config hygiene. The `min_length`
  guards likewise reject empty field names at load rather than yielding a confusing runtime KeyError.

### Not in scope / boundaries

- Generic **row validation** (`not_null`/`unique`/`accepted_values`/`relationships`) stays in
  `GenericTestMetadata` (`transform/config.py`) and `FieldTest` (`pipeline/graph.py`) — these are
  data-quality assertions, a separate concern from stream *transformation* (per DANDER-69's note).
  No validation is reimplemented as an operation.
- Common/Advanced field ops (`change_case`, `regex_replace`, `cast_type`, `parse_date`,
  `mask_field`, `hash_field`, etc.) are backlog — not added here.
- No secret/credential value is ever held: every op is configured by field name(s) and non-secret
  literals only (`steering/01-security.md`); `hide_input_in_errors` keeps literals out of error text.

## Implementation Notes

Built exactly per the Design's assumed construction seam — DANDER-69 had already merged (status
`done`) with the exact shape the Design flagged as its one dependency point: `register_operation`
decorator, `PipelineOperation.apply(records)`/`from_spec(spec)`, and — notably — the Core
`OperationKind` vocabulary (including all five of this ticket's kinds) already shipped by
DANDER-69's own "ship the vocabulary up front" decision. Confirmed this against the live
`src/dander/pipeline/operations.py` before writing any code, as the Design instructed.

**Files created**
- `src/dander/pipeline/field_operations.py` — `FieldOperation(PipelineOperation, BaseModel, ABC)`
  base (concrete `apply()` doing the mandatory `dict(record)` shallow copy + streaming; abstract
  `_apply_one`; a shared `from_spec` implemented once as `cls.model_validate(spec.params)`, since
  each concrete op's own Pydantic fields *are* its params — no separate params model needed) and
  the five concrete ops: `TruncateStringOperation`, `TrimWhitespaceOperation`,
  `DefaultValueOperation`, `RenameFieldOperation`, `DropFieldOperation`, each decorated with
  `@register_operation(OperationKind.X)` exactly as designed.
- `tests/pipeline/test_field_operations.py` — registration/dispatch, ABC-not-instantiable (both
  `FieldOperation` and `PipelineOperation`), each op's happy path + every documented edge case
  (absent field, `None`, non-`str`, `max_length` boundary/zero, rename-onto-existing-target,
  `default` omitted/explicit-`null` raising `ValidationError`, `from == to` raising, `extra`
  param raising), non-mutation of both a single record and the source list, and declared-order
  composition through `build_operations`/`apply_operations` (including a
  default-first-vs-trim-first pair on a *missing* field that proves order is actually honored,
  not just coincidentally identical either way).

**Files edited**
- `src/dander/pipeline/__init__.py` — added an import of `dander.pipeline.field_operations`
  (comment explains the intentional plugin-registration side effect, citing DANDER-69's own
  trade-off note) so registration runs at package-import time, and re-exported the base +
  five concrete classes in `__all__`.
- `src/dander/pipeline/README.md` — new `dander.pipeline.field_operations` row in the module
  table, a "Field-level operations" section (params table, YAML example, the
  trim-then-default composition note), and a `DANDER-69`/`DANDER-71` line in Related tickets.
- `tests/pipeline/test_operations.py` — **one deviation, not part of the Design's own file list,
  made necessary by the state DANDER-69 actually shipped in**: its `tag_adder_cls` fixture
  registered its test-local op against `_TEST_KIND = OperationKind.TRIM_WHITESPACE` and
  unconditionally `pop`s that registry entry in `finally`. Once this ticket registers a *real*
  `TrimWhitespaceOperation` against that same kind, running any test using that fixture would
  permanently delete the real registration from the module-level `_OPERATION_REGISTRY` for the
  rest of the pytest process (proven locally: reversed test-file run order changed behavior
  before this fix). Changed `_TEST_KIND` to `OperationKind.FILTER_ROWS` (a kind reserved for
  DANDER-72, still genuinely unregistered) and updated its comment; `_UNREGISTERED_KIND` stays
  `DEDUPLICATE`. No other line of that file changed. Verified both
  `pytest tests/pipeline/test_operations.py tests/pipeline/test_field_operations.py` and the
  reversed order pass identically after the fix.

**Deviations from Design**
- **No edit to `operations.py`'s enum/registry** — the Design's own text already anticipated this
  ("DANDER-70..72 add class + registry entry only" since the Core vocabulary ships up front); by
  the time this ticket ran, DANDER-69 had shipped exactly that, so there was literally nothing to
  add to `OperationKind` — all five members (`TRUNCATE_STRING`/`TRIM_WHITESPACE`/`DEFAULT_VALUE`/
  `RENAME_FIELD`/`DROP_FIELD`) already existed. `operations.py` is untouched by this ticket.
  `register_operation` is the only registry mechanism DANDER-69 shipped, so the "decorator vs.
  explicit dict" fork the Design flagged resolved itself.
- **`FieldOperation` base list is `(PipelineOperation, BaseModel, ABC)`**, not the Design's literal
  `(PipelineOperation, ABC)` — `BaseModel` is required in the bases for `model_config`/fields to
  exist at all; verified empirically that Pydantic v2's `ModelMetaclass` (an `ABCMeta` subclass)
  makes this combination behave correctly as an abstract class (`FieldOperation()` raises
  `TypeError`) before committing to it.
- Everything else (params, validators, edge-case semantics, non-mutation discipline, registration
  seam) matches the Design as written.

**Tooling** (via `uv run`, from repo root)
- `ruff check src tests` — clean.
- `ruff format --check src tests` — only `src/dander/security/secret_manager.py` would reformat;
  pre-existing, untouched by this ticket (matches DANDER-69's own note).
- `mypy src` (strict) — 76 files, no issues.
- `pytest tests/pipeline` — all pass (including the new `test_field_operations.py`).
- `pytest` (full suite) — 6 failures, all pre-existing in `tests/cli/*` (Rich/ANSI escape codes in
  captured stdout, the identical failures DANDER-69's review already confirmed pre-existing and
  unrelated). No other regressions.

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — PASS

Reviewed `src/dander/pipeline/field_operations.py`, `tests/pipeline/test_field_operations.py`,
`src/dander/pipeline/__init__.py`, `src/dander/pipeline/README.md`, and the one-line
`_TEST_KIND` change in `tests/pipeline/test_operations.py`, against the Acceptance Criteria,
`steering/01-security.md`, `steering/02-engineering.md`, and `steering/languages/python.md`.

**Acceptance criteria — all met, verified against the code and by execution.**

1. *Five concrete ops registered against an `OperationKind`* — `TruncateStringOperation`,
   `TrimWhitespaceOperation`, `DefaultValueOperation`, `RenameFieldOperation`,
   `DropFieldOperation`, each decorated `@register_operation(OperationKind.X)`. Confirmed
   `operation_for(...)` resolves all five (`test_operations_are_registered_against_their_kind`).
   The Implementation Notes' claim that DANDER-69 already shipped all five enum members is
   accurate — verified in `operations.py:76-80`; `operations.py` is genuinely untouched by this
   ticket, so the Design's flagged "one dependency point" resolved as documented.
2. *`truncate_string` bounds a string; non-string/null passes through* — `isinstance(value, str)`
   guard plus `len(value) > max_length`; absent field uses `.get()` so no key is added. Boundary
   (`len == max_length`), `max_length == 0`, `None`, `int`, and absent-field cases all tested.
3. *`trim_whitespace` strips; non-string/null passes through* — same guard shape; all-whitespace
   → `""` is asserted, which is the load-bearing case for AC 7's composition test.
4. *`default_value` fills null/missing only* — `if self.field not in record or record[self.field]
   is None`. The `model_fields_set` check (not truthiness) correctly admits `0`/`False`/`""` as
   real defaults while rejecting an omitted or explicit-`null` `default`; all four asserted.
5. *`rename_field` / `drop_field` stable on absent source* — `if self.from_field in record` and
   `record.pop(field, None)`; both no-ops are documented in the class docstrings, in
   `README.md`'s params table, and asserted. Rename-onto-existing-target overwrite is documented
   and tested.
6. *Configured only by field names + non-secret literals* — no credential-shaped literal anywhere
   in the diff (grepped). Nothing new in `.env.example` is required and none was added. Verified
   empirically that `hide_input_in_errors=True` actually suppresses the rejected value: a
   sentinel passed as `max_length` does **not** appear in the raised `ValidationError` string.
7. *Compose in declared order; no upstream mutation* — the shallow copy lives in exactly one place
   (`FieldOperation.apply`, unconditional, even on pass-through), so no concrete op can skip it.
   Both non-mutation tests (single record and source list) pass. The order test is a good one: it
   uses a *missing* field with a padded default so default-first and trim-first genuinely differ
   (`{"label": "x"}` vs `{"label": "  x  "}`), rather than being coincidentally order-independent.
8. *Unit tests, in-memory, no network* — 37 tests, `list[dict]` fixtures only, no mocks, no I/O.
9. *No steering violations* — see below.

**Security (`01-security.md`):** clean. No secrets, keys, tokens, or PII in source, docstrings,
tests, or sample data. Error messages name only field names and constraints. `extra="forbid"` +
`hide_input_in_errors=True` on the shared base means a typo'd or rejected param cannot echo an
authored literal into an exception. No new secret keys, no IAM/identity surface touched.

**Design fidelity:** matches the Design. Both declared deviations are legitimate and correctly
justified: (a) `operations.py` needed no edit because DANDER-69 shipped the Core vocabulary up
front — verified; (b) `(PipelineOperation, BaseModel, ABC)` rather than the Design's literal
`(PipelineOperation, ABC)` is *required* for Pydantic fields to exist. Independently confirmed the
abstractness is real and not incidental: `FieldOperation()` raises
`TypeError: Can't instantiate abstract class FieldOperation without an implementation for
abstract method '_apply_one'`, and `kind` correctly stays a `ClassVar`, not a model field
(`TruncateStringOperation.model_fields == {'field', 'max_length'}`).

**The `tests/pipeline/test_operations.py` deviation is correct and necessary, not scope creep.**
Independently reproduced the reasoning: that fixture's `finally` unconditionally pops `_TEST_KIND`
from the module-level `_OPERATION_REGISTRY`, so leaving it on `TRIM_WHITESPACE` would have
permanently deleted this ticket's real registration for the remainder of any pytest process that
ran the fixture first. Verified both file orders pass identically after the fix
(`test_operations.py test_field_operations.py` and the reverse). One line changed plus its
comment; nothing else in that file.

**Language conventions (`languages/python.md`):** `ruff check src tests` clean; `mypy src` strict
clean (76 files); `ruff format --check` flags only the pre-existing
`src/dander/security/secret_manager.py`, untouched by this ticket. Full docstrings on the module,
every class, every public method, and both validators. `Any` on `default` carries an explicit
justification, as required. Import-time side effect in `__init__.py` is the intended plugin
registration and is called out in a comment.

**Tests:** `pytest tests/pipeline` — all pass. Full `pytest` — 6 failures, all in `tests/cli/*`
and all Rich/ANSI-escape assertions on captured stdout; `tests/cli` is untouched by this ticket
and DANDER-69's review already recorded these as pre-existing. No regressions.

**Non-blocking advisories** (do not block this ticket; fold into a future ticket if useful):

1. **Mutable `default` aliasing.** `DefaultValueOperation`'s docstring
   (`field_operations.py:172`) and `README.md` widen the accepted `default` beyond the Design's
   `str`/`int`/`float`/`bool` to include `list`/`dict`. Because the base copy is (correctly)
   shallow, a `default=[]` injects the *same* list object into every record — verified:
   `result[0]["tags"] is result[1]["tags"]` is `True`. Harmless for the five ops here (none mutate
   nested containers in place), but a downstream in-place append would corrupt every record. Worth
   either narrowing the documented type to scalars or noting the caveat explicitly.
2. **`test_operations.py`'s fixture will collide again at DANDER-72.** This ticket moved
   `_TEST_KIND`/`_UNREGISTERED_KIND` onto `FILTER_ROWS`/`DEDUPLICATE`, which are exactly the kinds
   DANDER-72 implements — the same landmine, one ticket downrange. The durable fix is for the
   fixture to save and restore any pre-existing registration in `finally` instead of
   unconditionally popping. Out of scope here; DANDER-72 should do it rather than relocate the
   sentinel a third time.
3. **Test docstring density.** 9 of 37 test functions carry docstrings, against 14/14 in
   `test_graph.py` and 31/31 in `test_transformations.py`. Ruff's `select` omits `D`, the names
   are self-describing, and DANDER-69's sibling file set the looser precedent — noted for
   consistency only.
