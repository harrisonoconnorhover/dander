---
id: DANDER-65
title: Add get_single_object connector capability protocol
status: done
component: python
epic: connector-capabilities
depends_on: [DANDER-64]
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Satisfied on the current trunk (`teammate/main`, adopted as local `main` — see
`steering/00-project-overview.md` Decision Log, 2026-08-05): `SupportsGetSingleObject` /
`SourceCapabilities.get_single_object` in `src/dander/ingestion/capabilities.py`. No further
action needed. See `docs/decisions.md`, "2026-08-05 — Optional source capabilities remain
structural and read-only."

## Context

Some pipeline needs — targeted re-fetch of one record after a failure, spot reconciliation, or
resolving a single foreign key — require fetching exactly one record by its business key without
running a full `extract()`. Not every source can do this cheaply or at all, so it is an **optional
capability**, not part of the mandatory `Source` contract in `src/dander/ingestion/source.py`.

This ticket defines the `get_single_object` capability Protocol and registers it with the
`ConnectorAdapter`/`ConnectorOperation` mechanism from DANDER-64, per the composition-over-
inheritance and interface-first rules in `steering/02-engineering.md`.

## Acceptance Criteria

- [ ] A `runtime_checkable` `Protocol` for `get_single_object` is defined in
      `src/dander/ingestion/capabilities.py` (e.g. `SupportsGetSingleObject`) with a single method
      that accepts an endpoint name and a record identity (business-key value(s)) and returns one
      record mapping, or a defined not-found result.
- [ ] The Protocol is registered against `ConnectorOperation.GET_SINGLE_OBJECT` in the DANDER-64
      registry so `ConnectorAdapter.supports(...)` reports it for a source that implements it.
- [ ] A source implementing the Protocol is detected as supporting the operation; one that does
      not is not, and requesting it raises the DANDER-64 unsupported-operation error.
- [ ] Method signature/return typing matches the `Mapping[str, Any]` record shape already yielded
      by `Source.extract()` so downstream layers stay path-agnostic.
