---
id: DANDER-76
title: Add delete connector capability protocol
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

This ticket defines the `delete` capability Protocol — deleting a record in the source system by id
— and registers it with the DANDER-64 `ConnectorAdapter`/`ConnectorOperation` mechanism. This is the
write-side counterpart to the read-side `get_deleted` tombstone feed (DANDER-66); the two are
distinct capabilities (one reports source deletions, this one performs one) and neither implies the
other.

## Acceptance Criteria

- [ ] A `runtime_checkable` `Protocol` for `delete` (e.g. `SupportsDelete`) is defined in
      `src/dander/ingestion/capabilities.py` with a method that accepts an endpoint name and a record
      identity (business-key value(s)) and returns a defined result (e.g. confirmation / not-found
      status).
- [ ] The Protocol is registered against a new `ConnectorOperation.DELETE` member in the DANDER-64
      registry and detected by `ConnectorAdapter`.
- [ ] The identity typing is consistent with the `get_single_object`/`update` identity convention
      (DANDER-65/74) and the `Mapping[str, Any]` record shape.
- [ ] A source without the capability is reported unsupported and requesting it raises the DANDER-64
      unsupported-operation error.
- [ ] No secret or credential value appears in any error message (`steering/01-security.md`);
      credential access still routes through the audited auth strategy.
