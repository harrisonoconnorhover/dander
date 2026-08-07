---
id: DANDER-67
title: Add count connector capability protocol
status: done
component: python
epic: connector-capabilities
depends_on: [DANDER-64]
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Satisfied on the current trunk (`teammate/main`, adopted as local `main`): `SupportsCount` /
`SourceCapabilities.count` (with `CountResult`/`CountPrecision`) in
`src/dander/ingestion/capabilities.py`. No further action needed. See `docs/decisions.md`,
"2026-08-05 — Optional source capabilities remain structural and read-only."

## Context

Reconciliation and observability want a cheap record count (or estimate) for an endpoint without
pulling the full dataset — e.g. to compare source row count against the count landed in BigQuery,
or to size a run before executing it. Some APIs expose a total/count field or a HEAD-style probe;
others do not. This is an **optional capability**, kept off the mandatory `Source` contract in
`src/dander/ingestion/source.py`.

This ticket defines the `count` capability Protocol and registers it with the DANDER-64
`ConnectorAdapter`/`ConnectorOperation` mechanism, following the composition-over-inheritance rule
in `steering/02-engineering.md`.

## Acceptance Criteria

- [ ] A `runtime_checkable` `Protocol` for `count` (e.g. `SupportsCount`) is defined in
      `src/dander/ingestion/capabilities.py` with a method that accepts an endpoint name (and
      optional `since` cursor mirroring `extract`) and returns an integer count/estimate.
- [ ] The return type distinguishes or documents whether the value is exact or an estimate.
- [ ] The Protocol is registered against `ConnectorOperation.COUNT` in the DANDER-64 registry and
      detected by `ConnectorAdapter`.
- [ ] A source without the capability is reported unsupported and requesting it raises the
      DANDER-64 unsupported-operation error.
- [ ] `count` performs no full extraction in its contract (documented intent; enforced by the
      Protocol shape returning a scalar, not a record stream).
