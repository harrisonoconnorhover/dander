---
id: DANDER-72
title: Add row-level pipeline operations (filter_rows, deduplicate)
status: open
component: python
epic: pipeline-operations
depends_on: [DANDER-69]
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Partially satisfied on the current trunk (`teammate/main`, adopted as local `main`) — narrow the
remaining scope of this ticket to **`deduplicate` only**: `filter_rows` already shipped in
`src/dander/pipeline/operations.py` (`FilterRowsParams`/`FieldCondition`/`ComparisonOperator`/
`MatchLogic`). `deduplicate` is a real, still-open gap — its module docstring lists "deduplication"
as "intentionally absent," and `docs/decisions.md`, "2026-08-05 — Pipeline operations execute after
raw ingestion" confirms it "require[s] separate product decisions rather than entering through this
slice." Treat that as a prerequisite design gate before implementing against the Design below,
which predates the current `OperationKind`/`OperationSpec`/CTE-compiler architecture and covers
`filter_rows` (done) alongside `deduplicate` (still needed) — split or rewrite the Design to target
only `deduplicate`.

## Context

Beyond field cleanups (DANDER-71), pipelines commonly need row-level shaping: excluding records
that fail a predicate, and collapsing duplicates to a single latest-wins row per business key. Under
the SCD1 writer (`WriteMode.SCD1` in `src/dander/writer/base.py`), which MERGEs on the business key,
feeding duplicate keys in one batch is ambiguous, so a declarative `deduplicate` operation is a
natural pre-write step. Per "config-driven over code-driven" in `steering/02-engineering.md`, these
are YAML-declared node operations, not connector code.

This ticket implements a Core bundle of row-level `PipelineOperation`s on top of the DANDER-69
framework. It must **not** reimplement generic row *validation* (`not_null`/`unique`/
`accepted_values`/`relationships`) — that already lives as `GenericTestMetadata` in
`src/dander/transform/config.py`; `filter_rows` is predicate-based exclusion, a transformation, not
a validation assertion.

## Acceptance Criteria

- [ ] Concrete `PipelineOperation`s exist for `filter_rows` and `deduplicate`, each registered
      against an `OperationKind` member in the DANDER-69 registry.
- [ ] `filter_rows` excludes records that do not satisfy a declarative, non-code predicate expressed
      over field names and non-secret literals (e.g. field-comparison conditions); the predicate
      grammar is bounded and documented, not arbitrary executable code.
- [ ] `deduplicate` collapses records sharing a configured business key to one row, keeping the
      latest per a configured ordering field (latest-wins); ties and missing ordering values have
      documented deterministic behavior.
- [ ] `deduplicate`'s business-key semantics are consistent with `WriteTarget.business_key` /
      `DestinationSpec.business_key` so it composes cleanly ahead of an SCD1 write.
- [ ] Neither operation reimplements `GenericTestMetadata` validation; the design states the
      distinction between predicate exclusion and a validation assertion.
- [ ] Operations are configured by field names and non-secret literals only
      (`steering/01-security.md`) and compose in declared order through the DANDER-69 pipeline.
- [ ] Unit tests cover: predicate include/exclude cases, dedup latest-wins with an ordering field,
      tie/missing-key behavior, and composition with a field-level op, using in-memory records (no
      network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Dependency assumption (DANDER-69)

DANDER-69 is not yet designed, so this design targets the seam its **ticket** specifies and states
the contract it relies on. If 69's final shape differs, only the registration/config-union wiring
below changes — the operation logic does not. This design assumes DANDER-69 (`src/dander/pipeline/
operations.py`) exposes:

- `OperationKind(StrEnum)` — the closed set of operation kinds (this ticket **adds two members**:
  `FILTER_ROWS = "filter_rows"`, `DEDUPLICATE = "deduplicate"`).
- `PipelineOperation(ABC)` — with `apply(self, records: Iterable[Mapping[str, Any]]) ->
  Iterable[Mapping[str, Any]]`, record shape matching `Source.extract()`. Each concrete operation is
  constructed from its validated config model and does not mutate the input mappings in place.
- A **central registry in one place** (a `dict[OperationKind, ...]`, or a `register()`/decorator
  seam) mapping each kind to `(config model, operation class)`, plus an **ordered, discriminated
  operation-config union** wired into `node_config.py` and validated at load time. This ticket adds
  exactly: two enum members, two config models joined to that union, two operation classes, and two
  registry entries — **no** edits to 69's dispatch logic (per 69's AC).

