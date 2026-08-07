---
id: DANDER-70
title: Add pre_sql / post_sql pipeline operation hooks
status: open
component: python
epic: pipeline-operations
depends_on: [DANDER-69]
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Local `main` was reset onto `teammate/main` (Harrison's fork) as the trunk — see
`steering/00-project-overview.md` Decision Log, 2026-08-05 entry. Real, still-open gap:
`src/dander/pipeline/operations.py` ships `truncate_string`/`trim_whitespace`/`default_value`/
`filter_rows` only. Its docstring is explicit: "Provider write-back, arbitrary SQL hooks,
deduplication, and operations that change the declared schema are intentionally absent."
`docs/decisions.md`, "2026-08-05 — Pipeline operations execute after raw ingestion" confirms:
"arbitrary SQL hooks ... require separate product decisions rather than entering through this
slice." Treat that as a prerequisite design gate before implementing against the Design below,
which predates the current `OperationKind`/`OperationSpec`/CTE-compiler architecture and needs a
fresh pass against it.

## Context

Some node behavior cannot be expressed as a per-record transform: staging cleanup before a load,
index/partition maintenance, or a reconciliation statement after a write. Pipelines need declarative
`pre_sql` / `post_sql` hooks that run **against BigQuery directly** — before and after a node's main
work respectively — configured in node YAML per the "config-driven over code-driven" rule in
`steering/02-engineering.md`.

Unlike the field/row operations (DANDER-71/72) that operate on the in-flight record stream via
`PipelineOperation.apply()`, these hooks run SQL and do not touch the stream. This ticket wires them
into the DANDER-69 framework as a distinct hook phase.

## Acceptance Criteria

- [ ] `pre_sql` and `post_sql` are declarable on a node's config (extending the DANDER-69 wiring in
      `src/dander/pipeline/node_config.py`) as ordered SQL statements, distinct from the record-stream
      `operations` list.
- [ ] Execution runs `pre_sql` before the node's main operation and `post_sql` after, against a
      BigQuery client behind an injectable seam (no hard dependency on a live client in unit tests),
      consistent with how the writer module abstracts BigQuery execution.
- [ ] The SQL is treated as configuration, never a place for secret literals; any credential/
      connection detail is resolved indirectly (`steering/01-security.md`), and SQL text is not
      emitted into logs in a way that could leak sensitive values.
- [ ] Hook failures fail loud with actionable context that names the node and phase, without
      swallowing the exception (`steering/02-engineering.md`).