- [ ] Unit tests cover detection-positive, detection-negative, and both a successful delete and a
      not-found result via a fake source (no network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

DANDER-64 established the whole mechanism, and DANDER-65 / 73 / 74 established the extension pattern
every capability follows: an optional connector capability is a `runtime_checkable`
`typing.Protocol`, mapped to a `ConnectorOperation` member through the single module-level
`CAPABILITY_REGISTRY` (`dict[ConnectorOperation, type]`), and detected by `ConnectorAdapter` via
`isinstance()` once at construction. `ConnectorAdapter` carries **no per-capability branching** —
DANDER-64's Open/Closed contract is that a new capability is added by (1) one `ConnectorOperation`
member, (2) one `Protocol`, and (3) one registry entry, with **zero** edits to `ConnectorAdapter`
logic. This ticket is a pure **extension** along that seam and the last of the write-back set
(`create`/`update`/`upsert`/`bulk_upsert`/`delete`), additive and opt-in per the Decision Log entry
2026-08-04; the core read → land-in-BigQuery path stays mandatory and untouched.

`delete` is a write-back member, so — like `create` (DANDER-73) and `update` (DANDER-74) and unlike
the read-side tickets whose enum members DANDER-64 already shipped — this ticket must **add the enum
member itself**: `ConnectorOperation.DELETE = "delete"`, appended per DANDER-64's documented
add-member pattern. It then adds the `SupportsDelete` Protocol and the single registry entry
`ConnectorOperation.DELETE: SupportsDelete`. All new code lands in the already-created
`src/dander/ingestion/capabilities.py`.

The capability is "delete one record in the source system, addressed by its business key." Two shape
decisions matter; one is inherited verbatim from the family and one is specific to delete:

1. **Record identity is a `Mapping[str, str]`** keyed by the endpoint's business-key field name(s) —
   identical to the `get_single_object` (DANDER-65) and `update` (DANDER-74) identity convention, and
   grounded in `Endpoint.primary_key: list[str]`, so composite keys are first-class without positional
   guessing. A mapping (`{"id": "42"}`, or `{"tenant": "...", "id": "..."}`) is self-describing and
   cannot be confused with a record body. Key **values** are `str` because they travel into a URL path
   or query filter; a source needing a non-string key coerces at its own edge, exactly as DANDER-65/74
   and `Endpoint.cursor_param` do. This directly satisfies the AC that identity typing be consistent
   with the `get_single_object`/`update` identity convention. There is **no record body** — delete is
   addressed purely by identity — so unlike `create`/`update` there is no `record`/`changes` argument.

2. **The result is a defined, typed two-valued outcome, never an exception and never a bare bool.**
   The AC asks delete to return "a defined result (e.g. confirmation / not-found status)." Delete has
   two normal, expected outcomes — the record existed and was deleted, or it was already absent — and
   both must be reportable **as a value**, not by raising (which would tempt callers to embed the
   identity value in a message, violating `steering/01-security.md`). This design models that as a
   dedicated closed value set: `DeleteOutcome(StrEnum)` with `DELETED = "deleted"` and
   `NOT_FOUND = "not_found"`. A `StrEnum` matches the house convention (`WriteMode`, `IngestionEngine`,
   `BackoffKind`, `PaginationKind`, `ConnectorOperation`), is self-describing at call sites, is
   mypy-narrowable, and — unlike a `bool` — leaves room for the outcome to read unambiguously in logs
   and control tables without a comment. The method returns `DeleteOutcome` (no union, no sentinel).

Choosing a distinct `DeleteOutcome` StrEnum rather than reusing DANDER-65's `RECORD_NOT_FOUND`
sentinel: `RECORD_NOT_FOUND` was designed to sit in a union beside a *found record*
(`Mapping[str, Any] | RecordNotFound`) — delete returns no record, so a `Mapping | sentinel` union is
the wrong shape here. A two-member enum is the minimal honest contract for "confirmation vs.
not-found" and keeps the write-back result reading as its own small, closed vocabulary.

**Idempotency.** Delete-by-id is naturally idempotent — deleting an already-absent record converges
to the same source-system state — which fits the re-runnable-pipeline principle in
`steering/02-engineering.md`. The `NOT_FOUND` outcome is exactly what makes an idempotent retry safe:
a second delete of the same identity returns `NOT_FOUND` rather than raising, so a re-run neither
duplicates nor errors. This is the deliberate contrast with `create` (non-idempotent). The Protocol
makes no idempotency *guarantee* about the underlying API (that is per-source), but its result shape
does not preclude safe retry, and the docstring notes this.

This ticket does **not** implement `delete` on any concrete source (`DltRestSource`,
`WorkdayRaasSource`, `EnterpriseSource` subclasses); it defines the contract and its registration
only. Credential access is not the Protocol's concern — concrete implementations continue to route
every credential fetch through the audited auth strategy (`steering/01-security.md`); the Protocol
adds no new credential surface. Detection and the unsupported-operation raise are exercised through
`ConnectorAdapter` (DANDER-64) with a fake source in tests (no network).

### Security (`steering/01-security.md`)

- The Protocol defines an interface only; it opens **no** new credential path. Concrete
  implementations resolve credentials solely through the audited auth strategy.
- The `identity` values can be sensitive (a business key may itself be PII). The Protocol docstring
  makes the security invariant a contract term: **implementations MUST NOT put the `identity` value
  (or any resolved row value) into an exception, log, or message.** Transport/auth/permission
  failures are named by endpoint and operation only. Because "not found" is a returned
  `DeleteOutcome` value and **not** an exception, the common miss path never tempts a caller to build
  a message from the identity. DANDER-64's `UnsupportedConnectorOperationError` already names only
  `source.config.name` and the operation value — never a secret or row — so the detection-negative
  path is safe by construction.

### Interfaces / classes (all in `src/dander/ingestion/capabilities.py`)

- **`class DeleteOutcome(StrEnum)`** — the closed result vocabulary for a delete:
  - `DELETED = "deleted"` — the record existed and was removed.
  - `NOT_FOUND = "not_found"` — no record matched the identity (already absent); a normal, non-error
    outcome that makes idempotent retry safe.
  Google-style docstring states the two-outcome contract and the idempotency note.

- **`ConnectorOperation.DELETE = "delete"`** — new `StrEnum` member appended to the existing
  `ConnectorOperation` (value matches the method name, as the Core and other write-back members do).

- **`@runtime_checkable class SupportsDelete(Protocol)`** — the capability contract:
  ```python
  @runtime_checkable
  class SupportsDelete(Protocol):
      def delete(self, endpoint: str, identity: Mapping[str, str]) -> DeleteOutcome: ...
  ```
  Google-style docstring carries the full contract: `endpoint` is an `Endpoint.name`; `identity` maps
  each `Endpoint.primary_key` field name to its value (same convention as `SupportsGetSingleObject` /
  `SupportsUpdate`); returns `DeleteOutcome.DELETED` on removal or `DeleteOutcome.NOT_FOUND` when no
  record matched. States the two invariants: (a) not-found is a **returned value, not an exception**,
  and delete is naturally idempotent (a repeat delete returns `NOT_FOUND`); (b) the
  **no-identity/row-value-in-errors** security rule, and that credential access goes through the
  audited auth strategy, not the Protocol.

### Registry wiring (one enum member + one entry)

Two additions in `capabilities.py`, following DANDER-64's add-member pattern (no `ConnectorAdapter`
edits):