- [ ] No secret or row value appears in error messages (`steering/01-security.md`).
- [ ] Unit tests cover detection-positive, detection-negative, and a returned count via a fake
      source (no network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

`count` is an **optional capability**, discovered through the DANDER-64
`ConnectorAdapter`/`ConnectorOperation` mechanism, and stays off the mandatory `Source` contract in
`src/dander/ingestion/source.py`. This ticket adds three things to the existing
`src/dander/ingestion/capabilities.py` module (created by DANDER-64) and nothing to
`ConnectorAdapter` itself — extension is by "add a Protocol + one registry entry," per DANDER-64's
sixth acceptance criterion:

1. A small value object, `CountResult`, that carries the count **and** whether it is exact or an
   estimate.
2. A `runtime_checkable` `Protocol`, `SupportsCount`, whose single method mirrors `Source.extract`
   (endpoint name + optional `since` cursor) but returns the scalar `CountResult` rather than a
   record stream.
3. One registry entry mapping `ConnectorOperation.COUNT` (the enum member already defined by
   DANDER-64) to `SupportsCount`, so `ConnectorAdapter` detects and reports it with no logic
   change.

The "no full extraction" contract (AC 5) is enforced **structurally**, not just by prose: the
Protocol's return type is a scalar value object, not `Iterator[Mapping[str, Any]]`, so an
implementation that streamed the whole dataset would not fit the signature. The docstring reinforces
the intent (a total/count field, a HEAD-style probe, or a documented estimate — never a full pull).

The exact/estimate distinction (AC 2) is expressed **in the type**, which is stronger than a
documented convention on a bare `int`. `CountResult` pairs a non-negative `count` with a
`CountPrecision` enum. Sources with a trustworthy total (e.g. an API `total_count` field) return
`CountPrecision.EXACT`; sources that can only approximate (statistics endpoints, sampled estimates)
return `CountPrecision.ESTIMATE`. Two classmethod constructors (`CountResult.exact(n)` /
`CountResult.estimate(n)`) keep call sites readable.

Requesting `count` on a source that does not implement `SupportsCount` (AC 4) is handled entirely by
DANDER-64: the adapter computes `supports(ConnectorOperation.COUNT) is False` at construction and
its request/accessor path raises `UnsupportedConnectorOperationError` naming source + operation.
DANDER-67 adds no new error type and no adapter code; its tests exercise that existing path *through*
the newly registered `COUNT` operation.

### Interfaces / classes (all in `src/dander/ingestion/capabilities.py`)

- **`CountPrecision(StrEnum)`** — closed set `{EXACT = "exact", ESTIMATE = "estimate"}`, matching the
  `StrEnum` convention used by `WriteMode` / `IngestionEngine` / `BackoffKind` so it serializes to a
  stable string and rejects unknown values automatically.
- **`CountResult`** — `@dataclass(frozen=True, slots=True)` value object (frozen dataclass per
  `languages/python.md` for internal value objects). Fields: `count: int`, `precision:
  CountPrecision`. `__post_init__` validates `count >= 0` (a negative record count is never valid),
  raising `ValueError` with no payload data. Classmethods `exact(count: int) -> CountResult` and
  `estimate(count: int) -> CountResult` for ergonomic construction.
- **`SupportsCount(Protocol)`** — decorated `@runtime_checkable` (matching
  `core/interfaces.py` style). Single method:

  ```python
  def count(self, endpoint: str, *, since: str | None = None) -> CountResult:
      ...
  ```

  Signature deliberately mirrors `Source.extract(endpoint, *, since=None)` so a source can honor both
  with a consistent shape; `since` lets a count be bounded by the same incremental cursor an
  `extract` would use (e.g. "how many records changed since the last watermark"). The contract lives
  in this docstring: return a cheap total/estimate, perform **no** full extraction, and never place a
  secret or row value in any exception.

- **Registry entry** — extend the DANDER-64 operation→Protocol mapping with
  `ConnectorOperation.COUNT: SupportsCount`. This is the only wiring; `ConnectorAdapter` iterates the
  registry generically.

### Dependency seam on DANDER-64 (flagged)

DANDER-64 owns *how* a caller "requests" an operation and raises the unsupported error. Its
acceptance criteria promise `supported_operations` / `supports(op)` plus a typed
`UnsupportedConnectorOperationError`, but the exact request/accessor method name is DANDER-64's to
finalize. DANDER-67 must use whatever that lands as, and should not add a per-capability accessor to
`ConnectorAdapter` (that would violate DANDER-64's "no adapter edits" rule). The recommended shape,
if DANDER-64 has not already provided it, is a **generic typed accessor** such as
`adapter.require(op, protocol)` returning the source narrowed to `protocol` or raising — used as
`adapter.require(ConnectorOperation.COUNT, SupportsCount).count(endpoint)`. If DANDER-64 instead
exposes only `supports()` + direct source access, the count call is `cast`-guarded behind a
`supports()` check. This is the one coupling point to reconcile against the as-built DANDER-64 API.

### Files to touch / create

- **`src/dander/ingestion/capabilities.py`** (edit, created by DANDER-64) — add `CountPrecision`,
  `CountResult`, `SupportsCount`, and the one registry entry. No other module changes; `extract` /
  `discover` on `Source` are untouched.
- **`tests/ingestion/test_capabilities_count.py`** (new; sits beside the existing
  `tests/ingestion/` suite) — unit tests, no network.

### Test seams

Define an in-file **fake source** subclassing `Source` (implements `discover`/`extract` trivially)
in two flavors: one that also implements `count` returning a fixed `CountResult`, and one that does
not. Cover:

- **Detection-positive** — `ConnectorAdapter(fake_with_count).supports(ConnectorOperation.COUNT)` is
  `True`; `isinstance(fake_with_count, SupportsCount)` is `True`.
- **Detection-negative** — the plain fake source reports `COUNT` unsupported and the DANDER-64
  request/accessor path raises `UnsupportedConnectorOperationError` (assert the message names the
  source and operation and contains no secret/row value).
- **Returned count** — invoking `count("some_endpoint")` on the capable fake returns the expected
  `CountResult`; assert both `count` and `precision`, and cover an `estimate` case and the
  `since`-bounded call.
- **Value-object invariants** — `CountResult.exact` / `.estimate` set the right precision; a negative
  count raises `ValueError`.

No network, no real credentials; fixtures carry no sensitive data (`steering/02-engineering.md`,
`steering/01-security.md`).

### Trade-offs

- **`CountResult` value object vs. bare `int` + docstring.** A bare `int` is simpler but cannot
  *distinguish* exact from estimate (AC 2) — it can only document it, leaving callers guessing at a
  reconciliation gap. The frozen dataclass makes precision non-optional and type-checked; the small
  extra surface is justified because reconciliation logic (the ticket's motivating use case) must
  branch on exact vs. estimate.
- **Precision as an enum vs. a bool `is_estimate`.** `CountPrecision` follows the established
  `StrEnum` house pattern, serializes stably, and leaves room for a future third value (e.g. a
  lower-bound/capped count) without a breaking signature change — cheaper than a bool now.
- **Structural enforcement of "no full extraction."** Returning a scalar rather than an iterator
  makes the contract impossible to satisfy by streaming, which is stronger than a comment; the cost
  is that a source whose only way to count is to page everything simply should not implement the
  capability (and will be reported unsupported) rather than masquerading as a cheap count.
- **No adapter changes.** Keeping all additions in the registry honors DANDER-64's open/closed
  design; the price is the flagged coupling to DANDER-64's not-yet-final request API, called out
  above.

## Implementation Notes

Implemented exactly as designed, no deviations.

- Added to `src/dander/ingestion/capabilities.py`:
  - `CountPrecision(StrEnum)` — `EXACT` / `ESTIMATE`, matching the house `StrEnum` pattern.
  - `CountResult` — `@dataclass(frozen=True, slots=True)` with `count: int`, `precision:
    CountPrecision`; `__post_init__` raises `ValueError` on a negative `count` with no payload
    data in the message; `exact(count)` / `estimate(count)` classmethod constructors.
  - `SupportsCount(Protocol)` (`@runtime_checkable`) — single method
    `count(self, endpoint: str, *, since: str | None = None) -> CountResult`, mirroring
    `Source.extract`'s signature shape. Docstring states the "no full extraction" contract and the
    security constraint on exception messages.
  - Registered `ConnectorOperation.COUNT: SupportsCount` in `CAPABILITY_REGISTRY` (the only edit
    to the registry; `ConnectorAdapter` itself is untouched, per DANDER-64's open/closed design).
  - Extended the module docstring with a DANDER-67 scope note (count surfaces the capability only;
    consuming it for reconciliation/run-sizing is out of scope here).
- Exported `CountPrecision`, `CountResult`, `SupportsCount` from `src/dander/ingestion/__init__.py`
  (`__all__` kept alphabetically sorted, matching existing convention).
- **Dependency-seam resolution (Design's flagged coupling to DANDER-64):** the as-built
  `ConnectorAdapter.require(op)` returns `None` and raises `UnsupportedConnectorOperationError` —
  there is no generic typed `adapter.require(op, protocol)` accessor. Tests therefore follow the
  same pattern already established by `test_capabilities_get_deleted.py`: call
  `adapter.require(ConnectorOperation.COUNT)` as the guard, then call `count()` directly on the
  concrete (test-known) source. No new adapter code was added, consistent with DANDER-64's
  "extension by registry entry only" rule.
- New test file `tests/ingestion/test_capabilities_count.py` — no network, no real credentials,
  fixture data is synthetic. Covers: detection-positive (`supports()` + `isinstance`),
  detection-negative, unsupported-operation error (asserts source/operation named, no row-value or
  secret markers present), returned exact count (with and without `since` bound), returned
  estimate count, and `CountResult` value-object invariants (`exact`/`estimate` constructors set
  the right precision; negative count raises `ValueError`).

**Toolchain (via `uv run`):** `ruff check` clean, `ruff format --check` clean, `mypy` clean (73
source files), full `pytest` suite passes (all green, no failures/skips introduced).

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — PASS

Reviewed implementation in `src/dander/ingestion/capabilities.py`,
`src/dander/ingestion/__init__.py`, and `tests/ingestion/test_capabilities_count.py` against all
acceptance criteria and steering.

- **AC1 (runtime_checkable `SupportsCount` Protocol):** met. `@runtime_checkable`
  `SupportsCount` defines `count(self, endpoint: str, *, since: str | None = None) -> CountResult`,
  mirroring `Source.extract`. Off the mandatory `Source` contract, as required.
- **AC2 (exact/estimate distinction):** met, and expressed in the type via `CountPrecision(StrEnum)`
  + `CountResult`, stronger than a documented convention on a bare int.
- **AC3 (registered against `ConnectorOperation.COUNT`, detected by `ConnectorAdapter`):** met.
  `CAPABILITY_REGISTRY` carries the single entry; the adapter iterates the registry generically
  with no adapter edits, honoring DANDER-64's open/closed rule.
- **AC4 (unsupported reported + raises DANDER-64 error):** met. `FakePlainSource` reports COUNT
  unsupported; `adapter.require(COUNT)` raises `UnsupportedConnectorOperationError`.
- **AC5 (no full extraction):** met structurally — scalar `CountResult` return, not an
  `Iterator`; reinforced in the docstring.
- **AC6 (no secret/row value in error messages):** met. Error names only the source config name
  and operation; `__post_init__` `ValueError` carries only the invariant, no offending value.
  Test asserts absence of row/secret markers.
- **AC7 (unit tests, no network):** met. Nine tests cover detection-positive (`supports` +
  `isinstance`), detection-negative, unsupported-operation error, exact count with/without `since`,
  estimate, and value-object invariants (constructors + negative-count `ValueError`). Fake sources
  only, no network, synthetic fixtures.
- **AC8 (no steering violations):** met.

Toolchain verified locally via `uv run`: `ruff check` clean, `ruff format --check` clean, `mypy`
clean on the changed files, and the count + sibling capability suites pass (26 tests green). No
hardcoded secrets, no PII in fixtures/logs; `.env.example` needs no change (no new secrets).
Implementation matches the approved Design; the flagged DANDER-64 coupling seam is resolved
consistently with the as-built `ConnectorAdapter.require(op)` and the sibling capability tickets.

No blocking issues. Status set to `done`.
