---
id: DANDER-64
title: Add ConnectorAdapter capability registry and ConnectorOperation enum
status: done
component: python
epic: connector-capabilities
depends_on: []
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Local `main` was reset onto `teammate/main` (Harrison's fork) as the trunk — see
`steering/00-project-overview.md` Decision Log, 2026-08-05 entry. The fork independently built the
same seam this ticket specifies, under a different name: `SourceCapabilities` (not
`ConnectorAdapter`) in the current `src/dander/ingestion/capabilities.py`, with a private
`_CAPABILITY_PROTOCOLS` registry dict rather than an injectable `CAPABILITY_REGISTRY`. Concept is
satisfied; class/method names below are historical and no longer match the tree. No further action
needed for this ticket. See `docs/decisions.md`, "2026-08-05 — Optional source capabilities remain
structural and read-only."

## Context

The `Source` interface in `src/dander/ingestion/source.py` mandates exactly one operation:
`extract()` (plus `discover()`). Real SaaS APIs support more than a full extract — single-record
fetch, deleted-record feeds, counts, connectivity probes — but not uniformly. Baking those into
`Source` would force every concrete source (`DltRestSource`, `WorkdayRaasSource`,
`EnterpriseSource` subclasses) to stub methods it cannot honor, and callers would discover an
unsupported operation only as a runtime `AttributeError`.

This ticket adds the **Layer 1 foundation**: a set of small `typing.Protocol` capability contracts
and a `ConnectorAdapter` that wraps a `Source`, detects which capabilities its concrete class
actually implements, and exposes them as a typed, checkable set. This is composition over
inheritance and interface-first design per `steering/02-engineering.md` — the mandatory
`Source.extract()` contract is untouched, and optional capabilities are mixed in only where the
underlying API supports them. It is the base every other Layer 1 capability ticket (DANDER-65..68)
plugs into via `depends_on`.

Scope guard: this ticket introduces the mechanism and the `ConnectorOperation` value set, but does
**not** define the concrete capability Protocols themselves (each is its own ticket). Write-back
operations (`create`/`update`/`upsert`/`bulk_upsert`/`delete`) are now in scope as **optional,
opt-in** connector capabilities per the Decision Log entry 2026-08-04 ("Write-back is now an
optional, opt-in connector capability, not a hard non-goal") in `steering/00-project-overview.md`;
they are discovered through this same `ConnectorAdapter` mechanism and are specified in their own
tickets (DANDER-73..76). The core read → land-in-BigQuery path stays mandatory and unchanged.

## Acceptance Criteria

- [ ] A new module `src/dander/ingestion/capabilities.py` exists.
- [ ] A `ConnectorOperation` `StrEnum` (matching the `StrEnum` convention used by `WriteMode`,
      `IngestionEngine`, `BackoffKind`) enumerates the supported optional operations. It includes
      at minimum the four Core capabilities (`GET_SINGLE_OBJECT`, `GET_DELETED`, `COUNT`,
      `TEST_CONNECTION`); mandatory `extract` is not an optional operation and need not appear.
- [ ] A `ConnectorAdapter` class takes a `Source` instance at construction and, using
      `isinstance()` against `runtime_checkable` capability Protocols, computes the set of
      supported `ConnectorOperation`s **once at construction time**.
- [ ] `ConnectorAdapter` exposes the supported set (e.g. a `supported_operations` property /
      `supports(op)` method) so config validation can check availability before execution.
- [ ] Requesting an unsupported operation raises a clear, typed error (e.g.
      `UnsupportedConnectorOperationError`) naming the source and the operation — never an
      `AttributeError`, and never leaking a secret or row value (`steering/01-security.md`).
- [ ] The registry mapping each `ConnectorOperation` to its capability Protocol is defined in one
      place so DANDER-65..68 extend it by adding a Protocol + one registry entry, without editing
      `ConnectorAdapter` logic.
- [ ] `ConnectorAdapter` wrapping a plain `Source` that implements no optional capability reports
      an empty supported set and still exposes the underlying `extract`/`discover`.
- [ ] Unit tests cover: detection of a source with zero capabilities, detection with a mixed
      subset, and the unsupported-operation error path (see `steering/02-engineering.md`; no
      network).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

This is the Layer 1 seam that every other capability ticket (DANDER-65..68 read; DANDER-73..76
write-back) plugs into. It is deliberately **composition over inheritance**: `Source` and its
mandatory `extract()`/`discover()` contract are untouched, and each optional operation is a small
`typing.Protocol` that a concrete source *may* satisfy structurally. `ConnectorAdapter` wraps a
`Source`, asks a **capability registry** which operations that concrete instance actually
implements (via `isinstance` against `runtime_checkable` Protocols), freezes the answer once at
construction, and exposes it as a typed, checkable set. Callers (config validation, the executor)
ask `adapter.supports(op)` *before* execution instead of discovering an unsupported operation as a
runtime `AttributeError`.

The design's load-bearing decision is a **module-level registry mapping** — a single
`dict[ConnectorOperation, type]` where each value is the `runtime_checkable` Protocol that defines
that operation's method(s). `ConnectorAdapter` contains *no per-capability branching*: it iterates
the registry generically. That is what lets DANDER-65..68 extend the system by adding, in
`capabilities.py`, (1) one `ConnectorOperation` enum member, (2) one `Protocol`, and (3) one entry
in the registry dict — with zero edits to `ConnectorAdapter` logic (Open/Closed).

Per the scope guard, this ticket ships the **mechanism plus the `ConnectorOperation` value set**,
not the concrete Core capability Protocols. Concretely: `ConnectorOperation` carries the four Core
members now (they are the stable value set callers/config will reference), but the registry ships
**empty** — its entries arrive with each capability's Protocol in DANDER-65..68 / 73..76. An empty
registry means a plain `Source` correctly reports an empty supported set, satisfying that acceptance
criterion directly. To make the registry testable and injectable without global mutation,
`ConnectorAdapter` takes the registry as an injected dependency defaulting to the module-level
`CAPABILITY_REGISTRY` (per `languages/python.md`: "dependency-inject clients so tests can mock
them"). Tests supply a registry containing fake Protocols to prove mixed-subset detection without
touching global state.

### Interfaces / classes (all in `src/dander/ingestion/capabilities.py`)

- **`ConnectorOperation(StrEnum)`** — the closed value set of *optional* operations, matching the
  `StrEnum` convention of `WriteMode`, `IngestionEngine`, `BackoffKind`, `PaginationKind`. Members
  (at minimum, this ticket): `GET_SINGLE_OBJECT = "get_single_object"`, `GET_DELETED =
  "get_deleted"`, `COUNT = "count"`, `TEST_CONNECTION = "test_connection"`. Mandatory `extract` is
  **not** a member (it is not optional). DANDER-73..76 append their write-back members
  (`create`/`update`/`upsert`/`bulk_upsert`/`delete`) here following the same add-member pattern.

- **`CAPABILITY_REGISTRY: dict[ConnectorOperation, type]`** — the single canonical mapping from an
  operation to the `runtime_checkable` Protocol whose method(s) implement it. **Ships empty** in
  this ticket; each later capability ticket adds exactly one entry. Typed as `type` (a class
  object) because `isinstance` needs the class; the invariant "every registered value MUST be a
  `runtime_checkable` Protocol" is stated in the module/registry docstring (it cannot be expressed
  in the type system). Iteration order is deterministic (dict insertion order) so `supported_
  operations` is stable.

- **`ConnectorAdapter`** — wraps one `Source`.
  - `__init__(self, source: Source, *, registry: Mapping[ConnectorOperation, type] = CAPABILITY_REGISTRY) -> None`
    — stores the source, then computes `self._supported: frozenset[ConnectorOperation]` **once**
    by iterating `registry` and keeping each `op` where `isinstance(source, protocol)` is true.
  - `source` (property) → the wrapped `Source` (escape hatch for callers needing the concrete).
  - `supported_operations` (property) → `frozenset[ConnectorOperation]` (immutable; computed once).
  - `supports(self, op: ConnectorOperation) -> bool`.
  - `require(self, op: ConnectorOperation) -> None` — raises `UnsupportedConnectorOperationError`
    if `op` is unsupported; returns `None` otherwise. This is the guard seam DANDER-65..68 call at
    the top of each capability accessor before delegating to the source method.
  - `discover(self) -> Mapping[str, Any]` and
    `extract(self, endpoint: str, *, since: str | None = None) -> Iterator[Mapping[str, Any]]` —
    thin pass-throughs to the wrapped source, so the adapter is a drop-in over `Source` and the
    mandatory path stays reachable through it (AC: still exposes `extract`/`discover`).

- **`UnsupportedConnectorOperationError(ValueError)`** — subclasses `ValueError` to match the house
  convention (`ConnectorConfigError`, `EndpointNotFoundError`, `EnterpriseSourceError` all do).
  Message names the source (`source.config.name`) and the operation value only — never a secret,
  auth ref, or row value (`steering/01-security.md`). e.g.
  `"source 'greenhouse' does not support operation 'get_deleted'"`.

### Files to touch / create

- **CREATE `src/dander/ingestion/capabilities.py`** — the enum, registry, adapter, and error above,
  with a module docstring stating its responsibility and the registry invariant. `from __future__
  import annotations`; Google-style docstrings on all public symbols.
- **EDIT `src/dander/ingestion/__init__.py`** — import and add to `__all__`:
  `ConnectorOperation`, `ConnectorAdapter`, `UnsupportedConnectorOperationError`,
  `CAPABILITY_REGISTRY` (keep `__all__` alphabetized as it currently is).
- **CREATE `tests/ingestion/test_capabilities.py`** — see test seams.
- **EDIT `src/dander/ingestion/README.md`** (small) — one paragraph on the capability adapter as the
  optional-operation seam, to keep the package README true to code per `languages/python.md`.

### Test seams (no network, per `steering/02-engineering.md`)

- **Zero capabilities:** a minimal fake `Source` subclass implementing only `discover`/`extract`;
  `ConnectorAdapter(fake).supported_operations == frozenset()`, and `extract`/`discover` delegate
  (assert the pass-through returns the fake's records/schema).
- **Mixed subset:** define two local `@runtime_checkable` Protocols (e.g. a `_Counts` with
  `count(...)` and a `_Probes` with `test_connection(...)`), a fake source implementing only one,
  and an injected test registry `{COUNT: _Counts, TEST_CONNECTION: _Probes}`; assert
  `supported_operations == {COUNT}` and `supports(COUNT) is True`, `supports(TEST_CONNECTION) is
  False`.
- **Unsupported-operation error path:** `adapter.require(TEST_CONNECTION)` raises
  `UnsupportedConnectorOperationError` (a `ValueError`, **not** `AttributeError`); assert the source
  name and operation appear in the message and no secret-like text does.
- **Once-at-construction:** mutating the injected registry after construction does not change an
  existing adapter's `supported_operations` (proves the set is frozen at build time).

### Trade-offs

- **Structural (`isinstance`/Protocol) vs. explicit declaration:** `runtime_checkable` Protocols
  only verify *method presence*, not signatures — a source could match structurally with a wrong
  signature. Accepted: it keeps sources free of a registration boilerplate step and matches the
  interface-first spirit; signature correctness is covered by each concrete source's own tests. The
  registry indirection (vs. hard-coding `isinstance` checks in the adapter) is what buys the
  Open/Closed extension the ticket requires.
- **Injected registry vs. global-only:** the injectable default gives a clean, mutation-free test
  seam for mixed-subset detection while production callers use the single `CAPABILITY_REGISTRY`.
- **Empty registry now:** shipping the registry empty (Protocols land with their tickets) keeps
  this ticket strictly to "mechanism + value set" per the scope guard, and is exactly why a plain
  `Source` reports an empty set.

### Notes / flags

- The Context references Decision Log entry **2026-08-04** ("write-back is an optional, opt-in
  connector capability"); the copy of `steering/00-project-overview.md` in this session shows the
  latest entry as 2026-08-02. Not blocking for this ticket (it ships no write-back Protocols), but
  the Code agent should confirm that entry exists / is added before DANDER-73..76 build on it.

## Implementation Notes

Built exactly per Design, no deviations:

- **`src/dander/ingestion/capabilities.py`** (new) — `ConnectorOperation` `StrEnum` with the four
  Core members (`get_single_object`, `get_deleted`, `count`, `test_connection`); module-level
  `CAPABILITY_REGISTRY: dict[ConnectorOperation, type]`, shipped empty per the scope guard, with
  the "every value must be a `@runtime_checkable` Protocol" invariant documented on the object
  since it can't be expressed in the type system; `UnsupportedConnectorOperationError(ValueError)`
  matching the house convention (`ConnectorConfigError`, `EndpointNotFoundError`,
  `EnterpriseSourceError`), message names only the source name and operation value; `ConnectorAdapter`
  taking `source: Source` and an injected `registry` (defaulting to `CAPABILITY_REGISTRY`),
  computing `frozenset[ConnectorOperation]` once at `__init__` via generic `isinstance` iteration
  (no per-capability branching), plus `source`/`supported_operations` properties, `supports()`,
  `require()`, and pass-through `discover()`/`extract()`.
- **`src/dander/ingestion/__init__.py`** — imported and exported `CAPABILITY_REGISTRY`,
  `ConnectorAdapter`, `ConnectorOperation`, `UnsupportedConnectorOperationError`. Inserted into
  `__all__` in the same relative alphabetical style as the existing list (which is not strictly
  ASCII-sorted itself, e.g. pre-existing `CursorPagination` precedes `ConnectorConfigError` —
  matched that local convention rather than re-sorting the whole list).
- **`src/dander/ingestion/README.md`** — added an "Optional capability discovery" paragraph
  describing `ConnectorAdapter` as the seam and how DANDER-65..68/73..76 extend it.
- **`tests/ingestion/test_capabilities.py`** (new) — 5 tests, no network: zero-capabilities
  detection + pass-through delegation, mixed-subset detection with an injected fake registry
  (`_Counts`/`_Probes` local `runtime_checkable` Protocols), the `require()` unsupported-operation
  error path (asserts `ValueError` subclass, not `AttributeError`; asserts source name + operation
  value appear and common secret-like substrings do not), registry-mutation-after-construction
  proving the frozen-at-construction invariant, and a default-registry sanity check (currently
  empty, so a plain fake source reports an empty set through the real `CAPABILITY_REGISTRY`).

Toolchain: `ruff check` clean, `ruff format --check` clean on all touched files (one unrelated
pre-existing formatting diff in `src/dander/security/secret_manager.py`, untouched by this change,
confirmed via `git status` to predate this ticket), `mypy` clean (`src/dander` — 73 files, and the
new test file), full `pytest` suite green (all packages, no regressions).

Confirmed the Design's flagged item is already resolved: the Decision Log entry dated 2026-08-04
("Write-back is now an optional, opt-in connector capability, not a hard non-goal") is present in
`steering/00-project-overview.md`, so DANDER-73..76 have their grounding in place.

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — PASS

Reviewed the implementation against all acceptance criteria, the approved Design, and steering
(`01-security.md`, `02-engineering.md`, `languages/python.md`). Verdict: **PASS**.

Acceptance criteria — all met:
1. `src/dander/ingestion/capabilities.py` exists.
2. `ConnectorOperation(StrEnum)` carries the four Core members (`GET_SINGLE_OBJECT`, `GET_DELETED`,
   `COUNT`, `TEST_CONNECTION`); `extract`/`discover` correctly absent. Matches the `StrEnum`
   convention of `WriteMode`/`IngestionEngine`/`BackoffKind`.
3. `ConnectorAdapter.__init__` computes `self._supported` once via a generic
   `isinstance(source, protocol)` iteration over the registry — no per-capability branching.
4. Support exposed via `supported_operations` (frozenset property) and `supports(op)`.
5. `require(op)` raises `UnsupportedConnectorOperationError(ValueError)` naming
   `source.config.name` and `op.value` only — never `AttributeError`, no secret/PII leak.
6. Single canonical `CAPABILITY_REGISTRY` mapping; DANDER-65..68/73..76 extend via one enum member
   + one Protocol + one entry with zero adapter edits (Open/Closed).
7. Plain `Source` with the default (empty) registry reports an empty set and still exposes
   `extract`/`discover` pass-throughs.
8. Tests (`tests/ingestion/test_capabilities.py`, 5 tests, no network): zero-capabilities +
   delegation, mixed-subset via injected registry, unsupported-op error path (asserts `ValueError`
   not `AttributeError`, source name + op present, secret markers absent), frozen-at-construction,
   and empty default-registry sanity check.

Security: no hardcoded secrets in the diff; test fixture uses `auth_strategy="none"` and a
non-secret example URL; error message is source-name + operation only. No `.env.example` change
needed (no new secrets). Clean.

Toolchain verified locally: `ruff check` clean, `ruff format --check` clean, `mypy` clean on the
two new files, and `pytest tests/ingestion/` fully green (66 tests, no regressions). Design's
flagged Decision Log entry (2026-08-04, write-back opt-in) confirmed present in
`steering/00-project-overview.md`.

Non-blocking note: `__all__` in `__init__.py` is not strictly ASCII-sorted
(`CAPABILITY_REGISTRY`, `CursorPagination`, `ConnectorAdapter`, …), but this matches the
pre-existing local ordering convention as the Implementation Notes state; not a defect.