### Approach

Both operations are pure, connector-agnostic stream transformations declared in node YAML. Config is
**data only** — field names and non-secret scalar literals validated by Pydantic v2 models — never
executable code, never a secret reference; evaluation is a fixed interpreter over that data, never
`eval`/`exec` (`steering/01-security.md`). Neither operation mutates the mappings it receives, so
records shared with upstream nodes are untouched (both select/pass through existing record objects).

**Distinction from `GenericTestMetadata` (AC 5).** `filter_rows` is a *transformation*: records
failing the predicate are silently excluded from the downstream stream and the run continues.
`GenericTestMetadata` (`not_null`/`unique`/`accepted_values`/`relationships` in
`src/dander/transform/config.py`) is a *validation assertion*: it evaluates a condition and its
result is a pass/fail signal that can fail the run, and it does not remove rows. This ticket
reimplements neither — it introduces predicate-based row exclusion and key-based collapse, both of
which change the row set rather than assert over it. The design note in code and README states this.

**`filter_rows` — bounded predicate grammar.** A `filter_rows` op carries a `FilterPredicate`: a
flat, non-nested list of `FieldCondition`s combined by a single `logic` combinator (`ALL` = AND,
default; `ANY` = OR). A `FieldCondition` is `{field: str, op: ComparisonOperator, value:
Scalar | list[Scalar] | None}`. The grammar is deliberately bounded (flat list + one combinator, no
arbitrary nesting) — enough for field-comparison exclusion, no speculative generality. A record is
**kept** iff the predicate evaluates true; otherwise it is excluded. Streams lazily (a generator that
yields kept records) — O(1) memory.

`ComparisonOperator(StrEnum)`: `EQ`/`NE`/`GT`/`GTE`/`LT`/`LTE`/`IN`/`NOT_IN`/`IS_NULL`/`IS_NOT_NULL`.
Documented deterministic semantics:
- Missing field == field present with value `None` (uses `record.get(field)`).
- `IS_NULL`/`IS_NOT_NULL` take no `value` (validated: `value` must be omitted for these, required
  for the others; `IN`/`NOT_IN` require a non-empty `list`, the rest a single `Scalar`).
- A `None` (missing/null) operand under any operator other than `IS_NULL` evaluates **false** (so the
  record is excluded under `ALL`) — e.g. `age GT 18` excludes a row whose `age` is missing.
- Ordering comparisons (`GT`/`GTE`/`LT`/`LTE`) that raise `TypeError` on incomparable operand types
  (e.g. `str` vs `int`) are caught and evaluate **false** — deterministic, never crashes the run.

**`deduplicate` — latest-wins collapse (AC 3, 4).** Config `DeduplicateConfig`:
`{business_key: list[str] (non-empty), order_by: str, keep: KeepStrategy = LATEST}` where
`KeepStrategy(StrEnum)` is `LATEST`/`EARLIEST`. It groups records by the tuple of `business_key`
values — the **same key semantics** as `WriteTarget.business_key` / `DestinationSpec.business_key`
and the writer's existing `_deduplicate_keyed` (`src/dander/writer/bigquery.py`), so it composes
cleanly immediately ahead of an `WriteMode.SCD1` MERGE — and keeps one row per group by `order_by`.
Documented deterministic behavior:
- **Latest-wins:** within a group, keep the record with the max (`LATEST`) / min (`EARLIEST`)
  `order_by` value.