- [ ] No secret or row value appears in any error message (`steering/01-security.md`).
- [ ] Unit tests cover detection-positive, detection-negative, and the shape of a returned record
      via a fake source (no network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

DANDER-64 established the mechanism: an optional connector capability is a `runtime_checkable`
`typing.Protocol`, mapped to a `ConnectorOperation` member through a single registry, and detected
by `ConnectorAdapter` via `isinstance()` at construction. This ticket is a pure **extension** of
that mechanism — it adds one Protocol plus one registry entry and touches **no** `ConnectorAdapter`
logic (satisfying DANDER-64's "extend by adding a Protocol + one registry entry" contract). All new
code lives in the already-created `src/dander/ingestion/capabilities.py`.

The capability is "fetch exactly one record by its business key." Two shape decisions matter:

1. **Record identity is a `Mapping[str, str]`** keyed by the endpoint's business-key field name(s),
   not a bare scalar. `Endpoint.primary_key` is a `list[str]`, so composite keys are already a
   first-class notion in this codebase; a mapping (`{"id": "42"}`, or `{"tenant": "...", "id":
   "..."}`) is self-describing, handles composite keys without positional guessing, and cannot be
   confused with the returned record. Key **values** are typed `str` because they travel into a URL
   path or query filter; a source that needs a non-string key coerces at its own edge, exactly as
   `Endpoint.cursor_param` values already do. (Trade-off + alternative flagged below.)

2. **"Not found" is a defined, typed sentinel, never an exception and never `None`.** A missing
   record is a normal, expected outcome of a targeted re-fetch — it must not raise (which would
   tempt callers to embed the identity value in a message, violating `steering/01-security.md`) and
   must not overload `None` (ambiguous against an endpoint that legitimately returns an empty
   mapping). The module defines a single frozen sentinel `RECORD_NOT_FOUND` of a dedicated type
   `RecordNotFound`, and the method returns `Mapping[str, Any] | RecordNotFound`. Callers branch
   with `is RECORD_NOT_FOUND` (identity comparison) or `isinstance(result, RecordNotFound)`, both of
   which mypy narrows cleanly.

The **found** return type is `Mapping[str, Any]` — byte-for-byte the record shape already yielded by
`Source.extract()` — so downstream layers (writer, catalog, reconciliation) stay path-agnostic per
the acceptance criteria and the hybrid-source decision in `steering/00-project-overview.md`.

This ticket does **not** implement the capability on any concrete source (`DltRestSource`,
`WorkdayRaasSource`); it defines the contract and its registration only. Detection and the
unsupported-operation raise are exercised through `ConnectorAdapter` (DANDER-64) with a fake source
in tests.

### Interfaces / classes (all in `src/dander/ingestion/capabilities.py`)

- **`class RecordNotFound`** — a dedicated sentinel type for "no record matched the identity."
  Implemented as `@dataclass(frozen=True)` (matching the frozen-value-object rule in
  `languages/python.md`), with a private/frozen construction so `RECORD_NOT_FOUND` is effectively a
  singleton. Gives callers a nameable, importable, type-narrowable result.
- **`RECORD_NOT_FOUND: Final[RecordNotFound]`** — the module-level singleton instance. The one value
  a capability returns to signal "looked, found nothing."
- **`@runtime_checkable class SupportsGetSingleObject(Protocol)`** — the capability contract:
  ```python
  def get_single_object(
      self, endpoint: str, identity: Mapping[str, str]
  ) -> Mapping[str, Any] | RecordNotFound: ...
  ```
  Google-style docstring carries the full contract: `endpoint` is an `Endpoint.name`; `identity`
  maps each `Endpoint.primary_key` field name to its value; returns one record as `Mapping[str,
  Any]` (same shape as `extract()`), or `RECORD_NOT_FOUND` when no record matches. The docstring
  states the security invariant: **implementations must not put the identity value or any row value
  into an exception message** — "not found" is a return value, not an error, and transport/auth
  errors are named without the key.

### Registry wiring (one entry)

Add to DANDER-64's single capability registry (the `dict[ConnectorOperation, type]` that maps each
operation to its Protocol, e.g. `_CAPABILITY_REGISTRY`):

```python
ConnectorOperation.GET_SINGLE_OBJECT: SupportsGetSingleObject,
```

`ConnectorOperation.GET_SINGLE_OBJECT` already exists (DANDER-64 AC enumerates it among the four
Core capabilities). With this one line, `ConnectorAdapter(source).supports(
ConnectorOperation.GET_SINGLE_OBJECT)` returns `True` for any source whose class satisfies the
Protocol and `False` otherwise, and requesting the operation on a non-supporting source raises
DANDER-64's `UnsupportedConnectorOperationError` (which names source + operation only). No adapter
code changes.

### Files to touch

- `src/dander/ingestion/capabilities.py` — **extend** (created by DANDER-64): add `RecordNotFound`,
  `RECORD_NOT_FOUND`, `SupportsGetSingleObject`, and the one registry entry.
- `src/dander/ingestion/__init__.py` — export `SupportsGetSingleObject`, `RecordNotFound`, and
  `RECORD_NOT_FOUND` (add to `__all__`, keeping alphabetical order) so callers and tests import from
  the package surface.
- `tests/ingestion/test_capabilities_get_single_object.py` — new unit test module (mirrors the
  existing `tests/ingestion/` layout).

### Test seams

Two module-level fake `Source` subclasses, no network (per `steering/02-engineering.md`):
`FakeGetSingleSource` implements `get_single_object` over a small in-memory dict of endpoint →
records; `FakePlainSource` implements only the mandatory `Source.extract`/`discover`. Cases:

- **Detection-positive:** `ConnectorAdapter(FakeGetSingleSource(...)).supports(GET_SINGLE_OBJECT)` is
  `True`; the operation is in `supported_operations`.
- **Detection-negative:** same against `FakePlainSource` is `False`.
- **Returned record shape:** a hit returns a `Mapping[str, Any]` matching an `extract()`-style
  record; assert the mapping's keys/values.
- **Not-found result:** a miss returns `RECORD_NOT_FOUND` (`result is RECORD_NOT_FOUND`), does not
  raise, and no identity value leaks.
- **Unsupported-operation error path:** requesting the operation on `FakePlainSource` through the
  adapter raises `UnsupportedConnectorOperationError`, and `str(exc)` contains the source and
  operation names but **not** any identity/row value (security assertion).

### Trade-offs

- **Sentinel vs. `None` vs. raising for not-found.** Chosen: dedicated `RECORD_NOT_FOUND` sentinel.
  `None` collides with a legitimately empty mapping and reads ambiguously; raising forces callers to
  treat an expected outcome as exceptional and invites leaking the identity into a message
  (`steering/01-security.md`). A typed sentinel is explicit, importable, and mypy-narrowable, at the
  cost of one extra symbol to export.
- **Identity as `Mapping[str, str]` vs. a bare scalar.** Chosen: mapping keyed by business-key field
  name. It is the only shape that handles the composite `Endpoint.primary_key` case without
  positional convention and is self-describing at call sites. A bare `str` would be simpler for the
  common single-key endpoint but silently breaks on composite keys and couples callers to key
  order. **Flag for the Code agent:** the ticket says "business-key value(s)" without pinning the
  shape — if a reviewer prefers `str | Mapping[str, str]` for single-key ergonomics, that is a
  compatible widening, but the mapping-only form is recommended for one obvious contract.
- **Extension-only placement.** Everything lands in `capabilities.py` with a single registry line
  and no `ConnectorAdapter` edit, proving DANDER-64's open-for-extension design and keeping this
  ticket small and independently reviewable.

### Dependency note

Depends on DANDER-64 (`ConnectorOperation`, `ConnectorAdapter`, the registry, and
`UnsupportedConnectorOperationError`). This design assumes DANDER-64's registry is a single
mutable-at-definition mapping keyed by `ConnectorOperation` and that the adapter's raise/accessor is
generic (keyed by operation, not per-capability). If DANDER-64 instead exposes per-capability typed
accessor methods on `ConnectorAdapter`, add a thin `get_single_object(...)` accessor there that
casts to `SupportsGetSingleObject` or raises `UnsupportedConnectorOperationError` — but prefer the
generic path so this ticket stays a Protocol + one registry entry.

## Implementation Notes

Implemented exactly per Design, extending `src/dander/ingestion/capabilities.py` (created by
DANDER-64) with no `ConnectorAdapter` edits:

- `RecordNotFound` — `@dataclass(frozen=True)` sentinel type, and the module-level
  `RECORD_NOT_FOUND: Final[RecordNotFound]` singleton instance.
- `SupportsGetSingleObject` — `@runtime_checkable class ... (Protocol)` with
  `get_single_object(self, endpoint: str, identity: Mapping[str, str]) -> Mapping[str, Any] |
  RecordNotFound`, Google-style docstring stating the identity shape, the `RECORD_NOT_FOUND`
  contract, and the security invariant (no identity/row value in exception messages).
- One registry line: `CAPABILITY_REGISTRY[ConnectorOperation.GET_SINGLE_OBJECT] =
  SupportsGetSingleObject` (registry populated at definition, per DANDER-64's shape). Updated the
  registry's own docstring to note DANDER-65 as the first populated entry.
- `src/dander/ingestion/__init__.py` — exported `RECORD_NOT_FOUND`, `RecordNotFound`,
  `SupportsGetSingleObject` from the package surface; added to `__all__`. (Note: the pre-existing
  `__all__` list in this file is not strictly case-sensitive-ASCII sorted throughout — e.g.
  `CursorPagination` precedes `ConnectorAdapter` — and `RUF022` is not enabled in this repo's ruff
  config, so there's no tool-enforced canonical order. New entries were inserted at their
  case-sensitive-ASCII-alphabetical position, consistent with how the existing `CAPABILITY_REGISTRY`
  constant sorts ahead of same-letter classes.)

Design decision taken as specified: identity is `Mapping[str, str]` (not a bare scalar) — handles
composite `Endpoint.primary_key` without positional guessing. Kept the mapping-only form rather
than widening to `str | Mapping[str, str]`, per the Design's "recommended" default; flagging here
per the ticket's own trade-off note in case PR-review prefers the widened form.

New test module `tests/ingestion/test_capabilities_get_single_object.py`: `FakeGetSingleSource`
(implements `Source` + `get_single_object` over an in-memory endpoint→id→record dict) and
`FakePlainSource` (mandatory `Source` contract only). Covers: detection-positive,
detection-negative, a direct `isinstance` check against the Protocol, hit shape (`Mapping[str,
Any]` equality), miss (`result is RECORD_NOT_FOUND`, no raise), and the
`UnsupportedConnectorOperationError` path asserting the message contains source+operation names
but not the record id/name or any secret-shaped marker string.

No deviations from Design. No new dependencies, no secrets touched (nothing added to
`.env.example`).

**Toolchain (all on touched files + full suite):**
- `ruff check` — all checks passed (repo-wide and on touched files).
- `ruff format --check` — touched files already formatted (repo has one pre-existing unrelated
  reformat need in `src/dander/security/secret_manager.py`, not touched by this ticket).
- `mypy src/dander` — Success, no issues found in 73 source files.
- `pytest` — full suite: 647 passed (includes the 6 new tests in this ticket plus the existing
  DANDER-64 `test_capabilities.py` suite, unaffected).

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — PASS

Reviewed `src/dander/ingestion/capabilities.py`, `src/dander/ingestion/__init__.py`, and
`tests/ingestion/test_capabilities_get_single_object.py` against the acceptance criteria, the
approved Design, and steering (`01-security.md`, `02-engineering.md`, `languages/python.md`).

Acceptance criteria — all met:
1. `@runtime_checkable class SupportsGetSingleObject(Protocol)` is defined in `capabilities.py`
   with a single `get_single_object(self, endpoint: str, identity: Mapping[str, str]) ->
   Mapping[str, Any] | RecordNotFound` method — endpoint name + composite-safe identity mapping,
   record mapping or the defined `RECORD_NOT_FOUND` sentinel.
2. Registered as `CAPABILITY_REGISTRY[ConnectorOperation.GET_SINGLE_OBJECT] =
   SupportsGetSingleObject`; `ConnectorAdapter.supports(...)` reports it. No `ConnectorAdapter`
   edits (pure extension per DANDER-64's contract).
3. Detection-positive (`FakeGetSingleSource`), detection-negative (`FakePlainSource`), and the
   `require(...)` → `UnsupportedConnectorOperationError` path are all exercised through the adapter
   and pass.
4. Found return type is `Mapping[str, Any]`, matching `Source.extract()`'s record shape.
5. No secret/row value in any error message: `UnsupportedConnectorOperationError` names only
   source + operation; the Protocol docstring states the no-identity-in-exceptions invariant; a
   test asserts the message excludes identity/row/secret-marker substrings.
6. Six unit tests cover detection ±, `isinstance` against the Protocol, hit shape, not-found
   sentinel, and the unsupported-operation raise — all in-memory, no network.
7. No steering violations. Nothing added to `.env.example` (no secrets introduced).

Toolchain reproduced independently: `ruff check` clean, `ruff format --check` clean on touched
files, `mypy src/dander` success (73 files), full `pytest` suite 647 passed (incl. the 6 new
tests). Design fidelity confirmed — identity `Mapping[str, str]`, typed frozen sentinel over
`None`/raise, extension-only placement all as designed. The `__all__` ordering note is a
non-issue (RUF022 not enabled; `ruff check` passes). No blocking issues.