- [ ] Empty/absent `pre_sql`/`post_sql` is a valid no-op and does not change existing node behavior.
- [ ] Unit tests cover: pre/post ordering relative to the main operation, multiple ordered
      statements, the no-op case, and error propagation, using a fake/mock BigQuery seam (no
      network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

`pre_sql`/`post_sql` are a **separate concern** from DANDER-69's `operations`. DANDER-69 defines
`PipelineOperation.apply(records) -> records` — an in-flight record-stream transform. These hooks
never touch the stream: they run ordered SQL statements against BigQuery *around* a node's main
work. So this ticket does **not** add an `OperationKind`/`PipelineOperation` subclass; it adds a
parallel, small hook subsystem and a single ordering seam that DANDER-69's node executor calls.

The shape is deliberately minimal and mirrors seams already in the repo:

- **Config:** two ordered `list[str]` fields (`pre_sql`, `post_sql`) added to the typed node
  config alongside DANDER-69's `operations`, validated at load time. Each statement is a plain
  string of BigQuery Standard SQL; secret references are never embedded (`steering/01-security.md`)
  — SQL is treated purely as configuration.
- **Execution:** a `SqlHookRunner` that takes an **injected** BigQuery client behind a tiny
  `Protocol` (the same `query(sql).result()` seam already used by `BigQueryGraphRunner` in
  `runtime.py` and the writers in `bigquery.py`), runs each statement in declared order, and
  raises a typed `SqlHookError` naming the node id, phase, and statement index — never the SQL
  text — on the first failure, chaining the original exception as `__cause__` (fail loud, don't
  swallow, don't leak).
- **Ordering seam:** a pure `run_node_with_sql_hooks(...)` helper runs `pre_sql`, then invokes a
  caller-supplied `main` callable (the node's real work, owned by DANDER-69's dispatch), then
  `post_sql`. This is the one call site DANDER-69's runtime uses, and it makes pre/main/post
  ordering unit-testable without a live client or the full runtime.

Empty/absent `pre_sql`/`post_sql` default to `[]` → the runner issues zero queries → existing node
behavior is unchanged (no-op), satisfying the backward-compat criterion.

### Interfaces / classes

New module **`src/dander/pipeline/sql_hooks.py`**:

- `SqlHookPhase(StrEnum)` — `PRE = "pre"`, `POST = "post"`. Names the phase in errors (matches the
  `StrEnum` convention used by `NodeType`/`WriteMode`).
- `_HookJob(Protocol)` — `result() -> object` (mirrors `runtime._QueryJob`).
- `SqlHookClient(Protocol)` — `query(self, sql: str) -> _HookJob`. The injectable seam; structurally
  compatible with `google.cloud.bigquery.Client` and with the runtime's `_BigQueryClient`, so the
  runtime passes its existing client straight through (no second client, no credentials in tests).
- `SqlHookError(RuntimeError)` — carries `node_id: str`, `phase: SqlHookPhase`,
  `statement_index: int`; `__str__` is e.g. `"post_sql hook statement 2 failed for node 'load_x'"`
  — node + phase + index only, never the statement body.
- `SqlHookRunner`:
  - `__init__(self, client: SqlHookClient)` — client is **required and injected** (no default
    `bigquery.Client(...)` construction here; the runtime already owns one and hands it in).
  - `run_phase(self, statements: Sequence[str], *, node_id: str, phase: SqlHookPhase) -> None` —
    iterate in order, `self._client.query(stmt).result()` per statement; wrap any raised exception
    in `SqlHookError(node_id, phase, index)` via `raise ... from error`. Each statement runs as its
    own query (not one concatenated script) so failure attribution by index is exact.
- `run_node_with_sql_hooks(*, runner: SqlHookRunner, node_id: str, pre_sql, post_sql, main: Callable[[], None]) -> None`
  — the ordering seam: `run_phase(pre)` → `main()` → `run_phase(post)`.

Changes to **`src/dander/pipeline/node_config.py`**:

- Add `pre_sql: list[str] = Field(default_factory=list)` and
  `post_sql: list[str] = Field(default_factory=list)` to the shared **`NodeConfig`** base, next to
  where DANDER-69 lands `operations`, so any node type can declare them and they round-trip through
  `resolve_node_config`/`model_dump` losslessly. A `field_validator` (`mode="after"`, per-item)
  strips each entry and rejects empty/blank statements with a message that names the field and the
  index only (never the value).
- Add `hide_input_in_errors=True` to `NodeConfig.model_config` (same rationale already documented on
  `WriterConfig`/`Node`/`RequestSpec`): a `ValidationError` on a hook statement must not echo the
  rejected SQL back into the exception string (`steering/01-security.md`).

No change to `runtime.py` behavior is *required* by this ticket beyond exposing the seam; the live
integration point (where `BigQueryGraphRunner._materialize`'s work becomes the `main` callable) is
established by DANDER-69's node-operation dispatch, which this hook phase plugs into via
`run_node_with_sql_hooks`. The runtime passes its existing `_BigQueryClient` to `SqlHookRunner`.

### Files to touch / create

- **Create** `src/dander/pipeline/sql_hooks.py` — `SqlHookPhase`, `SqlHookClient`/`_HookJob`
  Protocols, `SqlHookError`, `SqlHookRunner`, `run_node_with_sql_hooks`. Module docstring states the
  responsibility and the "distinct from `operations` / never touches the record stream" boundary.
- **Edit** `src/dander/pipeline/node_config.py` — add `pre_sql`/`post_sql` fields + validator to
  `NodeConfig`; add `hide_input_in_errors=True`.
- **Edit** `src/dander/pipeline/__init__.py` — export the new public names.
- **Create** `tests/pipeline/test_sql_hooks.py` — a `FakeSqlHookClient` (records executed SQL in
  order; can be primed to raise on the N-th statement) drives all cases.
- **Edit** the node-config test module (e.g. `tests/pipeline/test_node_config.py`) — validation +
  round-trip cases for the new fields.

### Trade-offs

- **`list[str]` vs a `SqlHook` value object:** chose plain strings + a validator; the AC asks only
  for "ordered SQL statements," and a named-hook model is speculative generality
  (`steering/02-engineering.md`).
- **Fields on `NodeConfig` base vs `TargetNodeConfig` only:** the motivating cases (staging cleanup,
  partition maintenance, post-write reconciliation) are target-centric, but placing them on the
  shared base keeps them consistent with where DANDER-69 puts `operations` and avoids duplicating
  the field across subclasses. If DANDER-69 instead scopes `operations` to specific subclasses, mirror
  that placement. (Flagged for the Code agent.)
- **Per-statement queries, no implicit transaction wrapper:** each statement is independent so error
  attribution by index is exact; a caller needing atomicity authors an explicit `BEGIN…COMMIT`
  script as a single statement. Matches how `BigQueryGraphRunner` composes its own scripts.
- **Client required (not defaulted):** the runtime already constructs one BigQuery client; injecting
  it avoids a duplicate client and keeps unit tests network-free by construction.

### Test seams

- Mocked: the BigQuery client, via `FakeSqlHookClient` implementing `SqlHookClient` — appends each
  received SQL string to an ordered log and returns a fake `_HookJob`; a variant raises on a chosen
  index. No network (`steering/02-engineering.md`).
- Unit-tested: (1) pre/main/post ordering via `run_node_with_sql_hooks` with `main` appending a
  sentinel to the same shared log; (2) multiple statements execute in declared order; (3) no-op —
  empty/absent lists issue zero `query` calls and don't invoke `.result()`; (4) error propagation —
  a raising statement yields `SqlHookError` whose message contains the node id, phase, and index but
  **not** the SQL body, with the original exception as `__cause__`; (5) node-config validation —
  blank statement rejected, and a `pre_sql`/`post_sql` list round-trips through
  `resolve_node_config` + `model_dump` unchanged.

### Notes for the Code agent

- Depends on DANDER-69: import/extend whatever `NodeConfig` field placement DANDER-69 establishes
  for `operations`; keep `pre_sql`/`post_sql` **beside** it, not merged into the `operations` list.
- Do not reimplement generic row validation here (that stays `GenericTestMetadata`), and do not add
  an `OperationKind` member — these hooks are not stream operations.

## Implementation Notes

Implemented per the Design section. This revision addresses the 2026-08-04 review addendum in
full: the `mypy`-failing test helper is removed, `_dump_graph_payload` now omits empty
`pre_sql`/`post_sql` the same way it already omits empty `operations` (so there is no longer any
deviation from the `operations` precedent), the new on-disk keys are documented in
`src/dander/pipeline/README.md`, and the test counts below are corrected.

**Created `src/dander/pipeline/sql_hooks.py`:**
- `SqlHookPhase(StrEnum)` — `PRE = "pre"`, `POST = "post"`.
- `_HookJob(Protocol)` — `result() -> object`; `SqlHookClient(Protocol)` — `query(self, sql:
  str) -> _HookJob`. Structurally compatible with `google.cloud.bigquery.Client` and with
  `dander.pipeline.runtime._BigQueryClient` (extra optional `job_config` keyword on the real
  client doesn't break Protocol compatibility), so the runtime's existing client can be handed to
  `SqlHookRunner` directly.
- `SqlHookError(RuntimeError)` — carries `node_id`, `phase: SqlHookPhase`,
  `statement_index: int`. `__str__` is `"<phase>_sql hook statement <n> failed for node
  '<node_id>'"`, e.g. `"post_sql hook statement 2 failed for node 'load_x'"` — node, phase, and
  position only, never the SQL text. `statement_index` is **1-based** (a deliberate, documented
  choice — the design's own example message, "statement 2", reads naturally as the second
  statement; `enumerate(statements, start=1)` makes the index and the human-readable position the
  same number, so no `+1` translation is needed anywhere the index is displayed or asserted).
- `SqlHookRunner(client: SqlHookClient)` — client is a required constructor arg (no default
  `bigquery.Client(...)` construction). `run_phase(statements, *, node_id, phase)` runs each
  statement as its own `query(...).result()` call in declared order (never concatenated into one
  script, so `statement_index` attributes exactly), wraps any raised exception as
  `SqlHookError(...)` via `raise ... from error` (chains the original as `__cause__`, never
  swallowed), and issues zero queries for an empty list.
- `run_node_with_sql_hooks(*, runner, node_id, pre_sql, post_sql, main)` — the ordering seam:
  `run_phase(pre)` -> `main()` -> `run_phase(post)`. A `pre_sql` failure prevents `main`/`post_sql`
  from running; a `main` failure propagates **unwrapped** (it is not a hook-statement failure) and
  prevents `post_sql` from running.

**Edited `src/dander/pipeline/node_config.py`:**
- Added `pre_sql: list[str] = Field(default_factory=list)` and `post_sql: list[str] =
  Field(default_factory=list)` to the shared **`NodeConfig`** base (same placement as DANDER-69's
  `operations`), so every modeled node type inherits them.
- Added a single `field_validator("pre_sql", "post_sql", mode="after")` that strips each
  statement and rejects a blank one with a message naming only the field
  (`pre_sql`/`post_sql`) and its 0-based index (e.g. `"post_sql[1] must not be a blank SQL
  statement"`) — never the value.
- Added `hide_input_in_errors=True` to `NodeConfig.model_config` (it previously had none set;
  `WriterConfig`/`OperationSpec`/`Node`/`RequestSpec` already set it for the identical reason): a
  `ValidationError` on a hook statement (or any other `NodeConfig` field) must not echo the
  rejected input back into the exception string.
- No change to `resolve_node_config`: the new fields ride along inside the typed config models it
  already routes, exactly as the Design anticipated.

**Edited `src/dander/pipeline/__init__.py`:** re-exports `SqlHookClient`, `SqlHookError`,
`SqlHookPhase`, `SqlHookRunner`, `run_node_with_sql_hooks`; added to `__all__`; module docstring
updated to mention the new sibling hook-phase module.

**Created `tests/pipeline/test_sql_hooks.py`** (8 tests, no network): a `FakeSqlHookClient`
implementing `SqlHookClient` records executed SQL in call order and can be primed to raise on a
chosen call number, optionally interleaving into a caller-supplied shared log so pre/main/post
ordering is asserted against one exact sequence. `FakeSqlHookClient` is passed directly into
`SqlHookRunner(...)` at every call site — no narrowing helper is needed because
`FakeSqlHookClient.query(sql: str) -> _FakeHookJob` already structurally satisfies
`SqlHookClient` (and `_FakeHookJob.result() -> object` satisfies `_HookJob`). Covers:
empty-statements no-op (`run_phase` and `run_node_with_sql_hooks` with both lists empty issue
zero queries); full pre -> main -> post ordering via a shared log; multiple statements within one
phase running in declared order; a failing statement raising `SqlHookError` with the correct
`node_id`/`phase`/1-based `statement_index`, a message containing node/phase/index but not the
SQL body, and the original exception chained as `__cause__` (only statements up to and including
the failure ran); a `pre_sql` failure message says `pre_sql` not `post_sql` (and vice versa is
implied by the error-attribution tests); a `pre_sql` failure prevents `main`/`post_sql` from
running; a `main` failure propagates unwrapped (plain `ValueError`, not `SqlHookError`) and
prevents `post_sql`.

**Edited `tests/pipeline/test_node_config.py`:** added 9 cases for the new fields — default-empty
no-op, whitespace stripping, declared-order preservation, blank-statement rejection for both
`pre_sql` and `post_sql` (parametrized) asserting the field/index appear and the value does not,
YAML and JSON round-trip of non-empty `pre_sql`/`post_sql` (order-preserving, model-equal after
reload), a pre-DANDER-70-style graph (no `pre_sql`/`post_sql` authored) still loading with both
defaulting to `[]` and round-tripping unchanged, and a new
`test_hookless_node_dump_omits_pre_sql_and_post_sql_keys` asserting a bare target node's dumped
`config` dict contains **neither** the `pre_sql` nor the `post_sql` key (17 new tests total across
both files, counting the parametrized blank-statement case as 2: 8 in `test_sql_hooks.py`, 9 in
`test_node_config.py`).

**Edited `src/dander/pipeline/graph.py` (addendum fix):** `_dump_graph_payload` now pops
`pre_sql`/`post_sql` from a node's dumped `config` dict when the corresponding
`NodeConfig.pre_sql`/`NodeConfig.post_sql` is empty, immediately after the existing
`operations`-omission block and following its exact shape (guard on `isinstance(config,
NodeConfig)` and `isinstance(dumped_config, dict)`). This closes the gap the first review round
flagged: a bare target node's dumped `config` is `{}` again (was `{"pre_sql": [], "post_sql":
[]}`), and a bare source node's is `{"connector": null, "endpoint": null}` again (was
`{"pre_sql": [], "post_sql": [], "connector": null, "endpoint": null}`), restoring byte-identical
round-tripping with pre-DANDER-70 graphs — including through `dander graph serve` / `PUT
/v1/graph`, which atomically rewrites the operator's own graph file. The
`_dump_graph_payload`/`dump_graph_to_yaml`/`dump_graph_to_json` docstrings were updated to name
the new omission, the same way DANDER-69 documented the `operations` one. The "Deviation from
Design" note from the first round no longer applies and has been removed — there is no deviation.

**Edited `src/dander/pipeline/README.md` (addendum fix):** added a `NodeConfig.pre_sql` /
`.post_sql` row to §"JSON form & on-disk keys", worded like the `Edge.join`/`Node.cursor` rows
("omitted entirely (not `[]`) when the list is empty, so hook-less nodes round-trip byte-identical
to pre-DANDER-70 graphs"), and a sentence in §"Typed per-node-type config" noting `NodeConfig` now
carries ordered `pre_sql`/`post_sql` BigQuery Standard SQL statements — configuration only, never
a secret literal (`steering/01-security.md`) — executed by `dander.pipeline.sql_hooks`. No other
part of the README was touched.

**No change to `runtime.py`:** as the Design explicitly notes, no live wiring is required by this
ticket — `runtime.py` has no per-node main-operation dispatch yet for `sql_hooks`/`operations` to
plug into (DANDER-69 didn't add one either, per its own Files-to-Touch list); that integration
point is future work the Design defers to DANDER-69's dispatch. `run_node_with_sql_hooks` and
`SqlHookRunner` are fully usable/testable now via the injected `SqlHookClient` seam.

**Tooling (addendum re-run):** `ruff check .` and `ruff format --check .` — clean on all touched
files (one pre-existing, unrelated formatting diff in `src/dander/security/secret_manager.py`,
untouched by this ticket). Bare `uv run mypy` (CI scope — `[tool.mypy].files = ["src", "tests"]`
in `pyproject.toml`, so `src` **and** `tests` are checked, not `mypy src`) — 152 source files, no
issues; this is the command the first review round ran to find the `unused-ignore` error, now
confirmed clean. `pytest tests/pipeline` — 100% pass (295 tests). Full `pytest` run: 695 passed, 6
failed — the same pre-existing `tests/cli/` failures documented in DANDER-69's Implementation
Notes (Rich/ANSI escape-code assertions), unrelated to this change and unaffected by it.

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — FAIL

The hook subsystem itself is well built: `SqlHookRunner`/`run_node_with_sql_hooks` are a clean,
network-free injected seam; the error type names node + phase + index and never the SQL body,
chaining `__cause__`; `hide_input_in_errors=True` keeps rejected statements out of
`ValidationError` strings; ordering, multi-statement, no-op, and error-propagation cases are all
covered. Two blocking issues, both small and mechanical.

**Blocking**

1. **CI's `mypy` is red on this change** (`steering/languages/python.md`: "CI runs: `ruff check` →
   `ruff format --check` → `mypy` → `pytest`. All must pass"). `[tool.mypy]` in `pyproject.toml`
   sets `files = ["src", "tests"]`, so the bare `mypy` CI command type-checks tests too; the
   Implementation Notes only report `mypy` over `src/`, which is why this was missed. Running the
   configured command:

   ```
   tests/pipeline/test_sql_hooks.py:63: error: Unused "type: ignore" comment  [unused-ignore]
   Found 1 error in 1 file (checked 152 source files)
   ```

   **Fix:** delete the `_assert_is_sql_hook_client` helper (`tests/pipeline/test_sql_hooks.py`
   lines 60-63) entirely and pass `FakeSqlHookClient()` straight into `SqlHookRunner(...)` at all
   seven call sites. The helper is not needed: `FakeSqlHookClient.query(sql: str) ->
   _FakeHookJob` already structurally satisfies `SqlHookClient` (`_FakeHookJob.result() ->
   object` satisfies `_HookJob`) — verified: a scratch module doing `SqlHookRunner(
   FakeSqlHookClient())` passes `mypy --strict` with no ignore and no cast. Re-run bare `uv run
   mypy` (not `mypy src`) to confirm clean.

2. **Every dumped node now gains `pre_sql: []` / `post_sql: []`, breaking the established on-disk
   omission contract** — this is the deviation the Implementation Notes flagged, and the intended
   reading was the `operations` precedent. Confirmed on the current tree:

   ```
   config for a target node with nothing declared -> {"pre_sql": [], "post_sql": []}
   config for a source node with nothing declared -> {"pre_sql": [], "post_sql": [],
                                                     "connector": null, "endpoint": null}
   ```

   Previously these were `{}` and `{"connector": null, "endpoint": null}`. DANDER-69 deliberately
   extended `_dump_graph_payload` so an empty `operations` never reaches disk, and rewrote the
   `graph.py` docstrings around exactly that guarantee; leaving `pre_sql`/`post_sql` out of the
   same omission set contradicts the pattern one ticket later. It is not purely cosmetic: `dander
   graph serve` / `PUT /v1/graph` (`graph_service.py` -> `dump_graph_to_yaml`/`dump_graph_to_json`)
   **atomically rewrites the operator's own graph file**, so every node in every served file
   silently acquires two new keys, and `src/dander/pipeline/README.md` §"JSON form & on-disk keys"
   documents `"config": {}` for a bare target node, which is now false. AC 5 ("does not change
   existing node behavior") should hold for the serialized form too, on the precedent this repo
   just set.

   **Fix:** in `_dump_graph_payload` (`src/dander/pipeline/graph.py`, immediately after the
   existing `if isinstance(config, NodeConfig) and not config.operations:` block), pop `pre_sql`
   and `post_sql` from the dumped `config` dict when the corresponding `NodeConfig` list is empty,
   following that block's exact shape (guard on `isinstance(config, NodeConfig)` and on
   `isinstance(dumped_config, dict)`). Update the `_dump_graph_payload`, `dump_graph_to_yaml`, and
   `dump_graph_to_json` docstrings the same way DANDER-69 did, naming the new omission. Add a test
   in `tests/pipeline/test_node_config.py` asserting that a node declaring neither hook dumps a
   `config` dict containing **neither** the `pre_sql` nor the `post_sql` key, and that a node
   declaring them still round-trips in order (keep the existing round-trip tests — they must
   continue to pass, since reload restores `[]` by default).

**Also required (documentation, `steering/languages/python.md`)**

3. `src/dander/pipeline/README.md` — the new keys are part of the authorable on-disk schema and
   are currently undocumented. Add a `pre_sql` / `post_sql` row to the §"JSON form & on-disk keys"
   table, worded like the existing `Edge.join` / `Node.cursor` rows ("omitted entirely when the
   list is empty, so hook-less nodes round-trip byte-identical to pre-DANDER-70 graphs" — accurate
   once item 2 lands), and mention in §"Typed per-node-type config" that `NodeConfig` now carries
   `pre_sql`/`post_sql` (ordered BigQuery Standard SQL, configuration only, never a secret
   literal) executed by `dander.pipeline.sql_hooks`. Keep it to those two spots; do not restructure
   the README.

4. Correct the Implementation Notes before re-review: `tests/pipeline/test_sql_hooks.py` contains
   **8** test functions, not 11 (so 16 new tests total with the 8 in `test_node_config.py`, not
   19), and the tooling line should record the CI-scope `mypy` (src **and** tests) result rather
   than `mypy src`.

**Verified clean and not at issue:** no hardcoded secrets or credential-shaped literals anywhere in
the diff; no new `.env` keys needed; no SQL text, config values, or PII in any log, exception
message, or fixture (`SqlHookError.__str__` and the blank-statement `ValueError` both carry
identifiers/indices only); no client constructed inside `sql_hooks.py`, so unit tests are
network-free by construction; `ruff check` and `ruff format --check` pass; `pytest tests/pipeline`
passes (296 tests); no `OperationKind` member added and no row-validation logic duplicated, per the
Design's explicit boundary; no `runtime.py` change, which the Design does defer.

### 2026-08-04 — PASS

Both blocking items from the first round are fixed, verified on the current tree, and the two
"also required" items landed as well. Re-review found no new issues.

**Addendum items — verified closed**

1. **CI `mypy` green.** `uv run mypy` (bare, CI scope — `[tool.mypy].files = ["src", "tests"]`):
   `Success: no issues found in 152 source files`. The `_assert_is_sql_hook_client` helper and its
   `type: ignore` are gone from `tests/pipeline/test_sql_hooks.py`; `FakeSqlHookClient()` is passed
   straight into `SqlHookRunner(...)` at every call site and structurally satisfies
   `SqlHookClient` with no cast.
2. **On-disk omission contract restored.** `_dump_graph_payload` now pops `pre_sql`/`post_sql`
   when empty, immediately after the `operations` block and in its exact shape. Verified directly:
   a bare target node dumps `config == {}` and a bare source node dumps
   `{"connector": null, "endpoint": null}` — byte-identical to pre-DANDER-70 — while a node
   declaring `pre_sql=["a"], post_sql=["b"]` dumps both keys in order. Docstrings on
   `_dump_graph_payload` / `dump_graph_to_yaml` / `dump_graph_to_json` name the new omission the
   way DANDER-69 named `operations`, and correctly note that `exclude_none` would not have covered
   an empty-list default. `test_hookless_node_dump_omits_pre_sql_and_post_sql_keys` asserts the
   omission; the round-trip tests still pass.
3. **README documented.** `src/dander/pipeline/README.md` gained exactly the two edits asked for:
   a `NodeConfig.pre_sql` / `.post_sql` row in §"JSON form & on-disk keys" worded like the
   `Edge.join` / `Node.cursor` rows, and a sentence in §"Typed per-node-type config". No
   restructuring.
4. **Implementation Notes corrected.** Counts now match the tree: `test_sql_hooks.py` has 8 test
   functions; `test_node_config.py` adds 8 functions / 9 cases (blank-statement is parametrized
   over both fields) — 17 new cases, as stated. The tooling line records the CI-scope `mypy`.

**Acceptance criteria**

- AC1 — `pre_sql`/`post_sql` are `list[str]` fields on the shared `NodeConfig` base beside
  DANDER-69's `operations`, ordered, validated at load time, and distinct from the record-stream
  `operations` list. Met.
- AC2 — `run_node_with_sql_hooks` runs `run_phase(PRE)` → `main()` → `run_phase(POST)` against an
  injected `SqlHookClient` `Protocol` mirroring the `query(sql).result()` seam already used by
  `runtime._BigQueryClient` and `dander.writer.bigquery`. No client is constructed in
  `sql_hooks.py`, so unit tests are network-free by construction. Met.
- AC3 — SQL is configuration only; `SqlHookError.__str__` carries node id, phase, and 1-based
  position and never the statement body (asserted by
  `test_run_phase_wraps_failure_in_sql_hook_error_naming_node_phase_index`); `sql_hooks.py`
  performs no logging at all; `hide_input_in_errors=True` on `NodeConfig` keeps a rejected
  statement out of `ValidationError` strings; the blank-statement `ValueError` names field + index
  only. Met.
- AC4 — every statement failure is wrapped as `SqlHookError(node_id, phase, statement_index)` via
  `raise ... from error`, so the original is chained as `__cause__` and never swallowed; a `main`
  failure propagates unwrapped, which is correct (it is not a hook-statement failure). Met.
- AC5 — both fields default to `[]` → zero queries, and the serialized form is unchanged too
  (item 2 above), so behavior is identical to a pre-DANDER-70 node in memory and on disk.
  `test_dander_69_style_graph_with_no_sql_hooks_still_round_trips` covers the reload path. Met.
- AC6 — 8 tests in `test_sql_hooks.py` cover pre→main→post ordering against one shared log,
  multiple statements in declared order, both no-op paths, error attribution (node/phase/index,
  no SQL body, `__cause__` chained, later statements not run), a `pre_sql` failure skipping
  `main`/`post_sql`, and an unwrapped `main` failure skipping `post_sql` — all through
  `FakeSqlHookClient`, no network. Met.
- AC7 — no steering violations found (below). Met.

**Security (zero tolerance) — clean.** No hardcoded secrets or credential-shaped literals anywhere
in the diff; no new secret keys, so no `.env.example` change needed; no secrets, config values, or
PII in any log, exception message, docstring, or fixture; no client/credentials constructed in the
new module.

**Tooling.** `ruff check .` clean. `ruff format --check .` — only the pre-existing, unrelated
`src/dander/security/secret_manager.py` diff (file untouched by this ticket). `uv run mypy` clean
(152 files). `pytest tests/pipeline` — 100% pass. Full `pytest` — the same 6 pre-existing
`tests/cli/` failures (Rich/ANSI escape sequences breaking substring assertions; the expected text
is present but interleaved with escape codes), unaffected by and unrelated to this change.

**Non-blocking observations (no action required)**

- The validator's index is 0-based (`post_sql[1]`, matching list-subscript/Pydantic `loc`
  notation) while `SqlHookError.statement_index` is 1-based ("statement 2", matching the Design's
  own example message). Both are internally consistent and explicitly documented; noting only so
  a future reader is not surprised.
- `_dump_graph_payload` now has three consecutive `isinstance(config, NodeConfig)` blocks
  (`operations`, `pre_sql`, `post_sql`) that could collapse into one. Kept as-is deliberately —
  the addendum asked for the existing block's exact shape, and it stays diff-legible.
- No `runtime.py` wiring, which the Design explicitly defers to DANDER-69's node-operation
  dispatch. `run_node_with_sql_hooks` is fully usable and tested today via the injected seam.