- **Ties** (equal `order_by`): keep the **last occurrence in input order** (stable last-wins,
  matching the writer's last-record-wins convention).
- **Missing/`None` `order_by`:** sorts as the oldest possible value (loses to any present value under
  `LATEST`, wins under `EARLIEST`); comparison never raises because ordering is done with a
  `(present_flag, value)` sort key, so `None` is never compared against a real value. If every record
  in a group lacks `order_by`, last-in-stream wins by the tie rule.
- **Null/absent business-key component:** grouped by the key tuple as-is (a `None` component forms
  its own group); dedup does **not** reject null keys — that stays the SCD1 writer's responsibility
  (single responsibility; a later `default_value` op could also backfill it). Documented.
- **Output order:** one row per key, emitted in the order each key **first appeared** in the input
  (stable). Unlike `filter_rows`, `deduplicate` is **materializing** — it must buffer the batch to
  see all rows per key before emitting; noted as an O(n) memory trade-off, acceptable for the
  batch-oriented pipeline.

### Interfaces / classes (this ticket)

In a new concrete module `src/dander/pipeline/row_operations.py` (concretes live beside, not inside,
69's framework module — one concept per module, and keeps `operations.py` from bloating as
70/71/72 land; it imports only the ABC + enum from `operations.py`, so no import cycle):

- `ComparisonOperator(StrEnum)` — the closed operator set above.
- `MatchLogic(StrEnum)` — `ALL` / `ANY`.
- `FieldCondition(BaseModel, extra="forbid")` — `field`, `op`, `value`; a `model_validator`
  enforcing the value-arity rules per operator (none for `IS_*`, list for `IN`/`NOT_IN`, scalar
  otherwise). Reuses the module `Scalar` alias (`str | int | float | bool`) already used by
  `transform/config.py`.
- `FilterPredicate(BaseModel, extra="forbid")` — `conditions: list[FieldCondition]` (min_length 1),
  `logic: MatchLogic = ALL`; method `matches(record: Mapping[str, Any]) -> bool` (the pure
  interpreter).
- `FilterRowsConfig(BaseModel)` — the discriminated `kind: Literal[OperationKind.FILTER_ROWS]` +
  `predicate: FilterPredicate`.
- `FilterRowsOperation(PipelineOperation)` — constructed from `FilterRowsConfig`; `apply()` yields
  each record where `predicate.matches(record)`.
- `KeepStrategy(StrEnum)` — `LATEST` / `EARLIEST`.
- `DeduplicateConfig(BaseModel)` — `kind: Literal[OperationKind.DEDUPLICATE]`, `business_key:
  list[str]` (min_length 1), `order_by: str`, `keep: KeepStrategy = LATEST`.
- `DeduplicateOperation(PipelineOperation)` — constructed from `DeduplicateConfig`; `apply()` buffers
  and collapses per the semantics above.

`hide_input_in_errors=True` on both config models — same rationale as `WriterConfig`/`RequestSpec`:
keep rejected field values out of `ValidationError` strings (`steering/01-security.md`).

### Files to touch / create

- **`src/dander/pipeline/row_operations.py`** (new) — the two config models, two operation classes,
  the enums/`FieldCondition`/`FilterPredicate` above, Google-style docstrings documenting every
  edge-case rule stated here.
- **`src/dander/pipeline/operations.py`** (edit, owned by 69) — add `FILTER_ROWS`/`DEDUPLICATE` to
  `OperationKind`; add the two registry entries mapping each kind to its `(config model, operation
  class)`. No dispatch-logic edits.
- **`src/dander/pipeline/node_config.py`** (edit) — join `FilterRowsConfig`/`DeduplicateConfig` into
  69's discriminated operation-config union so they validate at the node-config boundary (only if 69
  builds that union to require per-kind registration here; otherwise no change).
- **`src/dander/pipeline/README.md`** (edit) — document the two operations, the bounded predicate
  grammar, and the `filter_rows`-vs-validation distinction.
- **`tests/pipeline/test_row_operations.py`** (new) — unit tests (below).
- **`.env.example`** — no change (no new secret keys; operations reference data by field name only).

### Test seams (AC 7)

Pure in-memory `list[dict]` records; no network, nothing mocked (these ops touch no I/O). Cover:
- `filter_rows` include/exclude across each `ComparisonOperator`; `ALL` vs `ANY`; missing-field and
  `None`-operand → excluded; `IS_NULL`/`IS_NOT_NULL`; incomparable-types (`str` vs `int`) → excluded,
  no raise; `IN`/`NOT_IN` membership.
- `deduplicate` latest-wins with an `order_by` field; `EARLIEST`; tie → last-in-order wins;
  missing/`None` `order_by`; multi-column `business_key`; null key component grouped (not rejected);
  first-appearance output order.