- Append the write-back member to `ConnectorOperation(StrEnum)`:
  ```python
  DELETE = "delete"
  ```
- Add one entry to the single `CAPABILITY_REGISTRY: dict[ConnectorOperation, type]`:
  ```python
  ConnectorOperation.DELETE: SupportsDelete,
  ```

With these, `ConnectorAdapter(source).supports(ConnectorOperation.DELETE)` returns `True` for any
source whose class structurally satisfies `SupportsDelete` and `False` otherwise; requesting the
operation on a non-supporting source (`adapter.require(ConnectorOperation.DELETE)`) raises DANDER-64's
`UnsupportedConnectorOperationError` (a `ValueError`, never `AttributeError`), which names source +
operation only. **No `ConnectorAdapter` code changes** — this satisfies the AC's registration and
detection requirements through the generic mechanism.

### Files to touch

- `src/dander/ingestion/capabilities.py` — **extend** (created by DANDER-64): add the `DeleteOutcome`
  `StrEnum`, the `ConnectorOperation.DELETE` member, the `SupportsDelete` Protocol, and the one
  registry entry. `from __future__ import annotations`; Google-style docstrings on the enum and the
  Protocol.
- `src/dander/ingestion/__init__.py` — export `DeleteOutcome` and `SupportsDelete` (add to the imports
  from `dander.ingestion.capabilities` and to `__all__`, keeping the existing alphabetical order).
  `ConnectorOperation` / `ConnectorAdapter` / `UnsupportedConnectorOperationError` are already exported
  by DANDER-64; the new enum member needs no separate export.
- `tests/ingestion/test_capabilities_delete.py` — new unit test module (mirrors the
  `tests/ingestion/test_capabilities_get_single_object.py` / `_update.py` layout).

### Test seams (no network, per `steering/02-engineering.md`)

Two module-level fake `Source` subclasses:
- `FakeDeleteSource` implements `delete(endpoint, identity)` over a small in-memory dict of
  `endpoint → {identity-tuple → record}`; a present identity is popped and returns
  `DeleteOutcome.DELETED`, an absent one returns `DeleteOutcome.NOT_FOUND` (no raise).
- `FakePlainSource` implements only the mandatory `Source.discover`/`extract`.

Cases:
- **Detection-positive:** `ConnectorAdapter(FakeDeleteSource(...)).supports(ConnectorOperation.DELETE)`
  is `True` and `ConnectorOperation.DELETE in supported_operations`.
- **Detection-negative:** the same against `FakePlainSource` is `False`.
- **Successful delete:** call `delete("candidates", {"id": "42"})` on a source that has that record;
  assert the return is `DeleteOutcome.DELETED` and the fake's store shrank by one.
- **Not-found result:** call `delete` for an absent identity; assert the return is
  `DeleteOutcome.NOT_FOUND`, that it does **not** raise, and that no identity value leaks (idempotency
  assertion — a repeat delete of the just-deleted id also returns `NOT_FOUND`).
- **Unsupported-operation error path:** `adapter.require(ConnectorOperation.DELETE)` against
  `FakePlainSource` raises `UnsupportedConnectorOperationError` (a `ValueError`, **not**
  `AttributeError`); assert `str(exc)` contains the source name and the operation value but **not** any
  identity value (security assertion, `steering/01-security.md`).

### Trade-offs

