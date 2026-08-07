---
id: DANDER-75
title: Add upsert connector capability protocol
status: done
component: python
epic: connector-capabilities
depends_on: [DANDER-64]
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Same gap and same prerequisite gate as DANDER-73 — see that ticket's reconciliation note. Local
`main` was reset onto `teammate/main` as the trunk; write-back semantics still need a Decision Log
entry per `docs/decisions.md`, "2026-08-05 — Optional source capabilities remain structural and
read-only" before this is implemented against the current `SourceCapabilities` pattern.

## Context

Write-back is now an optional, opt-in connector capability per the Decision Log entry 2026-08-04
("Write-back is now an optional, opt-in connector capability, not a hard non-goal") in
`steering/00-project-overview.md`. A connector implements a write-back operation only if the
underlying API genuinely supports it, discovered via `ConnectorAdapter`; the core read →
land-in-BigQuery path is unchanged and remains mandatory.

This ticket defines the `upsert` capability Protocol — create-or-update keyed on the business key —
and registers it with the DANDER-64 `ConnectorAdapter`/`ConnectorOperation` mechanism, following the
same interface-first shape as the read-side capabilities (`steering/02-engineering.md`).

Scope note — `bulk_upsert`: a dedicated bulk write-API path (mirroring the read-side `bulk_extract`
distinction) is only meaningful for sources that expose a genuine bulk write endpoint. The read-side
`bulk_extract` is itself deferred/not ticketed in this batch, so to keep the write side symmetric,
`bulk_upsert` is **not** a separate ticket now. It is captured as a documented follow-on below and
should be split out only when a first source with a real bulk write API needs it.

## Acceptance Criteria

- [ ] A `runtime_checkable` `Protocol` for `upsert` (e.g. `SupportsUpsert`) is defined in
      `src/dander/ingestion/capabilities.py` with a method that accepts an endpoint name and a record
      mapping, resolves create-or-update by the endpoint's business key, and returns the resulting
      record's identity or mapping.
- [ ] The Protocol is registered against a new `ConnectorOperation.UPSERT` member in the DANDER-64
      registry and detected by `ConnectorAdapter`.
- [ ] The business-key semantics are consistent with `Endpoint.primary_key` (source-side) and the
      `WriteTarget.business_key` / SCD1 keying convention, so the same key concept is used
      throughout.
- [ ] A source without the capability is reported unsupported and requesting it raises the DANDER-64
      unsupported-operation error.
- [ ] No secret or credential value appears in any error message (`steering/01-security.md`);
      credential access still routes through the audited auth strategy.
- [ ] The ticket records the `bulk_upsert` follow-on: a future `ConnectorOperation.BULK_UPSERT` +
      Protocol added the same way, gated on a source with a real dedicated bulk write API.