- Composition: a field-level op (e.g. DANDER-71 `default_value`/`trim_whitespace`, or a simple stand-
  in) then `deduplicate`/`filter_rows` applied in declared order through the pipeline, confirming
  order matters and upstream records are not mutated.
- Config-boundary: unknown operator / bad value-arity rejected at the Pydantic boundary; a secret-
  looking value is still just an opaque literal (there is no secret channel to exploit).

### Trade-offs

- **Flat predicate + single combinator** over nestable boolean trees: bounded and documented per AC 2,
  avoids speculative generality; `ANY`+`ALL` nesting can be a later ticket if a real need appears.
- **`TypeError` → false** for incomparable ordering comparisons rather than raising: keeps the
  pipeline deterministic and crash-free on dirty data; the alternative (fail the run) belongs to
  validation, not a filter transformation.
- **Dedup materializes** the batch: simplest correct latest-wins; acceptable for batch sizes bounded
  by `WriterConfig.max_batch_rows`. A streaming pre-sorted variant is unwarranted now.
- **Dedup does not reject null keys**: preserves single responsibility (writer already enforces this)
  and lets a downstream `default_value` op remediate; documented so it is not surprising.

### Flag (ambiguity)

AC 3 says ties/missing-ordering must be *documented deterministic* but does not dictate *which* way;
this design picks last-in-input-order for ties and oldest-sorts-`None`, and documents both. If a
different convention is preferred, it is a one-line change to the sort key.

## Implementation Notes

**Built.** `src/dander/pipeline/row_operations.py` (new): `ComparisonOperator` (`eq`/`ne`/`gt`/
`gte`/`lt`/`lte`/`in`/`not_in`/`is_null`/`is_not_null`), `MatchLogic` (`all`/`any`),
`FieldCondition`, `FilterPredicate` (the pure interpreter, `matches()`), `FilterRowsOperation`
(registered against `OperationKind.FILTER_ROWS`), `KeepStrategy` (`latest`/`earliest`),
`DeduplicateOperation` (registered against `OperationKind.DEDUPLICATE`), and a private `_Reversed`
comparison-wrapper helper used only inside `DeduplicateOperation._sort_key`. Both concrete ops
implement `PipelineOperation` directly (no intermediate ABC needed, since each has exactly one
class). `tests/pipeline/test_row_operations.py` (new, 50 tests) covers every acceptance criterion.
`src/dander/pipeline/__init__.py` re-exports the new public names and imports the module for its
registration side effect, exactly like `field_operations`. `src/dander/pipeline/README.md` gained a
*Row-level operations* section documenting the grammar and both operations' edge-case tables.

**Deviations from the ticket's assumed design** (both following the precedent DANDER-71 actually
established, which differs from what this ticket drafted before DANDER-69/71 existed):
- **No separate `FilterRowsConfig`/`DeduplicateConfig` model.** `FilterRowsOperation` and
  `DeduplicateOperation` are themselves frozen Pydantic models whose fields *are* their params
  (`predicate`; `business_key`/`order_by`/`keep`) — `from_spec` is `cls.model_validate(spec.params)`
  directly, mirroring every op in `field_operations.py`. A separate `*Config` class would just be
  the same fields duplicated onto an intermediate model with no behavior of its own.
- **No `node_config.py` edit.** `NodeConfig.operations` is `list[OperationSpec]` (an inert
  `kind` + free-form `params` dict), not a discriminated union of concrete per-kind config models
  — DANDER-69 shipped it that way, and DANDER-71 confirmed no union wiring is needed. Nothing in
  this ticket's scope required a `node_config.py` change.
- **No `operations.py` edit, no new `OperationKind` members.** DANDER-69 already enumerated
  `FILTER_ROWS`/`DEDUPLICATE` in the Core vocabulary up front (mirroring `ConnectorOperation`'s
  precedent) specifically so DANDER-72 wouldn't need to touch that module — confirmed by reading
  `operations.py` before writing code. `register_operation(OperationKind.X)` was all that was
  needed.