- **`DeleteOutcome` StrEnum vs. reusing `RECORD_NOT_FOUND` vs. a bare `bool`.** Chosen: a two-member
  `DeleteOutcome` StrEnum. Reusing DANDER-65's `RECORD_NOT_FOUND` sentinel is the wrong shape — that
  sentinel exists to sit in a union beside a *found record*, and delete returns no record. A bare
  `bool` (`True`=deleted) is terse but reads ambiguously and can't grow a third state cleanly. The
  enum is self-describing, matches the house `StrEnum` convention, narrows in mypy, and keeps the
  not-found path a value rather than an exception (security). Cost: one extra exported symbol.
- **Idempotent not-found-as-value vs. raising on missing target (asymmetry with `update`).** Chosen:
  return `NOT_FOUND`. Unlike `update` (DANDER-74), whose AC does not model not-found and leaves a
  missing target to the source's error path, this ticket's AC explicitly names "not-found status" as a
  returned result, and delete's idempotency makes a returned status the natural, retry-safe contract.
- **Identity-only, no record body.** Delete is addressed purely by business key, so — unlike
  `create`/`update` — there is no `record`/`changes` argument; the interface stays minimal.
- **Extension-only placement.** Everything lands in `capabilities.py` (one enum, one enum member, one
  Protocol, one registry line) with zero `ConnectorAdapter` edits, proving DANDER-64's
  open-for-extension design and keeping the ticket small and independently reviewable.

### Notes / flags

- **Result-shape latitude (flag for Code/Review):** the AC says delete returns "a defined result
  (e.g. confirmation / not-found status)" without pinning the type. This design pins it to a
  `DeleteOutcome(DELETED | NOT_FOUND)` StrEnum. If a reviewer prefers symmetry with DANDER-65 via a
  `RecordNotFound`-style sentinel, or a source needs to carry extra confirmation metadata (e.g. a
  server delete id/timestamp), a `@dataclass(frozen=True)` result is a compatible refinement — but the
  two-member enum is recommended as the minimal one-obvious contract the AC asks for.
- **Depends on DANDER-64** (`ConnectorOperation`, `ConnectorAdapter`, the registry, and
  `UnsupportedConnectorOperationError`) and on the DANDER-65/74 `identity: Mapping[str, str]`
  convention — all currently `in-code`, not `done`. This design assumes the registry is a single
  mapping keyed by `ConnectorOperation` and that `supports`/`require` are generic (keyed by operation,
  not per-capability). If DANDER-64 instead lands per-capability accessors, add a thin `delete(...)`
  accessor on `ConnectorAdapter` that casts to `SupportsDelete` or raises
  `UnsupportedConnectorOperationError`; prefer the generic path so this stays a Protocol + one entry.
- **`DELETE` is not among DANDER-64's four Core enum members**; this ticket adds the member itself,
  consistent with DANDER-64's plan that DANDER-73..76 append their write-back members.
