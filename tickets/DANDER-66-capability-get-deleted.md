---
id: DANDER-66
title: Add get_deleted connector capability protocol
status: done
component: python
epic: connector-capabilities
depends_on: [DANDER-64]
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Local `main` was reset onto `teammate/main` (Harrison's fork) as the trunk — see
`steering/00-project-overview.md` Decision Log, 2026-08-05 entry. This is a real, still-open gap:
the fork's capability contract (`src/dander/ingestion/capabilities.py`, `SourceCapabilities`)
ships `get_single_object`/`count`/`test_connection` only. `docs/decisions.md`, "2026-08-05 —
Optional source capabilities remain structural and read-only" explicitly defers this one: "Deleted-
record feeds ... remain absent until their cursor, retry, authorization, and destination semantics
are approved separately." Treat that as a **prerequisite design gate** — resolve and record the
cursor/retry/destination approach (likely a Decision Log entry) before implementing against the
Design below, which still describes the superseded `ConnectorAdapter` shape from the pre-reconcile
branch and needs a fresh pass against the current `SourceCapabilities`/`_CAPABILITY_PROTOCOLS`
pattern.

## Context

The existing SCD1 write pattern (`WriteMode.SCD1` in `src/dander/writer/base.py`) merges on the
business key and overwrites in place — it has no way to learn that a record was **hard-deleted** at
the source, so deleted rows silently persist in BigQuery. Correct delete propagation requires a
source-side tombstone/deleted-records feed. Many APIs expose one (e.g. a `deleted_after` endpoint
or a soft-delete flag); many do not. This makes it an **optional capability** rather than part of
the mandatory `Source` contract.

This ticket defines the `get_deleted` capability Protocol and registers it with the DANDER-64
`ConnectorAdapter`/`ConnectorOperation` mechanism. It only surfaces the deleted-record stream as a
typed capability; actually applying deletions to a BigQuery target (a delete/merge-tombstone write
pattern) is deferred and would build on this plus the writer module — call that out in the design,
do not implement it here.

## Acceptance Criteria

- [ ] A `runtime_checkable` `Protocol` for `get_deleted` (e.g. `SupportsGetDeleted`) is defined in
      `src/dander/ingestion/capabilities.py` with a method that yields the identities (business
      keys) of records deleted at the source, optionally bounded by a `since` cursor mirroring
      `Source.extract(..., since=...)`.
- [ ] The Protocol is registered against `ConnectorOperation.GET_DELETED` in the DANDER-64 registry
      and detected by `ConnectorAdapter`.
- [ ] The returned stream carries enough to identify a row for deletion (business key value(s)) and
      is typed as an iterator of mappings consistent with `Source.extract()`.
- [ ] A source without the capability is reported unsupported and requesting it raises the
      DANDER-64 unsupported-operation error.
- [ ] The ticket Context/design records that consuming this feed to propagate hard deletes into a
      BigQuery target is out of scope here (deferred write-pattern work).
- [ ] No secret or row value appears in error messages (`steering/01-security.md`).
- [ ] Unit tests cover detection-positive, detection-negative, and iteration of a fake deleted
      feed with a `since` bound (no network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

This ticket is a **Layer 1 capability** that plugs into the DANDER-64 mechanism
(`ConnectorAdapter` + `ConnectorOperation` + the Protocol registry in
`src/dander/ingestion/capabilities.py`). Per that ticket's scope guard, DANDER-64 introduces the
mechanism and the `ConnectorOperation` value set (including `GET_DELETED`) but deliberately does
**not** define the concrete capability Protocols. DANDER-66 supplies exactly one such Protocol —
`SupportsGetDeleted` — plus its single registry entry, without editing `ConnectorAdapter` logic.
That is the extension seam DANDER-64 promises ("DANDER-65..68 extend it by adding a Protocol + one
registry entry"). All work here therefore lives inside the already-created `capabilities.py`
module and its test module; no change to `Source`, the concrete sources, or the writer.

The capability models a **source-side deleted-record feed**: a stream that reports which business
keys were hard-deleted at the origin so a downstream write pattern can eventually tombstone them in
BigQuery. It is optional because many APIs expose such a feed (a `deleted_after` endpoint, a
soft-delete flag, a change-data stream) and many do not — baking it into the mandatory `Source`
contract would force every concrete source to stub a method it cannot honor, exactly the failure
mode DANDER-64 exists to prevent.

The method signature deliberately **mirrors `Source.extract()`**: it is keyed by `endpoint`
(deletions are per-entity, just like extraction) and takes an optional `since` string cursor with
the same meaning and type as `extract(..., since=...)`. Because deletions are tracked against the
same incremental cursor semantics as inserts/updates, reusing the `str | None` cursor keeps the
two streams symmetric and lets a future writer reconcile them off one watermark. Each yielded item
is a `Mapping[str, Any]` — the **same record type `extract()` yields** — but is expected to carry
only the business-key field(s) (e.g. `{"id": "42"}` for a Greenhouse endpoint whose
`primary_key` is `[id]`). Returning a mapping rather than a bare scalar id supports composite
business keys and keeps the type identical to the extract stream so downstream code has one record
shape to handle.

### Interfaces / classes

Added to `src/dander/ingestion/capabilities.py` (the module DANDER-64 creates):

- **`SupportsGetDeleted`** — a `@runtime_checkable` `typing.Protocol` (structural, matching how
  DANDER-64's other capability Protocols are declared so `ConnectorAdapter`'s `isinstance()`
  detection works). One method:

  ```python
  def get_deleted(
      self, endpoint: str, *, since: str | None = None
  ) -> Iterator[Mapping[str, Any]]: ...
  ```

  Contract (in the Protocol docstring): yields one mapping per record deleted at the source for
  `endpoint`, each containing at least that endpoint's business-key field(s) and their value(s),
  consistent with the records `Source.extract()` yields. When `since` is provided it bounds the
  feed to deletions at/after that cursor, mirroring `Source.extract(..., since=...)`; when `None`
  the implementation returns the full available deleted set. Implementations must not place secret
  material or non-key row values in the stream, and must raise with endpoint-name context only
  (no secret/row-value leakage) on failure, per `steering/01-security.md`.

- **Registry entry** — one line added to the DANDER-64 single-source-of-truth mapping
  (`ConnectorOperation.GET_DELETED -> SupportsGetDeleted`). This is the *only* wiring change;
  `ConnectorAdapter` reads the registry generically, so detection, `supports(...)`,
  `supported_operations`, and the `UnsupportedConnectorOperationError` path all light up for
  `GET_DELETED` with no further code.

No new class hierarchy is introduced — this is composition/structural typing per
`steering/02-engineering.md` (a source *opts in* to the capability by structurally implementing
`get_deleted`; nothing forces it to).

### Files to touch / create

- `src/dander/ingestion/capabilities.py` — **edit** (created by DANDER-64): add the
  `SupportsGetDeleted` Protocol and its registry entry. Import `Iterator`/`Mapping` from
  `collections.abc` under `TYPE_CHECKING` (matching `source.py`).
- `tests/ingestion/test_capabilities_get_deleted.py` — **create**: unit tests for this capability
  (kept separate from DANDER-64's adapter tests so each ticket owns its own test module). Uses
  small in-file fake sources; no network, per `steering/02-engineering.md`.

### Test seams

All tests use lightweight fakes constructed in-file; nothing hits the network.

- **Detection-positive**: a fake `Source` subclass that also implements `get_deleted` is wrapped by
  `ConnectorAdapter`; assert `adapter.supports(ConnectorOperation.GET_DELETED)` is `True` and that
  `GET_DELETED` is in `supported_operations`. Also assert the fake is an `isinstance` of
  `SupportsGetDeleted` directly (Protocol is runtime-checkable).
- **Detection-negative**: a plain fake `Source` implementing only `extract`/`discover` reports
  `GET_DELETED` unsupported, and requesting it raises `UnsupportedConnectorOperationError`
  naming the source and operation (assert the exception message contains neither secret nor row
  values — it references only class/operation names).
- **Iteration with a `since` bound**: a fake deleted feed yields business-key mappings (e.g.
  `[{"id": "1"}, {"id": "2"}]`); assert that calling `get_deleted(endpoint, since=<cursor>)`
  passes the cursor through and yields the expected key mappings, and that `since=None` yields the
  full set. This exercises the mapping-of-business-keys contract and the extract-mirrored `since`.

### Trade-offs

- **Mapping vs. scalar id return.** Yielding `Mapping[str, Any]` (not `str`/`int`) costs a little
  verbosity for single-key endpoints but is the only shape that handles composite `primary_key`s
  and keeps the stream type identical to `extract()`, so one downstream consumer type covers both.
- **`str | None` cursor vs. a richer cursor type.** `source.py`'s `extract` still uses the legacy
  narrow `str` cursor (the graph-level `CursorStrategy` is a separate concern); mirroring it keeps
  symmetry now. If/when `extract`'s cursor type is upgraded, this method upgrades with it in
  lockstep — deliberately coupled.
- **Structural Protocol vs. ABC mixin.** A `runtime_checkable` Protocol lets any existing source
  (dlt-backed or enterprise) opt in without changing its base class, honoring composition over
  inheritance and DANDER-64's detection-by-`isinstance` design; the minor cost is that
  `runtime_checkable` only checks method *presence*, not signature — acceptable because the
  registry + Protocol are the contract and CI (mypy strict) checks the signature at the call site.
- **Per-endpoint keying.** Keying by `endpoint` (rather than a source-wide deleted feed) matches
  `extract` and the reality that primary keys and deleted-feed availability are per-entity. A
  source whose API only offers a global deleted stream can still fan it out per endpoint internally.

### Out of scope (deferred — write-pattern work)

Per Acceptance Criterion 5 and the ticket Context: **consuming this feed to propagate hard deletes
into a BigQuery target is explicitly not built here.** Today's `WriteMode.SCD1`
(`src/dander/writer/base.py`) MERGEs on the business key and overwrites in place; it has no
tombstone/delete path. Applying the identities this capability yields — via a delete/merge-tombstone
write pattern (e.g. an SCD1 variant that soft-deletes or a hard `DELETE ... WHERE key IN (...)`) —
is future work that builds on this Protocol **plus** the writer module and would be its own ticket.
DANDER-66 only surfaces the deleted-record stream as a typed, detectable capability.

### Notes for the Code agent

- This ticket **depends on DANDER-64**; it assumes `capabilities.py`, `ConnectorOperation`
  (with `GET_DELETED`), the `ConnectorAdapter`, the registry mapping, and
  `UnsupportedConnectorOperationError` already exist. If DANDER-64's registry symbol names differ
  from those referenced above, adopt DANDER-64's actual names — do not re-invent the mechanism.
- Google-style docstring on the Protocol carrying the full contract (per `steering/languages/
  python.md`); the docstring is the contract, implementations note only deviations.

## Implementation Notes

**2026-08-05 update:** the note below and the Review Log entry beneath it describe the
pre-reconciliation `ConnectorAdapter` implementation from `backup/local-main-pre-reconcile`, which
is no longer on this trunk (see the ticket's Reconciliation note above). Kept for history, not
current. The actual current implementation, against `teammate/main`'s `SourceCapabilities`:

- `src/dander/ingestion/capabilities.py`: added `SupportsGetDeleted` (`@runtime_checkable`
  `Protocol`, `get_deleted(self, endpoint, *, since=None) -> Iterator[Mapping[str, Any]]`,
  mirroring `Source.extract()`), registered it in `_CAPABILITY_PROTOCOLS`, and added
  `SourceCapabilities.get_deleted()` — `require()` guard then a direct pass-through delegate (no
  result-shape validation, since the return is a lazily-consumed iterator rather than an
  eagerly-checkable value, matching how `extract()`/`discover()` already pass through).
- Cursor/retry/authorization/destination semantics recorded in `docs/decisions.md`, "2026-08-05 —
  Write-back and deleted-record-feed semantics."
- `src/dander/ingestion/__init__.py`: exported `SupportsGetDeleted`.
- `src/dander/ingestion/README.md`: added a "Optional capability discovery" section covering this
  and the write-back capabilities together.
- `tests/ingestion/test_capabilities.py`: extended the shared `_CapableSource` fixture with
  `get_deleted`, and the facade test with iteration + cursor-passthrough assertions; extended the
  full-operation-set assertion.
- Verified: `ruff check` clean, `ruff format --check` clean, `mypy src/dander/ingestion` clean,
  `pytest tests/ingestion tests/pipeline tests/cli/test_connector_cli.py` green. Done directly in
  this reconciliation session, not through the Design→Code→PR-Review agent pipeline — no PASS
  entry added to Review Log for this pass.

---

Original (superseded) note below:

Implemented exactly as designed — no deviations.

- `src/dander/ingestion/capabilities.py`: added `SupportsGetDeleted`, a `@runtime_checkable`
  `Protocol` with one method, `get_deleted(self, endpoint: str, *, since: str | None = None) ->
  Iterator[Mapping[str, Any]]`, mirroring `Source.extract()`'s keying and cursor semantics. Its
  Google-style docstring carries the full contract (business-key-only mappings, `since` bound
  semantics, no-secret/no-row-value error messages per `steering/01-security.md`). Registered it
  as the single new entry `ConnectorOperation.GET_DELETED: SupportsGetDeleted` in
  `CAPABILITY_REGISTRY` — the only wiring change; `ConnectorAdapter` (DANDER-64) required zero
  edits. Also updated the module docstring to note DANDER-66's addition and its scope guard
  (surfacing the feed only — consuming it to propagate hard deletes into BigQuery is deferred
  write-pattern work, per the ticket Context and Acceptance Criterion 5).
- `src/dander/ingestion/__init__.py`: exported `SupportsGetDeleted` alongside the existing
  capability exports.
- `tests/ingestion/test_capabilities_get_deleted.py` (new): fakes-only, no network. Covers
  detection-positive (`ConnectorAdapter.supports`/`supported_operations` and direct
  `isinstance(..., SupportsGetDeleted)`), detection-negative (a plain `Source` reports
  unsupported and `adapter.require(...)` raises `UnsupportedConnectorOperationError` naming only
  `fake_plain`/`get_deleted`, asserted to exclude row values `"1"`/`"2"` and
  secret-shaped markers), and iteration of a fake deleted feed both with a `since` cursor (filters
  to the expected subset) and with `since=None` (yields the full deleted set), confirming the
  cursor passes through and the yielded shape is `{"id": ...}` business-key mappings.

Tooling run from repo root via `uv run`: `ruff check .` — all checks passed; `ruff format --check`
on touched files — already formatted; `mypy src` — success, no issues in 73 source files;
`pytest -q` — full suite (653 tests) passed, including the 6 new tests.

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — PASS

Reviewed `SupportsGetDeleted` in `src/dander/ingestion/capabilities.py`, its `CAPABILITY_REGISTRY`
entry, the `__init__.py` export, the ingestion README addition, and
`tests/ingestion/test_capabilities_get_deleted.py`. All eight acceptance criteria are met:

1. `SupportsGetDeleted` is a `@runtime_checkable` `Protocol` (capabilities.py:125-166) with
   `get_deleted(self, endpoint: str, *, since: str | None = None) -> Iterator[Mapping[str, Any]]`,
   mirroring `Source.extract()`'s per-endpoint keying and `str | None` cursor.
2. Registered as `ConnectorOperation.GET_DELETED: SupportsGetDeleted` in `CAPABILITY_REGISTRY`
   (capabilities.py:171); `ConnectorAdapter` detects it generically — zero adapter edits, matching
   the DANDER-64 extension seam.
3. Yielded stream is `Mapping[str, Any]` carrying business-key field(s), type-identical to
   `extract()`.
4. Detection-negative and `require()` unsupported paths verified — `UnsupportedConnectorOperationError`
   raised naming source + operation only (test_detection_negative / test_unsupported…).
5. Deferred write-pattern scope recorded in the module docstring (lines 30-33), the Protocol
   docstring (lines 132-135), and the ticket Context/Design.
6. Error message (capabilities.py:250-252) uses only `config.name` and `op.value`; the negative
   test asserts absence of row values ("1"/"2") and secret-shaped markers.
7. Tests cover detection-positive, `isinstance`, detection-negative, `since`-bounded iteration, and
   `since=None` full-set iteration — fakes only, no network.
8. No steering violations: no hardcoded secrets, no PII/secrets in logs or fixtures, Google-style
   docstring carries the full contract, composition/structural-typing per engineering steering.

Tooling verified from repo root: `ruff check` + `ruff format --check` clean on touched files;
`mypy src` — no issues in 73 files; the 6 new tests pass. Implementation matches the approved
Design with no unjustified deviation.
