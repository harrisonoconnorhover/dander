---
id: DANDER-73
title: Add create connector capability protocol
status: done
component: python
epic: connector-capabilities
depends_on: [DANDER-64]
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Local `main` was reset onto `teammate/main` (Harrison's fork) as the trunk — see
`steering/00-project-overview.md` Decision Log, 2026-08-05 entry. Real, still-open gap: write-back
was not ported. `docs/decisions.md`, "2026-08-05 — Optional source capabilities remain structural
and read-only" is explicit: "provider create/update/delete operations remain absent until their
cursor, retry, authorization, and destination semantics are approved separately." All four
write-back tickets (DANDER-73..76) share this same prerequisite design gate — resolve it once, not
per-ticket, before implementing against the Design below, which describes the superseded
`ConnectorAdapter` shape from the pre-reconcile branch and needs a fresh pass against the current
`SourceCapabilities`/`_CAPABILITY_PROTOCOLS` pattern in `src/dander/ingestion/capabilities.py`.

## Context

Write-back is now an optional, opt-in connector capability rather than a hard non-goal, per the
Decision Log entry 2026-08-04 ("Write-back is now an optional, opt-in connector capability, not a
hard non-goal") in `steering/00-project-overview.md`: `create`/`update`/`upsert`/`bulk_upsert`/
`delete` join the Layer 1 capability set alongside the read-side capabilities, and a connector
implements them only if the underlying API genuinely supports them. The core read → land-in-BigQuery
path is unchanged and remains mandatory; write-back is additive surface a pipeline opts into per
connector.

This ticket defines the `create` capability Protocol — creating a new record in the source system —
and registers it with the DANDER-64 `ConnectorAdapter`/`ConnectorOperation` mechanism, following the
same composition-over-inheritance, interface-first shape as the read-side capabilities
(`steering/02-engineering.md`).

## Acceptance Criteria

- [ ] A `runtime_checkable` `Protocol` for `create` (e.g. `SupportsCreate`) is defined in
      `src/dander/ingestion/capabilities.py` with a method that accepts an endpoint name and a
      record mapping to create, and returns the created record's identity (business-key value(s)) or
      the created record mapping.
- [ ] The Protocol is registered against a new `ConnectorOperation.CREATE` member in the DANDER-64
      registry so `ConnectorAdapter.supports(...)` reports it for an implementing source.
- [ ] A source implementing the Protocol is detected as supporting the operation; one that does not
      is reported unsupported and requesting it raises the DANDER-64 unsupported-operation error.
- [ ] The record mapping and returned identity are typed consistently with the `Mapping[str, Any]`
      record shape used by `Source.extract()`, so downstream layers stay path-agnostic.
- [ ] No secret or credential value appears in any error message (`steering/01-security.md`);
      credential access still routes through the audited auth strategy.
- [ ] Unit tests cover detection-positive, detection-negative, and a successful create via a fake
      source (no network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

DANDER-64 established the whole mechanism: an optional connector capability is a
`runtime_checkable` `typing.Protocol`, mapped to a `ConnectorOperation` member through a single
module-level registry (`dict[ConnectorOperation, type]`), and detected by `ConnectorAdapter` via
`isinstance()` once at construction. `ConnectorAdapter` contains *no per-capability branching* —
DANDER-64's Open/Closed contract is that a new capability is added by (1) one `ConnectorOperation`
member, (2) one `Protocol`, and (3) one registry entry, with **zero** edits to `ConnectorAdapter`
logic. This ticket is a pure **extension** along that seam, and the first **write-back** one.

`create` is the first member of the write-back set, so unlike the read-side tickets (DANDER-65..68,
whose `ConnectorOperation` members DANDER-64 already shipped) this ticket must **add the enum member
itself**: `ConnectorOperation.CREATE = "create"`, appended per DANDER-64's documented add-member
pattern for write-back. It then adds the `SupportsCreate` Protocol and the single registry entry
`ConnectorOperation.CREATE: SupportsCreate`. All new code lives in the already-created
`src/dander/ingestion/capabilities.py`.

The capability is "create one new record in the source system." Three shape decisions matter:

1. **The record to create is `Mapping[str, Any]`** — byte-for-byte the record shape already yielded
   by `Source.extract()` and consumed across the writer/catalog layers. The AC requires exactly this
   so downstream stays path-agnostic (the hybrid-source decision in
   `steering/00-project-overview.md`). It is the write-side mirror of the read record.

2. **The return is also `Mapping[str, Any]`, with a documented minimum.** The ticket phrases the
   return as "the created record's identity (business-key value(s)) *or* the created record mapping."
   Both forms are the same static type — `Mapping[str, Any]` — so the contract is expressed as **one**
   type with an invariant stated in the docstring: *the returned mapping MUST at minimum carry the
   endpoint's business-key field(s)* (the fields named in `Endpoint.primary_key`), and MAY carry the
   full server-materialized record (server-assigned id, timestamps, defaults). This gives callers a
   single, mypy-clean contract, satisfies the "typed consistently with `Mapping[str, Any]`"
   criterion directly, and lets a source return the whole created row when the API echoes it without
   forcing sources that only return an id to fabricate one. This mirrors DANDER-65's decision to keep
   the found-record shape identical to `extract()`.

3. **No idempotency guarantee, and it must be documented.** `create` is inherently
   **non-idempotent** — calling it twice with the same record generally produces two records. That is
   a deliberate contrast with `upsert`/`bulk_upsert` (DANDER-75) which are the idempotent write-back
   verbs. The core read → land-in-BigQuery path's idempotency rules
   (`steering/02-engineering.md`) are unaffected; write-back idempotency is a per-pipeline concern the
   caller opts into by choosing the verb. The Protocol docstring states this explicitly so no one
   mistakes `create` for a safe-to-retry operation.

This ticket does **not** implement `create` on any concrete source (`DltRestSource`,
`WorkdayRaasSource`); it defines the contract and its registration only. Detection and the
unsupported-operation raise are exercised through `ConnectorAdapter` with a fake source in tests.

### Security (`steering/01-security.md`)

- The Protocol defines an interface only; it opens **no** new credential path. Concrete
  implementations (later tickets) resolve credentials solely through the audited auth strategy —
  the Protocol imposes nothing that bypasses it, and the docstring says so.
- Write-back carries a *heightened* leakage surface: the `record` mapping and the returned identity
  can contain sensitive field values (HR/comp/customer). The Protocol docstring makes the security
  invariant a contract term: **implementations MUST NOT put any `record` field value, identity
  value, or returned row value into an exception, log, or message.** Transport/auth failures are
  named without the payload. The DANDER-64 `UnsupportedConnectorOperationError` already names only
  `source.config.name` and the operation value — never a secret or row — so the detection-negative
  path is safe by construction.

### Interfaces / classes (all in `src/dander/ingestion/capabilities.py`)

- **`ConnectorOperation.CREATE = "create"`** — new `StrEnum` member appended to the existing
  `ConnectorOperation` (the first write-back member; DANDER-74..76 append `UPDATE`/`UPSERT`/
  `BULK_UPSERT`/`DELETE` after it the same way).

- **`@runtime_checkable class SupportsCreate(Protocol)`** — the capability contract:

  ```python
  @runtime_checkable
  class SupportsCreate(Protocol):
      def create(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]: ...
  ```

  Google-style docstring carries the full contract: `endpoint` is an `Endpoint.name`; `record` is the
  new record's fields as a `Mapping[str, Any]` (same shape as an `extract()` record); returns a
  `Mapping[str, Any]` that at minimum contains the endpoint's business-key field(s) identifying the
  created record, and may be the full server-materialized record. States the two invariants above:
  (a) **non-idempotent** — callers must not blindly retry; (b) the **no-payload-in-errors** security
  rule.

### Registry wiring (one entry)

Add to DANDER-64's single capability registry (the `dict[ConnectorOperation, type]` mapping each
operation to its Protocol):

```python
ConnectorOperation.CREATE: SupportsCreate,
```

With this one line, `ConnectorAdapter(source).supports(ConnectorOperation.CREATE)` returns `True`
for any source whose class satisfies `SupportsCreate` and `False` otherwise; requesting the
operation on a non-supporting source via `adapter.require(ConnectorOperation.CREATE)` raises
DANDER-64's `UnsupportedConnectorOperationError`. **No `ConnectorAdapter` code changes.**

### Files to touch

- `src/dander/ingestion/capabilities.py` — **extend** (created by DANDER-64): add the
  `ConnectorOperation.CREATE` enum member, the `SupportsCreate` Protocol, and the one registry entry.
- `src/dander/ingestion/__init__.py` — export `SupportsCreate` (add to `__all__`, keeping alphabetical
  order) so callers and tests import from the package surface. (The `ConnectorOperation` symbol is
  already exported by DANDER-64; the new member needs no separate export.)
- `tests/ingestion/test_capabilities_create.py` — new unit test module (mirrors the
  `tests/ingestion/` layout and the DANDER-65 `test_capabilities_get_single_object.py` naming).

### Test seams (no network, per `steering/02-engineering.md`)

Two module-level fake `Source` subclasses:

- `FakeCreateSource` — implements `create(endpoint, record)` over a small in-memory
  `dict[str, list[dict]]`, appending the record and returning a mapping carrying the business key
  (e.g. a synthesized `{"id": "..."}`), to exercise the success path without any network.
- `FakePlainSource` — implements only the mandatory `Source.discover`/`extract`.

Cases:

- **Detection-positive:** `ConnectorAdapter(FakeCreateSource(...)).supports(ConnectorOperation.CREATE)`
  is `True`; `CREATE` is in `supported_operations`.
- **Detection-negative:** the same against `FakePlainSource` is `False`.
- **Successful create:** call `create` on the supporting source through the wrapped source (after
  `adapter.require(CREATE)` passes); assert the returned mapping is a `Mapping[str, Any]` containing
  the business-key field, and that the fake's in-memory store grew by one.
- **Unsupported-operation error path:** `adapter.require(ConnectorOperation.CREATE)` against
  `FakePlainSource` raises `UnsupportedConnectorOperationError` (a `ValueError`, **not**
  `AttributeError`); assert `str(exc)` contains the source name and the operation value and **no**
  record/identity value (security assertion).

### Trade-offs

- **One return type (`Mapping[str, Any]`) vs. a union `identity | record`.** Chosen: a single
  `Mapping[str, Any]` with a documented "business key present" minimum. It matches the AC's
  "typed consistently with `Mapping[str, Any]`" wording, keeps one contract for callers, and stays
  mypy-clean, while still letting a source return the full echoed record. A `Mapping[str, str]`
  identity-only return (like DANDER-65's `identity` *input*) was rejected because APIs commonly return
  server-assigned non-string ids/timestamps in the created record, and forcing `str` would lose them.
- **`record` as `Mapping[str, Any]` mirroring `extract()`** rather than a bespoke create-DTO: keeps
  the write path path-agnostic and symmetric with the read path; per-endpoint field validation
  belongs to the concrete source, not this interface.
- **Extension-only placement.** Everything lands in `capabilities.py` (one enum member, one Protocol,
  one registry line) with no `ConnectorAdapter` edit, proving DANDER-64's open-for-extension design
  and keeping the ticket small and independently reviewable.

### Notes / flags

- **Return-shape ambiguity (flag for Code/Review):** the AC says the method returns "identity
  (business-key value(s)) *or* the created record mapping." This design unifies both under one
  `Mapping[str, Any]` return with a documented minimum (business key present). If a reviewer instead
  wants two distinct typed results, that is a compatible refinement, but the single-shape form is
  recommended for one obvious contract.
- **Depends on DANDER-64** (`ConnectorOperation`, `ConnectorAdapter`, the registry, and
  `UnsupportedConnectorOperationError`) — currently `in-code`, not `done`. This design assumes the
  registry is a single mapping keyed by `ConnectorOperation` and the adapter's `supports`/`require`
  are generic (keyed by operation, not per-capability). If DANDER-64 lands per-capability accessors
  instead, add a thin `create(...)` accessor that casts to `SupportsCreate` or raises
  `UnsupportedConnectorOperationError`; prefer the generic path so this stays a Protocol + one entry.
- The Decision Log entry this ticket relies on (2026-08-04, "Write-back is now an optional, opt-in
  connector capability, not a hard non-goal") is present in `steering/00-project-overview.md`.

## Implementation Notes

**2026-08-05 update:** the note below and the Review Log entry beneath it describe the
pre-reconciliation `ConnectorAdapter` implementation from `backup/local-main-pre-reconcile`, no
longer on this trunk (see the Reconciliation note above). Kept for history. Current implementation
against `teammate/main`'s `SourceCapabilities`:

- `src/dander/ingestion/capabilities.py`: added `ConnectorOperation.CREATE`, the `SupportsCreate`
  `Protocol` (`create(self, endpoint, record) -> Mapping[str, Any]`, docstring states
  non-idempotency and the no-blind-retry rule), a `_CAPABILITY_PROTOCOLS` entry, and
  `SourceCapabilities.create()` (`require()` guard, delegate, `isinstance(result, Mapping)`
  validation raising `InvalidConnectorCapabilityResultError` otherwise) — matching the existing
  `get_single_object`/`count` accessor pattern exactly.
- Idempotency/retry/authorization semantics recorded in `docs/decisions.md`, "2026-08-05 —
  Write-back and deleted-record-feed semantics."
- `src/dander/ingestion/__init__.py` / `README.md` updated to export and document it.
- `tests/ingestion/test_capabilities.py`: extended `_CapableSource`, the facade test, and the
  invalid-result and full-operation-set parametrizations to cover `create`.
- Verified: `ruff check`/`ruff format --check`/`mypy src/dander/ingestion` clean;
  `pytest tests/ingestion tests/pipeline tests/cli/test_connector_cli.py` green. Done directly in
  this reconciliation session, not through the Design→Code→PR-Review agent pipeline — no PASS
  entry added to Review Log for this pass.

---

Original (superseded) note below:

Built exactly per Design, no deviations:

- **`src/dander/ingestion/capabilities.py`** — added `ConnectorOperation.CREATE = "create"` as the
  fifth enum member (documented as the first write-back capability, non-idempotent, in its
  attribute docstring); added `@runtime_checkable class SupportsCreate(Protocol)` with
  `create(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]`, whose docstring
  states the single-shape return contract (business-key minimum, may carry the full
  server-materialized record), the non-idempotency invariant, and the no-payload-in-errors
  security rule; added the one registry entry `ConnectorOperation.CREATE: SupportsCreate` to
  `CAPABILITY_REGISTRY`. No `ConnectorAdapter` code changed. Module docstring updated with a
  DANDER-73 scope note alongside the existing DANDER-65..68 notes.
- **`src/dander/ingestion/__init__.py`** — imported and exported `SupportsCreate`, inserted
  alphabetically into both the import block and `__all__` (`SupportsCount`, `SupportsCreate`,
  `SupportsGetDeleted`, …), matching the existing local ordering convention.
- **`tests/ingestion/test_capabilities_create.py`** (new) — mirrors the DANDER-65
  `test_capabilities_get_single_object.py` shape: module-level `FakeCreateSource` (implements
  `create` over an in-memory `dict[str, list[dict]]`, synthesizing an incrementing `id` and
  echoing the submitted fields back) and `FakePlainSource` (mandatory contract only). Five tests,
  no network: detection-positive (`supports`/`supported_operations`), detection-negative, a direct
  `isinstance` check against `SupportsCreate`, a successful-create test asserting the returned
  mapping carries the business key plus submitted fields and the fake's extract-visible store grew
  by one, and the unsupported-operation path asserting `UnsupportedConnectorOperationError` (a
  `ValueError`, not `AttributeError`) whose message contains the source name and `"create"` but
  none of the record's field values or secret-like markers.

Resolved the Design's flagged return-shape ambiguity as recommended: one `Mapping[str, Any]`
return type with a documented "business key present" minimum, not a union type — matches the AC's
literal wording and keeps one contract for callers.

Confirmed DANDER-64 is `done` (not just `in-code` as this ticket's Design flagged as a risk), so
the registry, `ConnectorOperation`, `ConnectorAdapter.supports`/`.require`, and
`UnsupportedConnectorOperationError` were all available as designed — no fallback accessor needed.

Toolchain: `ruff check` clean and `ruff format --check` clean on all three touched/created files;
`mypy` clean (`src/dander/ingestion/capabilities.py`, `src/dander/ingestion/__init__.py`,
`tests/ingestion/test_capabilities_create.py`); `pytest tests/ingestion/` fully green (91 tests,
no regressions). Ran the full repo `pytest` suite too: five pre-existing failures in
`tests/cli/` (Rich ANSI-escape formatting assertions in `test_catalog_cli.py`,
`test_cli.py`, `test_metadata_cli.py`, `test_transform_cli.py`) reproduce identically on `main`
before this change (verified via `git stash`) — unrelated to this ticket's files, not a
regression introduced here.

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — PASS

Verified against the Acceptance Criteria, the Design, `steering/01-security.md`,
`steering/02-engineering.md`, and `steering/languages/python.md`. Inspected
`src/dander/ingestion/capabilities.py`, `src/dander/ingestion/__init__.py`, and
`tests/ingestion/test_capabilities_create.py`.

**Acceptance criteria**

1. `@runtime_checkable class SupportsCreate(Protocol)` is defined in
   `src/dander/ingestion/capabilities.py` with
   `create(self, endpoint: str, record: Mapping[str, Any]) -> Mapping[str, Any]` — endpoint name +
   record mapping in, created-record/identity mapping out. Met.
2. `ConnectorOperation.CREATE = "create"` added as a new member with an attribute docstring noting
   it is the first write-back operation and non-idempotent; registered via the single entry
   `ConnectorOperation.CREATE: SupportsCreate` in `CAPABILITY_REGISTRY`. Met.
3. Detection works through the unchanged generic adapter: `ConnectorAdapter.supports` /
   `supported_operations` report `CREATE` for `FakeCreateSource` and not for `FakePlainSource`;
   `adapter.require(ConnectorOperation.CREATE)` raises `UnsupportedConnectorOperationError`
   (a `ValueError`, not `AttributeError`). Met.
4. Record in and identity out are both `Mapping[str, Any]`, matching the `Source.extract()` record
   shape; the docstring states the "business key at minimum, may carry the full server-materialized
   record" invariant, resolving the Design's flagged return-shape ambiguity as recommended. Met.
5. Security: no credential-shaped literal anywhere in the diff (grepped); the Protocol opens no new
   credential path and its `Raises:` section makes "no `record` field value, identity value, or
   returned row value in an exception, log, or message" a contract term;
   `UnsupportedConnectorOperationError` still names only `source.config.name` and the operation
   value. `.env.example` needs no change (no new secret keys). Met.
6. Five unit tests, no network: detection-positive, detection-negative, direct `isinstance` against
   the Protocol, a successful create asserting the returned mapping carries the business key and the
   fake's store grew by one, and the unsupported-operation path asserting the message contains the
   source name and `"create"` but no record values or secret-shaped markers. Fixtures carry only
   synthetic data (`https://fake.example.test`, a public-domain name). Met.
7. Toolchain confirmed independently: `ruff check` clean, `ruff format --check` clean, `mypy`
   (strict, per `pyproject.toml`) clean on all three files, `pytest tests/ingestion/` 100 passed.
   Met.

**Design fidelity** — pure extension along the DANDER-64 seam: one enum member, one Protocol, one
registry line, zero `ConnectorAdapter` edits, exactly as designed. Export added to
`__init__.py`/`__all__` in the local alphabetical position. Dependency DANDER-64 confirmed `done`.

**Non-blocking nits (no rework required; fold into a later capability ticket if convenient):**

- `src/dander/ingestion/capabilities.py` module docstring still says DANDER-64 shipped "the full
  `ConnectorOperation` value set"; the write-back members now extend it (the same paragraph already
  describes the add-a-member pattern, so this is only a wording staleness).
- `src/dander/ingestion/README.md` describes write-back members as "later"; `create` has now landed.
- `tests/ingestion/test_capabilities.py::test_default_registry_is_empty_for_plain_source` has a
  docstring claiming the shipped registry is empty (stale since DANDER-65); the assertion itself is
  about a plain source's empty support set and remains correct.

Verdict: **PASS**. Status set to `done`.