- The Decision Log entry this ticket relies on (2026-08-04, "Write-back is now an optional, opt-in
  connector capability, not a hard non-goal") should be confirmed present in
  `steering/00-project-overview.md`; the copy in this session shows the latest entry as 2026-08-02
  (same flag DANDER-64 raised). Not blocking — this ticket ships an interface only.

## Implementation Notes

**2026-08-05 update:** the note below and the Review Log entry beneath it describe the
pre-reconciliation `ConnectorAdapter` implementation from `backup/local-main-pre-reconcile`, no
longer on this trunk (see the Reconciliation note above). Kept for history. Current implementation
against `teammate/main`'s `SourceCapabilities`:

- `src/dander/ingestion/capabilities.py`: added `ConnectorOperation.DELETE`, the `DeleteOutcome`
  `StrEnum` (`DELETED`/`NOT_FOUND`), the `SupportsDelete` `Protocol`
  (`delete(self, endpoint, identity) -> DeleteOutcome`, docstring notes natural idempotency via
  `NOT_FOUND` rather than raising on a miss), a `_CAPABILITY_PROTOCOLS` entry, and
  `SourceCapabilities.delete()` (`require()` guard, delegate, `isinstance(result, DeleteOutcome)`
  validation) — matching the existing accessor pattern.
- Idempotency/retry/authorization semantics recorded in `docs/decisions.md`, "2026-08-05 —
  Write-back and deleted-record-feed semantics."
- `src/dander/ingestion/__init__.py` / `README.md` updated to export `DeleteOutcome` and
  `SupportsDelete` and document it.
- `tests/ingestion/test_capabilities.py`: extended `_CapableSource`, the facade test (both the
  `DELETED` and `NOT_FOUND` branches), and the invalid-result and full-operation-set
  parametrizations to cover `delete`.
- Verified: `ruff check`/`ruff format --check`/`mypy src/dander/ingestion` clean;
  `pytest tests/ingestion tests/pipeline tests/cli/test_connector_cli.py` green. Done directly in
  this reconciliation session, not through the Design→Code→PR-Review agent pipeline — no PASS
  entry added to Review Log for this pass.

---

Original (superseded) note below:

Implemented exactly per Design, no deviations.

- `src/dander/ingestion/capabilities.py`:
  - Added `ConnectorOperation.DELETE = "delete"` as the new final member of the enum, with an
    `Attributes:` docstring entry describing idempotency and its relationship to `GET_DELETED`.
  - Added `DeleteOutcome(StrEnum)` with `DELETED` / `NOT_FOUND` members, documented as the closed,
    non-exception result vocabulary that keeps a miss out of exception messages
    (`steering/01-security.md`) and makes retry idempotent.
  - Added `@runtime_checkable class SupportsDelete(Protocol)` with
    `delete(self, endpoint: str, identity: Mapping[str, str]) -> DeleteOutcome`, matching the
    `identity: Mapping[str, str]` convention from `SupportsGetSingleObject`/`SupportsUpdate`
    exactly (no `record`/`changes` argument, since delete is identity-only). Docstring states the
    two-outcome contract, the no-identity/row-value-in-errors security invariant, and that
    credential access is out of this Protocol's concern (routes through the audited auth strategy
    in concrete implementations).
  - Added the single registry entry `ConnectorOperation.DELETE: SupportsDelete` to
    `CAPABILITY_REGISTRY`; zero edits to `ConnectorAdapter` itself (detection/`require()` already
    generic over the registry). Updated the module docstring and the registry's docstring to note
    DANDER-76 as the fourth write-back capability.
- `src/dander/ingestion/__init__.py`: exported `DeleteOutcome` and `SupportsDelete`, inserted in
  alphabetical order alongside the existing imports/`__all__` entries.
- `tests/ingestion/test_capabilities_delete.py` (new): mirrors the
  `test_capabilities_get_single_object.py` / `test_capabilities_update.py` layout with
  `FakeDeleteSource` (in-memory table; pops on delete, returns `DELETED`/`NOT_FOUND`) and
  `FakePlainSource`. Covers: detection-positive, detection-negative,
  `isinstance` against `SupportsDelete` directly, a successful delete (return value +
  store shrinks by one), a not-found result on an absent identity (no raise) plus a genuine
  repeat-delete-of-the-just-deleted-id idempotency assertion (also `NOT_FOUND`, no raise), and the
  `UnsupportedConnectorOperationError` path asserting the message names the source/operation but
  leaks no identity/row value.

Toolchain (`uv run`): `ruff check` clean, `ruff format --check` clean, `mypy` clean on all changed
files, and `pytest tests/ingestion/` — 118 tests pass (6 in the new
`tests/ingestion/test_capabilities_delete.py`, no regressions in the rest of the suite). A
full-repo `pytest` run shows 6 pre-existing failures in
`tests/cli/{test_cli,test_catalog_cli,test_metadata_cli,test_transform_cli}.py` unrelated to this
change (rich-console ANSI-formatted output vs. plain-string assertions); confirmed pre-existing by
reproducing on a clean `git stash` of `main` before this change.

### Addendum fixes (2026-08-04)

- `src/dander/ingestion/README.md` — amended the "Optional capability discovery" paragraph's
  operation enumeration to include `delete` alongside `create`/`update`/`upsert`, and added a
  clause distinguishing `delete` (performs a deletion; returns `DeleteOutcome.DELETED` /
  `NOT_FOUND` rather than raising, so a repeat call is idempotent) from `get_deleted` (reports
  deletions the source already made). Reflowed the whole paragraph while in the file, which also
  fixes DANDER-75's flagged 101-char line at what was `README.md:39`; all lines in the paragraph
  are now ≤99 chars.