**A design bug caught and fixed while writing tests.** The ticket's design says a missing/`None`
`order_by` "sorts as the oldest possible value (loses to any present value under `LATEST`, wins
under `EARLIEST`)". My first `_sort_key` draft ranked "has a value" above "missing" unconditionally
regardless of `keep`, which is right for `LATEST` but backwards for `EARLIEST` (where the record
representing "-infinity" should win the minimum). Fixed by flipping the rank direction
(`has_value if pick_last else not has_value`) so a missing `order_by` beats a present one under
`EARLIEST`, matching the design text; `test_deduplicate_missing_order_by_wins_under_earliest`
exercises this directly and would have caught the original bug.

**A second known-in-advance ripple, fixed.** `tests/pipeline/test_operations.py` (DANDER-69) had
picked `OperationKind.FILTER_ROWS`/`DEDUPLICATE` as its "real member with no registered class"
sentinels — its own comments said this was reserved specifically for DANDER-72. Landing this ticket
necessarily invalidates that assumption (every `OperationKind` member now has a real registered
class), so two things there needed fixing to keep the full suite green: (1) the `tag_adder_cls`
fixture's `finally` now saves/restores whatever was previously registered against `_TEST_KIND`
instead of unconditionally popping it (the old code would have permanently deleted
`FilterRowsOperation`'s registration for the rest of the test session); (2) the two
"unregistered kind" tests now use `monkeypatch.delitem` to unregister `DEDUPLICATE` for their own
duration only, rather than relying on it staying unregistered forever. Verified: `uv run pytest
tests/pipeline -q` is green; `uv run pytest -q` is green except six pre-existing CLI-output
assertion failures in `tests/cli/` (Rich ANSI formatting vs. plain-text `in` assertions) that
reproduce identically on `git stash` (pre-change baseline) — confirmed unrelated to this ticket
before treating them as out of scope.

**Toolchain:** `ruff check`/`ruff format --check` clean on every file touched (repo-wide `ruff
format --check` flags one pre-existing, untouched file — `src/dander/security/secret_manager.py` —
absent from `git diff`/`git status`, not from this change). `mypy src/dander` (77 files) and `mypy`
on the new/edited test files: clean. `uv run pytest tests/pipeline -q`: 100% green (including the
new 50 tests and the DANDER-69 fixes above).

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — PASS

**Acceptance criteria — all met.**

1. **Concrete ops registered.** `FilterRowsOperation` / `DeduplicateOperation`
   (`src/dander/pipeline/row_operations.py`) are decorated `@register_operation(
   OperationKind.FILTER_ROWS)` / `(OperationKind.DEDUPLICATE)`. Both members already existed in
   DANDER-69's `OperationKind` (`operations.py:81-82`) — verified, so the Implementation Notes'
   "no `operations.py` edit" deviation is correct, not an omission. `dander/pipeline/__init__.py`
   imports the module for its registration side effect and re-exports the seven public names.
2. **Bounded, non-code predicate.** `FilterPredicate` = flat `list[FieldCondition]` (`min_length=1`)
   + one `MatchLogic` combinator; `ComparisonOperator` is a closed `StrEnum`; evaluation is a fixed
   interpreter (`FieldCondition.evaluate`) — no `eval`/`exec`, confirmed by reading the module. Value
   arity is enforced at the Pydantic boundary (`_check_value_arity`) and the grammar is documented in
   both the class docstrings and `src/dander/pipeline/README.md` ("Row-level operations").
3. **Latest-wins + documented determinism.** `DeduplicateOperation._sort_key` returns
   `(rank, ordered_value, index)`; ties resolve to the last input occurrence under **both**
   `KeepStrategy` values (index left ascending) and missing/`None` `order_by` sorts as "oldest"
   (`rank = has_value if pick_last else not has_value`). The `EARLIEST` direction flip called out in
   the Implementation Notes is correct and is directly exercised by
   `test_deduplicate_missing_order_by_wins_under_earliest`. All rules appear in the class docstring
   and the README edge-case table.
4. **Business-key consistency.** Grouping is `tuple(record.get(f) for f in business_key)` —
   positionally identical to `_deduplicate_keyed` in `src/dander/writer/bigquery.py:717-731`, which
   also keys on the same tuple and keeps the last row per key. `business_key: list[str]` mirrors
   `DestinationSpec.business_key` (`node_config.py:269`); `WriteTarget.business_key` is the
   `tuple[str, ...]` internal form (`writer/base.py:62`). Null-key rejection is correctly left to the
   writer (verified it raises there) and documented as such.
5. **No `GenericTestMetadata` reimplementation.** The transformation-vs-assertion distinction is
   stated in the module docstring and the README; nothing here evaluates a pass/fail assertion or can
   fail a run over a row's content.
6. **Config is field names + non-secret literals; composes in declared order.** `extra="forbid"` and
   `hide_input_in_errors=True` on every model. Grepped the new files for credential-shaped literals:
   none. `.env.example` correctly unchanged (no new secret keys). Composition verified by
   `test_filter_then_deduplicate_composition_via_registry`,
   `test_trim_then_filter_composition_order_matters`, and
   `test_build_operations_from_node_config_round_trips`.
7. **Tests.** `tests/pipeline/test_row_operations.py` — 51 tests, all in-memory `list[dict]`, no
   network. Covers every operator, `ALL`/`ANY`, missing/`None` operands, incomparable-type →
   `False`, arity rejections, latest/earliest, ties under both strategies, missing `order_by` under
   both, all-missing fallback, multi-column keys, null key components, first-appearance output order,
   non-mutation of upstream records, and composition with a DANDER-71 field op.
8. **Steering / toolchain.** `ruff check` and `ruff format --check` clean on all four touched files;
   `mypy src/dander` (77 files) clean; `mypy` on both touched test files clean; `uv run pytest
   tests/pipeline -q` 100% green (386 tests). The six `tests/cli/` failures in the full-suite run are
   Rich ANSI-vs-plain-text assertion mismatches in `test_catalog_cli`/`test_cli`/`test_metadata_cli`/
   `test_transform_cli` — no code path shared with this ticket; independently confirmed unrelated.
   The DANDER-69 `tests/pipeline/test_operations.py` ripple fix (save/restore of `_TEST_KIND`'s prior
   registration + `monkeypatch.delitem` for the unregistered-kind tests) is the right fix and keeps
   both tests meaningful now that every `OperationKind` member is registered.

**Non-blocking observations (no rework required; fold into a future ticket if desired):**

1. `DeduplicateOperation` does not guard `TypeError` from a *heterogeneous* `order_by` column
   (e.g. a group where `updated_at` is `str` on one row and `int` on another) — the `(has_value, …)`
   sort key only makes the `None` case safe. `filter_rows` catches this case and returns `False`;
   `deduplicate` would surface a bare `TypeError` out of `max()`. Outside AC 3's scope (which covers
   ties and missing values only) and the docstrings scope their "never raises" claim correctly, but a
   typed, actionable error would read better against `steering/02-engineering.md`'s "fail loud with
   actionable context".