- [ ] Unit tests cover detection-positive, detection-negative, the create branch, and the update
      branch via a fake source (no network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

This is a Layer 1 write-back capability that plugs into the DANDER-64 mechanism exactly like the
read-side capabilities (DANDER-65..68) and the sibling write-back tickets (create/update/delete).
It is **pure additive surface**: one new `runtime_checkable` `Protocol` (`SupportsUpsert`), one new
`ConnectorOperation.UPSERT` enum member, and one new entry in `CAPABILITY_REGISTRY` — with **zero
edits to `ConnectorAdapter` logic** (Open/Closed, as DANDER-64's registry indirection is designed to
allow). Detection, the empty-set case, and the unsupported-operation error path are all inherited
from DANDER-64; this ticket only adds the operation and its contract.

`upsert` is the create-or-update operation: the caller passes an endpoint name and a full record
mapping; the source resolves whether that record already exists **by the endpoint's business key**
and either creates it (insert branch) or updates it in place (update branch), returning the
resulting record. Semantically it is the write-side twin of the SCD1 `MERGE` the BigQuery Writer
performs on `WriteTarget.business_key` — the *same key concept* keyed the same way, just applied to
the source system instead of the warehouse. This is why the Protocol carries no separate identity
argument (unlike `SupportsUpdate`, which takes an explicit identity): the identity is *derived from
the record's own business-key fields*, mirroring how a `MERGE` matches on the key columns already
present in the incoming row.

**Business-key semantics (AC-3).** The single source of truth for an endpoint's business key is
`Endpoint.primary_key: list[str]` (see `src/dander/ingestion/source.py`). This is already the field
`runtime.py` maps into `WriteTarget.business_key=tuple(endpoint.primary_key)` for SCD1 writes, so
aligning `upsert`'s key resolution to `Endpoint.primary_key` makes the source-side write-back and
the warehouse-side merge use one key concept end to end. The Protocol method receives only the
endpoint *name* (not the key list); the implementing source is expected to look up
`primary_key` for that endpoint from its own `SourceConfig.endpoints` (each `Source` already holds
`self.config`). The Protocol docstring states this contract; the fake source in the tests
demonstrates it by reading `primary_key` off its config to pick the matching stored record.

**Record shape (path-agnostic).** Both the `record` argument and the return value are typed
`Mapping[str, Any]`, identical to the record shape `Source.extract()` yields, so downstream layers
stay path-agnostic (same rule DANDER-73/74 apply). The return value is the **resulting record
mapping** (post-create or post-update), which necessarily contains the business-key values, so a
caller that only wants the identity can project the key fields out of it — one return type serves
both "identity" and "mapping" phrasings in the AC without an ambiguous union.

**Security (AC-5).** The Protocol declares no credentials and the adapter guard (`require`) raises
only `UnsupportedConnectorOperationError`, whose DANDER-64 message names the source and operation
value only — never a secret, auth ref, or row value. Concrete sources continue to obtain
credentials through the audited auth strategy (`get_credentials`); this Protocol adds no new
credential path and no new place a secret could be logged. No `upsert` implementation against a real
API is in scope here (only the contract + a network-free fake), so there is nothing new to audit
beyond the existing strategy.

### Interfaces / classes (all in `src/dander/ingestion/capabilities.py`)

- **`ConnectorOperation.UPSERT = "upsert"`** — one new member appended to the existing
  `ConnectorOperation` `StrEnum` from DANDER-64, following the same add-member pattern the read-side
  and sibling write-back tickets use. Value is the snake_case string `"upsert"`.

- **`SupportsUpsert(Protocol)`** — `@runtime_checkable`. One method:

  ```python
  def upsert(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]: ...
  ```

  Contract (in the Protocol docstring, per `languages/python.md` — interfaces carry the contract):
  create-or-update the record identified by the endpoint's `primary_key` business-key field(s) drawn
  from `record`; if a record with those key value(s) exists in the source, update it in place,
  otherwise create it. Returns the resulting record mapping (containing the business-key values).
  The docstring states the invariant that the key is `Endpoint.primary_key` for `endpoint`, matching
  `WriteTarget.business_key` / the SCD1 merge key, and that implementations resolve it from their own
  `SourceConfig`. `Raises:` notes that an unknown `endpoint`, or a `record` missing a required
  business-key field, is an implementation-defined `ValueError`-family error naming only the endpoint
  and field name — never a row value or secret.

- **`CAPABILITY_REGISTRY` entry** — add exactly one mapping:
  `ConnectorOperation.UPSERT: SupportsUpsert`. No other change to the registry or to
  `ConnectorAdapter`. Detection, `supports`, `supported_operations`, and `require` all work
  generically over this entry.

### Files to touch / create

- **EDIT `src/dander/ingestion/capabilities.py`** — add the `UPSERT` enum member, the
  `SupportsUpsert` Protocol (with `from __future__ import annotations`, `@runtime_checkable`,
  Google-style docstring), and the one `CAPABILITY_REGISTRY` entry. No `ConnectorAdapter` edits.
  (This module is created by DANDER-64; this ticket extends it.)
- **EDIT `src/dander/ingestion/__init__.py`** — export `SupportsUpsert` (add to imports and to the
  alphabetized `__all__`). `ConnectorOperation` / `ConnectorAdapter` / `CAPABILITY_REGISTRY` /
  `UnsupportedConnectorOperationError` are already exported by DANDER-64.
- **EDIT `tests/ingestion/test_capabilities.py`** — add the `SupportsUpsert` test cases (see seams).
  Reuse the file DANDER-64 creates rather than adding a parallel one.
- **EDIT `src/dander/ingestion/README.md`** (small) — add `upsert` to the list of optional write-back
  capabilities the adapter can detect, keeping the README true to code.

### Test seams (no network, per `steering/02-engineering.md`)

Use a single in-memory **fake source** subclassing `Source` (implementing `discover`/`extract` as
minimal stubs) that also implements `upsert`, backed by a `dict` keyed by the endpoint's
`primary_key` value(s). Its `SourceConfig` declares one `Endpoint` with a `primary_key` (e.g.
`["id"]`) so the fake resolves the key from config exactly as the contract requires. Also define a
plain fake source **without** `upsert` for the negative case.

- **Detection-positive:** `ConnectorAdapter(fake_with_upsert).supports(ConnectorOperation.UPSERT)`
  is `True` and `ConnectorOperation.UPSERT in adapter.supported_operations`.
- **Detection-negative:** a fake source lacking `upsert` reports `supports(UPSERT) is False`, and
  `adapter.require(ConnectorOperation.UPSERT)` raises `UnsupportedConnectorOperationError` (a
  `ValueError`, not `AttributeError`); assert the message contains the source name and `"upsert"`
  and contains no secret-like text.
- **Create branch:** `upsert` on a business-key value not yet present inserts the record; assert the
  returned mapping equals the stored record and the backing store now contains it.
- **Update branch:** `upsert` on a business-key value already present overwrites in place; assert the
  changed field is updated, the returned mapping reflects the change, and no duplicate row was added
  (store size unchanged) — proving create-or-update keys on `primary_key`.

### Trade-offs

- **No separate identity argument (vs. `SupportsUpdate`):** `upsert` derives the identity from the
  record's own business-key fields, matching MERGE-on-key semantics; adding a redundant identity
  parameter would invite it to disagree with the record and duplicate DANDER-74's shape. Accepted:
  the endpoint's `primary_key` is the single arbiter, resolved from config.
- **Return the full record mapping (vs. a bare identity or a `Mapping | scalar` union):** returning
  the resulting record satisfies both "identity or mapping" AC phrasings with one unambiguous type
  and lets callers project the key out; a union return would push branching onto every caller for no
  gain.
- **Structural (`isinstance`/Protocol) detection** carries the same signature-blindness caveat noted
  in DANDER-64 (a source could match `upsert` with a wrong signature); accepted for the same reason —
  it keeps sources free of registration boilerplate and matches the interface-first spirit, with
  signature correctness covered by each concrete source's own tests.

### `bulk_upsert` follow-on (AC-6, documented, not ticketed now)

A dedicated bulk write path is added **the same way** when — and only when — a first source with a
genuine bulk write endpoint needs it: (1) append `ConnectorOperation.BULK_UPSERT = "bulk_upsert"` to
the enum, (2) define a `runtime_checkable` `SupportsBulkUpsert` Protocol with a method taking an
endpoint name and an *iterable* of record mappings and returning the resulting identities/records,
(3) add one `CAPABILITY_REGISTRY` entry `BULK_UPSERT: SupportsBulkUpsert`. It mirrors the read-side
`bulk_extract` distinction, which is itself deferred/not ticketed in this batch; splitting
`bulk_upsert` out now would be speculative generality (no source with a real bulk write API exists
yet), so it stays a documented follow-on to be split out at that point — with zero `ConnectorAdapter`
changes, exactly like `upsert`.

### Notes / flags

- **Dependency ordering:** `capabilities.py` is created by DANDER-64 (currently `in-code`, not
  `done`). This ticket must be built after DANDER-64's module lands; it *edits* that module, it does
  not create it. If built before DANDER-64 merges, coordinate on the shared `ConnectorOperation`
  enum / `CAPABILITY_REGISTRY` / `test_capabilities.py` to avoid conflicts with the other write-back
  tickets (73/74/76), which touch the same three symbols.
- **Business-key resolution helper:** DANDER-73/74/75/76 all need to resolve an endpoint's
  `primary_key` from `SourceConfig`. A small shared helper (e.g. `business_key_for(config, endpoint)`)
  would remove repetition, but it is not required for this ticket and no ticket owns it; flagged for
  the Code agent to lift into a shared helper if the sibling implementations converge, rather than
  designed speculatively here.

## Implementation Notes

**2026-08-05 update:** the note below and the Review Log entry beneath it describe the
pre-reconciliation `ConnectorAdapter` implementation from `backup/local-main-pre-reconcile`, no
longer on this trunk (see the Reconciliation note above). Kept for history. Current implementation
against `teammate/main`'s `SourceCapabilities`:

- `src/dander/ingestion/capabilities.py`: added `ConnectorOperation.UPSERT`, the `SupportsUpsert`
  `Protocol` (`upsert(self, endpoint, record) -> Mapping[str, Any]`, no separate identity
  argument — identity is resolved from the record's own business-key fields), a
  `_CAPABILITY_PROTOCOLS` entry, and `SourceCapabilities.upsert()` (`require()` guard, delegate,
  `isinstance(result, Mapping)` validation) — matching the existing accessor pattern.
- Idempotency/retry/authorization semantics recorded in `docs/decisions.md`, "2026-08-05 —
  Write-back and deleted-record-feed semantics."
- `src/dander/ingestion/__init__.py` / `README.md` updated to export and document it.
- `tests/ingestion/test_capabilities.py`: extended `_CapableSource`, the facade test, and the
  invalid-result and full-operation-set parametrizations to cover `upsert`.
- Verified: `ruff check`/`ruff format --check`/`mypy src/dander/ingestion` clean;
  `pytest tests/ingestion tests/pipeline tests/cli/test_connector_cli.py` green. Done directly in
  this reconciliation session, not through the Design→Code→PR-Review agent pipeline — no PASS
  entry added to Review Log for this pass.

---

Original (superseded) note below:

Implemented exactly as designed — a pure extension of `src/dander/ingestion/capabilities.py`
following the DANDER-65/73/74 pattern, with zero `ConnectorAdapter` edits:

- Added `ConnectorOperation.UPSERT = "upsert"` member (with an `Attributes:` docstring entry) to
  the existing `StrEnum`, appended after `UPDATE`.
- Added `@runtime_checkable class SupportsUpsert(Protocol)` with
  `upsert(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]`. Docstring states:
  no separate identity argument (identity is derived from the record's own business-key fields,
  which are `Endpoint.primary_key` for `endpoint`, resolved by the implementation from its own
  `SourceConfig.endpoints`, not passed in); the write-side twin of the BigQuery Writer's SCD1
  `MERGE` on `WriteTarget.business_key`; the security invariant that no `record`/identity/row
  value ever appears in an exception, log, or message, only the endpoint and error kind; and that
  credential access routes through the audited `AuthStrategy`, not through this method.
- Added one entry `ConnectorOperation.UPSERT: SupportsUpsert` to `CAPABILITY_REGISTRY`, updated
  the registry's docstring history line and the module docstring's capability-history paragraph
  with a DANDER-75 entry (mirroring the DANDER-73/74 entries), including a short note pointing at
  the documented `bulk_upsert` follow-on.
- Exported `SupportsUpsert` from `src/dander/ingestion/__init__.py` (added to the import block and
  to `__all__`, keeping the existing alphabetical order).
- Added `upsert` to the write-back operations named in `src/dander/ingestion/README.md`'s
  "Optional capability discovery" section, plus one sentence noting the business-key-derived
  identity / SCD1-`MERGE` parallel, keeping the README true to code.
- The `bulk_upsert` follow-on (AC-6) is recorded in the ticket's own Design section
  ("`bulk_upsert` follow-on (AC-6, documented, not ticketed now)") — no code added for it, per
  that section and the Decision Log's opt-in-per-source-need framing; this Implementation Notes
  entry is the confirmation the follow-on stayed documentation-only as designed.

**Deviation from Design's file list:** the Design's "Files to touch" says to *edit*
`tests/ingestion/test_capabilities.py`. By the time this ticket was implemented, DANDER-65/66/67/
68/73/74 had all already landed as **separate** `tests/ingestion/test_capabilities_<name>.py`
modules instead (`test_capabilities.py` remains the DANDER-64-only mechanism test, untouched by
every capability ticket since). Followed that established, consistent repo convention instead of
the Design's file list and added `tests/ingestion/test_capabilities_upsert.py` as a new module
mirroring `test_capabilities_update.py`'s shape, rather than editing the shared
`test_capabilities.py` and creating a parallel-file inconsistency with every sibling ticket.
`test_capabilities.py` itself was not touched.

**Tests** (`tests/ingestion/test_capabilities_upsert.py`, new):
- `FakeUpsertSource` — declares one `Endpoint(name="candidates", primary_key=["id"])` in its own
  `SourceConfig`, implements `upsert` by resolving the business-key tuple from that endpoint's
  `primary_key` read off `self.config.endpoints` (not a caller-supplied identity), backed by an
  in-memory `dict[key_tuple, record]`; missing key fields raise `ValueError` naming only the
  endpoint and field.
- `FakePlainSource` — implements only the mandatory `Source` contract (detection-negative case).
- Cases covered: detection-positive (`supports`/`supported_operations`), detection-negative,
  bare `isinstance` structural check, the **create branch** (new business key inserts and the
  returned mapping matches the stored record), the **update branch** (existing business key
  overwrites in place — changed field updates, returned mapping reflects it, store size is
  unchanged, proving create-or-update keys on `primary_key`), and the unsupported-operation raise
  asserting the message contains the source + operation names but no row/secret-marker values.

**Tooling results** (via `uv run`):
- `ruff check` (touched files, then repo-wide `src tests`) — all checks passed. One `SIM108`
  ternary-operator suggestion was fixed in the test fake's `upsert` implementation.
- `ruff format --check` — 3 touched files already formatted.
- `mypy --strict` (touched files) — no issues found.
- `pytest tests/ingestion/` — 112 passed (full ingestion suite, including the 6 new tests), no
  network.

No other deviations from the Design's interfaces, registry wiring, or trade-offs.

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — PASS

Reviewed `src/dander/ingestion/capabilities.py`, `src/dander/ingestion/__init__.py`,
`src/dander/ingestion/README.md`, and `tests/ingestion/test_capabilities_upsert.py` against the
Acceptance Criteria, the Design, `steering/01-security.md`, `steering/02-engineering.md`, and
`steering/languages/python.md`.

**Acceptance criteria — all met.**

1. **AC-1 (Protocol):** `@runtime_checkable class SupportsUpsert(Protocol)` with
   `upsert(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]` is defined in
   `capabilities.py`. The Google-style docstring states the create-or-update contract, that the
   identity is derived from the record's own business-key fields, and that the return is the
   resulting record mapping (which carries the key, so callers wanting only the identity project
   it out) — one unambiguous type serving both AC phrasings.
2. **AC-2 (registration):** `ConnectorOperation.UPSERT = "upsert"` appended to the `StrEnum` (with
   an `Attributes:` entry) and exactly one `CAPABILITY_REGISTRY` entry
   `ConnectorOperation.UPSERT: SupportsUpsert`. **Zero `ConnectorAdapter` edits** — verified by
   reading the class; detection/`supports`/`supported_operations`/`require` work generically over
   the new entry (Open/Closed as designed).
3. **AC-3 (business-key consistency):** verified against the actual code, not just the docstring —
   `Endpoint.primary_key: list[str]` (`src/dander/ingestion/source.py:114`) is the field mapped at
   `src/dander/runtime.py:171` as `business_key=tuple(endpoint.primary_key)` into `WriteTarget`
   for the SCD1 merge. The Protocol docstring names that exact chain, and the test fake resolves
   the key by reading `primary_key` off `self.config.endpoints` rather than accepting a
   caller-supplied identity, so the same key concept is genuinely used end to end.
4. **AC-4 (unsupported path):** `test_detection_negative_for_source_missing_protocol` and
   `test_unsupported_operation_raises_without_leaking_record_values` cover both `supports(...) is
   False` and `require(...)` raising the DANDER-64 `UnsupportedConnectorOperationError`
   (a `ValueError`, explicitly asserted not to be an `AttributeError`).
5. **AC-5 (security):** greped the diff for credential-shaped literals — none. No secret, token,
   key, or connection string anywhere; no new secret keys, so no `.env.example` change is owed.
   The Protocol declares no credentials and adds no credential path; the docstring records the
   invariant that no record/identity/row value may reach an exception or log and that credential
   access routes through the audited `AuthStrategy`. `ConnectorAdapter.require`'s message names
   only the source name and operation value (`capabilities.py:630-632`), and the test asserts the
   message contains no row value or secret-shaped marker. Test fixtures carry no sensitive data
   (`https://fake.example.test`, `auth_strategy="none"`, a synthetic historical name).
6. **AC-6 (`bulk_upsert` follow-on):** recorded in the ticket's Design section with the exact
   three-step recipe and the deferral rationale, and cross-referenced from the module docstring
   (`capabilities.py:66-69`). Correctly documentation-only — no speculative code added.
7. **AC-7 (tests):** six tests, no network. Detection-positive (`supports` + membership in
   `supported_operations`), detection-negative, bare `isinstance` structural check, create branch
   (new key inserts; returned mapping equals the stored record), update branch (existing key
   updates in place; changed field reflected in the return and store size unchanged, proving the
   keying is on `primary_key` and not append-only), and the unsupported raise.
8. **AC-8 (steering):** no violations found.

**Design fidelity.** Matches the approved Design's interfaces, registry wiring, and trade-offs
(no separate identity argument; full-record return; structural detection with its documented
signature-blindness caveat). The single deviation — a new
`tests/ingestion/test_capabilities_upsert.py` module instead of editing the shared
`tests/ingestion/test_capabilities.py` — is declared in Implementation Notes and is correct on the
facts: `test_capabilities.py` is the DANDER-64 mechanism test and every sibling capability ticket
(65/66/67/68/73/74) landed its own `test_capabilities_<name>.py`. Following the established repo
convention over the stale file list is the right call; `test_capabilities.py` was left untouched.
The flagged optional `business_key_for(config, endpoint)` helper was not lifted — acceptable, as
no production code needs it yet (the only resolver is a test fake) and no ticket owns it.

**Tooling — independently re-run, not taken on trust.**
- `uv run ruff check src tests` → all checks passed.
- `uv run ruff format --check` (3 touched files) → already formatted.
- `uv run mypy --strict` (3 touched files) → success, no issues.
- `uv run pytest tests/ingestion/` → 112 passed, no network.
- Full suite: 3 failures in `tests/cli/test_metadata_cli.py` and `tests/cli/test_transform_cli.py`
  — all ANSI/Rich terminal-formatting assertions unrelated to ingestion, pre-existing and
  untouched by this diff. Not attributable to DANDER-75.

**Non-blocking nits (no action required for this ticket):**
- `src/dander/ingestion/README.md:39` is 101 chars, one over the house ~100 guidance. Markdown is
  not Ruff-enforced and two pre-existing docstring lines in `capabilities.py` (369, 447, from
  DANDER-68/74) are the same length, so this is consistent with the file, not a regression.
- `__all__` in `src/dander/ingestion/__init__.py` is not strictly alphabetical
  (`"CursorPagination"` precedes `"ConnectorAdapter"`). This ordering came from DANDER-64's import
  block, not this ticket; `"SupportsUpsert"` is itself correctly placed. Worth a cleanup in a
  future ticket that owns that file.
- The test fake's update branch merges (`{**existing, **record}`) rather than replacing. Identical
  behavior for the full-record contract the Protocol specifies, so it does not weaken the update
  assertion; noted only for whoever mirrors this fake in a `bulk_upsert` follow-on.

**Verdict: PASS.** Status → `done`.