- Corrected the toolchain paragraph above to the actual `uv run pytest tests/ingestion/` figures:
  118 tests pass, 6 of them in `tests/ingestion/test_capabilities_delete.py` (re-verified by
  re-running the suite during this addendum).

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — FAIL

**Verified good.** The core contract is correct and cleanly built:

- `SupportsDelete` is a `@runtime_checkable` `Protocol` in `src/dander/ingestion/capabilities.py:578`
  with `delete(self, endpoint: str, identity: Mapping[str, str]) -> DeleteOutcome` — identity typing
  is byte-for-byte the `SupportsGetSingleObject` / `SupportsUpdate` convention (AC 1, AC 3).
- `ConnectorOperation.DELETE = "delete"` added (`capabilities.py:132`) with an `Attributes:` entry,
  and exactly one registry line `ConnectorOperation.DELETE: SupportsDelete` (`capabilities.py:632`).
  **Zero** `ConnectorAdapter` edits — DANDER-64's Open/Closed seam held (AC 2).
- `DeleteOutcome(StrEnum)` (`DELETED` / `NOT_FOUND`) matches the house `StrEnum` convention and keeps
  the miss path a returned value, so no identity can leak into an exception message (AC 5). The
  unsupported path raises `UnsupportedConnectorOperationError` (a `ValueError`) naming source +
  operation only (AC 4).
- `tests/ingestion/test_capabilities_delete.py`: 6 tests covering detection-positive,
  detection-negative, direct `isinstance`, successful delete (return value + store shrinks),
  not-found + genuine repeat-delete idempotency, and the unsupported-operation message with a
  no-leak assertion. No network (AC 6).
- Exports in `src/dander/ingestion/__init__.py` are alphabetically correct in both the import block
  and `__all__`.
- Toolchain re-run independently: `ruff check` clean, `ruff format --check` clean, `mypy` clean on
  all three changed files, `pytest tests/ingestion/` — 118 passed. Full-repo run reproduces the 6
  pre-existing `tests/cli/*` rich-console ANSI failures, unrelated to this change.
- Security: no credential-shaped literal anywhere in the diff; no new secret keys, so `.env.example`
  needs no change; the fixture uses a fictional/public-domain name consistent with the sibling
  capability test modules; the Protocol opens no credential path.

**Blocking.**

1. **`src/dander/ingestion/README.md:30` is now false.** The "Optional capability discovery"
   paragraph enumerates the detectable set as "`get_single_object`, `get_deleted`, `count`,
   `test_connection`, plus opt-in write-back operations `create`, `update`, `upsert`" — `delete` is
   missing, so the package README actively tells a reader the adapter cannot detect a capability it
   now can. This violates AC 7 ("No steering violations (… docs)") and
   `steering/languages/python.md` ("READMEs per top-level package"; docs stay true to code). Every
   sibling capability ticket kept this line current (DANDER-64 wrote the paragraph; 66, 68, 73, 74,
   and 75 each added their member); DANDER-76's Design simply omitted `README.md` from "Files to
   touch", so this is a design gap, not a coding error — fix the doc regardless.
   **Expected:** amend the enumeration to `…write-back operations `create`, `update`, `upsert`,
   `delete``, and — mirroring the existing one-line `upsert` note at `README.md:37-39` — add a short
   clause distinguishing `delete` (performs a deletion, returns `DeleteOutcome.DELETED` /
   `NOT_FOUND` rather than raising, so a repeat call is idempotent) from `get_deleted` (reports
   deletions the source already made). Keep lines at or under ~100 chars; DANDER-75's review already
   flagged `README.md:39` at 101 chars, so reflow that line while you are in the file.