2. `FieldCondition.value` accepts any scalar, while its sibling `dander.pipeline.request_spec`
   rejects raw-credential-shaped literals via `looks_like_raw_credential`. This matches the design
   the ticket approved (`value` is an opaque comparison operand, not a value sent anywhere), so it is
   not a violation — but reusing that helper would be cheap defense-in-depth against someone pasting
   a real key into a `filter_rows` predicate.
3. `test_filter_rows_predicate_value_is_an_opaque_literal_not_a_secret_channel` uses
   `"stripe-fake-not-a-real-secret"`. Not a steering violation — it is self-evidently fake and
   follows the existing repo precedent of `"AKIAfake-not-a-real-access-key"` in `tests/pipeline/
   test_request_spec.py`. Avoid the literal `sk_live_`/`sk_test_`/`rk_live_` prefixes here — those
   are exactly what gitleaks's built-in `stripe-access-token` rule regexes on (confirmed by running
   `gitleaks detect` locally during the 2026-08-05 branch reconciliation, which is why this example
   was reworded away from an earlier `sk_live_`-prefixed draft).
4. `test_deduplicate_null_key_component_forms_its_own_group_not_rejected` uses an explicit
   `{"id": None}` rather than an *absent* `id`. Behaviorally identical (both read through
   `record.get`), so coverage is not actually short; noted only because the docstring says
   "null/**absent**".
5. Cosmetic: the trailing comment in `test_trim_then_filter_composition_order_matters` reads
   `# "  ok " != "ok"` while the fixture record is `{"status": " ok "}` (single spaces).