2. **Implementation Notes report test counts that do not match the tree.** The notes claim "all 108
   tests pass (18 in the new file)"; the actual figures are **118** in `tests/ingestion/` and **6**
   in `tests/ingestion/test_capabilities_delete.py`. The substance ("everything passes, no
   regressions") is verified true, but the numbers are wrong and a reviewer relies on them.
   **Expected:** correct both figures in the Implementation Notes toolchain paragraph to the values
   an actual `uv run pytest` run reports.

No other blocking issues. Fix 1 and 2 and this is a PASS — the Protocol, the registration, the
security posture, and the tests themselves need no changes.

### 2026-08-04 — PASS

Re-review of the two blocking items from the FAIL above. **Both are fixed**, and everything
previously verified good still holds.

**Addendum item 1 — `src/dander/ingestion/README.md` (fixed).** The "Optional capability
discovery" paragraph now enumerates the write-back set as `create`, `update`, `upsert`, `delete`
(`README.md:30`), and adds the requested distinguishing clause at `README.md:40-42`: "`delete`
performs a deletion by caller-supplied identity and returns `DeleteOutcome.DELETED` / `NOT_FOUND`
rather than raising, so a repeat call is idempotent — distinct from `get_deleted`, which reports
deletions the source already made." The paragraph was reflowed; every line in it is now ≤99 chars
(measured), which also clears DANDER-75's flagged 101-char line. Docs are true to code again
(AC 7, `steering/languages/python.md`).

**Addendum item 2 — Implementation Notes test counts (fixed).** The notes now state 118 tests in
`tests/ingestion/` with 6 in `tests/ingestion/test_capabilities_delete.py`. Independently
re-verified: `uv run pytest tests/ingestion/` → **118 passed**; `uv run pytest
tests/ingestion/test_capabilities_delete.py` → **6 passed**. Figures match the tree.

**Acceptance criteria — all met.**

1. `@runtime_checkable class SupportsDelete(Protocol)` in `src/dander/ingestion/capabilities.py:578`
   with `delete(self, endpoint: str, identity: Mapping[str, str]) -> DeleteOutcome`; the result is
   the defined two-valued `DeleteOutcome(StrEnum)` (`DELETED` / `NOT_FOUND`, `capabilities.py:558`).
2. `ConnectorOperation.DELETE = "delete"` (`capabilities.py:132`, with an `Attributes:` entry) and
   exactly one registry line `ConnectorOperation.DELETE: SupportsDelete` (`capabilities.py:632`).
   **Zero** `ConnectorAdapter` edits — DANDER-64's Open/Closed seam held.
3. Identity typing is byte-for-byte the `SupportsGetSingleObject` / `SupportsUpdate` convention
   (`Mapping[str, str]` keyed by `Endpoint.primary_key`); no record body, correct for delete.
4. Detection-negative returns `False` and `require()` raises `UnsupportedConnectorOperationError`
   (a `ValueError`, not `AttributeError`), whose docstring and message name only source + operation.
5. No credential-shaped literal anywhere in the diff (grepped); no new secret keys, so
   `.env.example` needs no change; the Protocol opens no credential path and its docstring makes
   the no-identity/no-row-value-in-errors rule a contract term. Not-found is a returned value, not
   an exception, so the common miss path cannot build a message from the identity.
6. `tests/ingestion/test_capabilities_delete.py` — 6 tests: detection-positive, detection-negative,
   direct `isinstance`, successful delete (return value + store shrinks), not-found plus a genuine
   repeat-delete idempotency assertion, and the unsupported-operation message with a no-leak
   assertion. Fully offline; fixture uses a fictional/public-domain name, consistent with siblings.
7. No steering violations.

**Toolchain re-run independently:** `uv run ruff check` clean, `uv run ruff format --check` clean
(23 files), `uv run mypy` clean on all three changed files, `uv run pytest tests/ingestion/` → 118
passed. The 6 `tests/cli/*` failures remain pre-existing (rich-console ANSI vs. plain-string
assertions) and are untouched by this change.

**Non-blocking observation (not for this ticket):** in `src/dander/ingestion/__init__.py`,
`"CursorPagination"` sits between `"ConnectionStatus"` and `"ConnectorAdapter"` in `__all__` — a
pre-existing ordering artifact from DANDER-64, not introduced here. DANDER-76's own entries
(`DeleteOutcome`, `SupportsDelete`) are correctly placed in both the import block and `__all__`.

Verdict: **PASS**. Status set to `done`.
