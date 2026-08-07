---
id: DANDER-74
title: Add update connector capability protocol
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
underlying API genuinely supports it, discovered the same way as any other capability via
`ConnectorAdapter`; the core read → land-in-BigQuery path is unchanged and remains mandatory.

This ticket defines the `update` capability Protocol — updating an existing record by id — and
registers it with the DANDER-64 `ConnectorAdapter`/`ConnectorOperation` mechanism, following the
same interface-first shape as the read-side capabilities (`steering/02-engineering.md`).

## Acceptance Criteria

- [ ] A `runtime_checkable` `Protocol` for `update` (e.g. `SupportsUpdate`) is defined in
      `src/dander/ingestion/capabilities.py` with a method that accepts an endpoint name, a record
      identity (business-key value(s)), and the field changes to apply, and returns the updated
      record mapping or its identity.
- [ ] The Protocol is registered against a new `ConnectorOperation.UPDATE` member in the DANDER-64
      registry and detected by `ConnectorAdapter`.
- [ ] The identity/record typing is consistent with the `Mapping[str, Any]` shape used by
      `Source.extract()` and the `get_single_object` identity convention (DANDER-65).
- [ ] A source without the capability is reported unsupported and requesting it raises the DANDER-64
      unsupported-operation error.
- [ ] No secret or credential value appears in any error message (`steering/01-security.md`);
      credential access still routes through the audited auth strategy.
- [ ] Unit tests cover detection-positive, detection-negative, and a successful update via a fake
      source (no network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

DANDER-64 established the mechanism and DANDER-65 established the extension pattern every capability
follows: an optional connector capability is a `runtime_checkable` `typing.Protocol`, mapped to a
`ConnectorOperation` member through the single `CAPABILITY_REGISTRY`, and detected by
`ConnectorAdapter` via `isinstance()` at construction. This ticket is a pure **extension** of that
mechanism — it adds one `ConnectorOperation` member, one `Protocol`, and one registry entry, and
touches **no** `ConnectorAdapter` logic (satisfying DANDER-64's Open/Closed "extend by adding a
Protocol + one registry entry" contract). All new code lands in the already-created
`src/dander/ingestion/capabilities.py`. It is the write-back sibling of the read-side
`SupportsGetSingleObject` (DANDER-65) and mirrors its shape exactly so the whole capability set reads
as one family; write-back is additive and opt-in per the Decision Log entry 2026-08-04, while the
core read → land-in-BigQuery path stays mandatory and untouched.

The capability is "update an existing record, addressed by its business key, with a set of field
changes." Three shape decisions matter, and two of them are inherited verbatim from DANDER-65 so the
family stays consistent:

1. **Record identity is a `Mapping[str, str]`** keyed by the endpoint's business-key field name(s) —
   identical to the `get_single_object` identity convention (DANDER-65) and grounded in
   `Endpoint.primary_key: list[str]`, so composite keys are first-class without positional guessing.
   A mapping (`{"id": "42"}`, or `{"tenant": "...", "id": "..."}`) is self-describing and cannot be
   confused with the record body. Key **values** are `str` because they travel into a URL path or
   query filter; a source needing a non-string key coerces at its own edge, exactly as DANDER-65 and
   `Endpoint.cursor_param` do. This directly satisfies the AC that identity typing be consistent with
   the `get_single_object` identity convention.

2. **The field changes are a `Mapping[str, Any]`** — a partial record (PATCH-like: only the fields to
   change, not a full replacement), byte-for-byte the same record shape `Source.extract()` yields.
   Using the extract record shape keeps downstream layers (writer, reconciliation, catalog)
   path-agnostic per the AC and the hybrid-source decision in `steering/00-project-overview.md`.
   Whether an individual API applies partial or full-document semantics is a per-source concern
   behind the same contract; the Protocol commits only to "the field changes to apply."

3. **The return type is `Mapping[str, Any]`**, covering the AC's "returns the updated record mapping
   or its identity." The updated record is a `Mapping[str, Any]` (extract shape); an identity-only
   return is a `Mapping[str, str]`, which is assignable to `Mapping[str, Any]`. One return type
   therefore admits both a source that echoes the full updated record and one that can only confirm
   the identity, with no union noise at call sites. This mirrors DANDER-65's *found* return
   (`Mapping[str, Any]`) so the read/write pair is symmetric.

Note on not-found: unlike `get_single_object` — where a miss is a normal, expected outcome modelled
by the `RECORD_NOT_FOUND` sentinel — updating a record that does not exist is genuinely exceptional
and is left to the concrete source's transport/error handling. The ticket AC does not ask this
Protocol to model not-found, so this design keeps the return type clean (no sentinel union) rather
than importing `RecordNotFound`. The **binding** constraint the Protocol docstring states is the
security invariant: an implementation that raises for a missing/invalid target (or any transport/auth
failure) must name the endpoint and operation only, **never** the identity value or any field-change
value (`steering/01-security.md`). This is flagged below for a reviewer who may prefer sentinel
symmetry with DANDER-65.

Update-by-id is naturally idempotent — re-applying the same field changes to the same identity
converges to the same source-system state — which fits the re-runnable-pipeline principle in
`steering/02-engineering.md`; the Protocol makes no idempotency *guarantee* (that is per-source), it
simply does not preclude it.

This ticket does **not** implement the capability on any concrete source (`DltRestSource`,
`WorkdayRaasSource`, `EnterpriseSource` subclasses); it defines the contract and its registration
only. Credential access is not the Protocol's concern — concrete implementations continue to route
every credential fetch through the audited auth strategy (`steering/01-security.md`); the Protocol
adds no new credential surface. Detection and the unsupported-operation raise are exercised through
`ConnectorAdapter` (DANDER-64) with a fake source in tests (no network).

### Interfaces / classes (all in `src/dander/ingestion/capabilities.py`)

- **`@runtime_checkable class SupportsUpdate(Protocol)`** — the capability contract:
  ```python
  def update(
      self,
      endpoint: str,
      identity: Mapping[str, str],
      changes: Mapping[str, Any],
  ) -> Mapping[str, Any]: ...
  ```
  Google-style docstring carries the full contract: `endpoint` is an `Endpoint.name`; `identity`
  maps each `Endpoint.primary_key` field name to its value (same convention as
  `SupportsGetSingleObject`); `changes` is the partial set of fields to apply, in the
  `Mapping[str, Any]` record shape yielded by `Source.extract()`; returns the updated record as
  `Mapping[str, Any]` (extract shape), or the record's identity as a `Mapping[str, str]` when the
  API returns only the key. The docstring states the security invariant: **implementations must not
  put the identity value, any `changes` value, or any returned row value into an exception message**;
  transport/auth/target-missing errors are named by endpoint and operation only. It also notes that
  credential access must go through the audited auth strategy, not be handled inside the Protocol.

### Registry wiring (one enum member + one entry)

Two one-line additions in `capabilities.py`, following DANDER-64's add-member pattern:

- Append the write-back member to `ConnectorOperation(StrEnum)` (value matches the method name, as
  the Core members do):
  ```python
  UPDATE = "update"
  ```
- Add one entry to the single `CAPABILITY_REGISTRY: dict[ConnectorOperation, type]`:
  ```python
  ConnectorOperation.UPDATE: SupportsUpdate,
  ```

With these, `ConnectorAdapter(source).supports(ConnectorOperation.UPDATE)` returns `True` for any
source whose class structurally satisfies `SupportsUpdate` and `False` otherwise; requesting the
operation on a non-supporting source (`adapter.require(ConnectorOperation.UPDATE)`) raises DANDER-64's
`UnsupportedConnectorOperationError` (a `ValueError`, never `AttributeError`), which names source +
operation only. **No `ConnectorAdapter` code changes** — this satisfies the AC's registration and
detection requirements through the generic mechanism.

### Files to touch

- `src/dander/ingestion/capabilities.py` — **extend** (created by DANDER-64): add the
  `ConnectorOperation.UPDATE` member, the `SupportsUpdate` Protocol, and the one registry entry.
  `from __future__ import annotations`; Google-style docstring on the Protocol.
- `src/dander/ingestion/__init__.py` — export `SupportsUpdate` (add to imports and to `__all__`,
  keeping the existing alphabetical order). `ConnectorOperation` / `ConnectorAdapter` /
  `UnsupportedConnectorOperationError` are already exported by DANDER-64.
- `tests/ingestion/test_capabilities_update.py` — new unit test module (mirrors the
  `tests/ingestion/test_capabilities_get_single_object.py` layout from DANDER-65).

### Test seams (no network, per `steering/02-engineering.md`)

Two module-level fake `Source` subclasses:
- `FakeUpdateSource` implements `update(endpoint, identity, changes)` over a small in-memory dict of
  `endpoint → {identity-tuple → record}`; applying `changes` merges into the stored record and
  returns the updated `Mapping[str, Any]`.
- `FakePlainSource` implements only the mandatory `Source.extract`/`discover`.

Cases:
- **Detection-positive:** `ConnectorAdapter(FakeUpdateSource(...)).supports(UPDATE)` is `True` and
  `ConnectorOperation.UPDATE in supported_operations`.
- **Detection-negative:** the same against `FakePlainSource` is `False`.
- **Successful update:** call `update("candidates", {"id": "42"}, {"stage": "hired"})` on the fake and
  assert the returned mapping is `Mapping[str, Any]` (extract shape) with the merged change applied;
  a variant fake that returns only the identity asserts the `Mapping[str, str]` identity form is a
  valid return.
- **Unsupported-operation error path:** requesting `UPDATE` on `FakePlainSource` through the adapter
  raises `UnsupportedConnectorOperationError`; assert `str(exc)` contains the source and operation
  names but **not** any identity value or `changes` value (security assertion, `steering/01-security.md`).

### Trade-offs

- **Single `Mapping[str, Any]` return vs. an explicit `Mapping[str, Any] | Mapping[str, str]` union.**
  Chosen: one `Mapping[str, Any]` return, since a `Mapping[str, str]` identity is assignable to it.
  This keeps call sites free of union-narrowing while still admitting an identity-only echo — the AC's
  "updated record mapping or its identity." A source that wants callers to *distinguish* the two
  cases would need a richer type, but no ticket asks for that; the wider single type is the minimal
  honest contract.
- **No not-found sentinel (asymmetry with DANDER-65).** Chosen: omit `RecordNotFound` from `update`'s
  return. For a read, a miss is expected and modelled as a value; for a write, a missing target is
  exceptional and belongs in the source's error path. **Flag for the Code agent / reviewer:** if the
  reviewer prefers read/write symmetry, returning `Mapping[str, Any] | RecordNotFound` (reusing the
  DANDER-65 sentinel) is a compatible, security-preserving alternative that avoids raising with the
  identity — but the AC lists no not-found behavior, so the clean return is recommended for one
  obvious contract.
- **Partial-`changes` (PATCH) vs. full-record replacement.** Chosen: `changes` is a partial mapping
  of fields to apply; full-vs-partial application is the source's affair behind the same contract.
  This matches how real APIs differ (PATCH vs PUT) without leaking that difference into the interface.
- **Extension-only placement.** Everything lands in `capabilities.py` with one enum member, one
  Protocol, and one registry line, and zero `ConnectorAdapter` edits — proving DANDER-64's
  open-for-extension design and keeping this ticket small and independently reviewable.

### Notes / flags

- **Depends on DANDER-64 and the DANDER-65 conventions.** Assumes DANDER-64's registry is a single
  mapping keyed by `ConnectorOperation` with a generic (operation-keyed, not per-capability)
  adapter accessor, and that `identity: Mapping[str, str]` is the settled cross-capability identity
  shape from DANDER-65. If DANDER-64 instead exposes per-capability typed accessors, add a thin
  `update(...)` accessor on `ConnectorAdapter` that casts to `SupportsUpdate` or raises
  `UnsupportedConnectorOperationError` — but prefer the generic path so this ticket stays a Protocol +
  one registry entry.
- **Registry ships empty in DANDER-64; UPDATE is not among its four Core members.** This ticket adds
  the `UPDATE` enum member itself (DANDER-64 enumerated only the read-side Core four), so the Code
  agent adds both the member and the entry here — consistent with DANDER-64's stated plan that
  DANDER-73..76 append their write-back members.

## Implementation Notes

**2026-08-05 update:** the note below and the Review Log entry beneath it describe the
pre-reconciliation `ConnectorAdapter` implementation from `backup/local-main-pre-reconcile`, no
longer on this trunk (see the Reconciliation note above). Kept for history. Current implementation
against `teammate/main`'s `SourceCapabilities`:

- `src/dander/ingestion/capabilities.py`: added `ConnectorOperation.UPDATE`, the `SupportsUpdate`
  `Protocol` (`update(self, endpoint, identity, changes) -> Mapping[str, Any]`, docstring notes
  natural-but-not-guaranteed idempotency), a `_CAPABILITY_PROTOCOLS` entry, and
  `SourceCapabilities.update()` (`require()` guard, delegate, `isinstance(result, Mapping)`
  validation) — matching the existing accessor pattern.
- Idempotency/retry/authorization semantics recorded in `docs/decisions.md`, "2026-08-05 —
  Write-back and deleted-record-feed semantics."
- `src/dander/ingestion/__init__.py` / `README.md` updated to export and document it.
- `tests/ingestion/test_capabilities.py`: extended `_CapableSource`, the facade test, and the
  invalid-result and full-operation-set parametrizations to cover `update`.
- Verified: `ruff check`/`ruff format --check`/`mypy src/dander/ingestion` clean;
  `pytest tests/ingestion tests/pipeline tests/cli/test_connector_cli.py` green. Done directly in
  this reconciliation session, not through the Design→Code→PR-Review agent pipeline — no PASS
  entry added to Review Log for this pass.

---

Original (superseded) note below:

Implemented exactly as designed — a pure extension of `src/dander/ingestion/capabilities.py`
following the DANDER-65/73 pattern, with zero `ConnectorAdapter` edits:

- Added `ConnectorOperation.UPDATE = "update"` member (with docstring entry) to the existing
  `StrEnum`.
- Added `@runtime_checkable class SupportsUpdate(Protocol)` with
  `update(self, endpoint: str, identity: Mapping[str, str], changes: Mapping[str, Any]) -> Mapping[str, Any]`.
  Docstring states the security invariant (no identity/changes/row values in exceptions, endpoint
  + operation only) and that credential access is out of scope for the Protocol (routes through
  the audited `AuthStrategy` on the concrete source).
- Added one entry `ConnectorOperation.UPDATE: SupportsUpdate` to `CAPABILITY_REGISTRY`, updated the
  registry's docstring history line, and the module docstring's capability-history paragraph.
- Exported `SupportsUpdate` from `src/dander/ingestion/__init__.py` (import + `__all__`, kept
  alphabetical).
- No not-found sentinel was added to `update`'s return type — followed the Design's recommended
  option (asymmetry with `SupportsGetSingleObject` is intentional; a missing/invalid target is
  exceptional and handled by the concrete source's transport/error path, never by embedding the
  identity in a raised message). Flagged in Design as a reviewer trade-off; no ticket AC asked for
  not-found modeling.
- `changes` is a partial `Mapping[str, Any]` (PATCH-style); full-vs-partial application semantics
  are left to the concrete source, per Design.
- No deviations from the Design's interfaces, registry wiring, or file list.

**Tests** (`tests/ingestion/test_capabilities_update.py`, new, mirrors
`test_capabilities_get_single_object.py`/`test_capabilities_create.py` layout):
- `FakeUpdateSource` — implements `update` over an in-memory table, merges `changes` into the
  stored record, returns the merged record (extract shape).
- `FakeIdentityOnlyUpdateSource` — implements `update` returning only `Mapping[str, str]` identity,
  proving that return form is valid per the Protocol's `Mapping[str, Any]` contract.
- `FakePlainSource` — implements only the mandatory `Source` contract (detection-negative case).
- Cases covered: detection-positive, detection-negative, `isinstance` structural check,
  successful update (merged-record return), identity-only return variant, and the
  unsupported-operation raise asserting the message contains source name + operation name but
  none of the identity/changes/secret marker values.

**Tooling results** (via `uv run`):
- `ruff check` — all checks passed (scoped to touched files).
- `ruff format --check` — 3 files already formatted.
- `mypy` (strict, scoped to touched files) — no issues found.
- `pytest tests/ingestion/` — full ingestion test suite (including the new module) passes, no
  network.

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — **PASS**

Reviewed `src/dander/ingestion/capabilities.py`, `src/dander/ingestion/__init__.py`, and
`tests/ingestion/test_capabilities_update.py` against the Acceptance Criteria,
`steering/01-security.md`, `steering/02-engineering.md`, and `steering/languages/python.md`.

**Acceptance criteria — all met:**

1. `@runtime_checkable class SupportsUpdate(Protocol)` is defined in `capabilities.py` with
   `update(self, endpoint: str, identity: Mapping[str, str], changes: Mapping[str, Any]) ->
   Mapping[str, Any]` — endpoint name, record identity, field changes, updated-record return.
2. `ConnectorOperation.UPDATE = "update"` added to the `StrEnum` (with an `Attributes:` docstring
   entry) and registered as `ConnectorOperation.UPDATE: SupportsUpdate` in `CAPABILITY_REGISTRY`.
   Detection is via the generic `ConnectorAdapter.__init__` `isinstance` loop — **zero**
   `ConnectorAdapter` edits, honoring DANDER-64's Open/Closed contract.
3. Typing is consistent with the family: `identity: Mapping[str, str]` matches
   `SupportsGetSingleObject.get_single_object` exactly; `changes`/return are `Mapping[str, Any]`,
   the `Source.extract()` record shape. The identity-only return (`Mapping[str, str]`) is
   assignable to the declared return type — verified by `FakeIdentityOnlyUpdateSource` type-checking
   clean under strict mypy.
4. `test_detection_negative_for_source_missing_protocol` and
   `test_unsupported_operation_raises_without_leaking_identity_or_change_values` prove a
   non-supporting source reports `supports(UPDATE) is False` and that `adapter.require(UPDATE)`
   raises `UnsupportedConnectorOperationError` (a `ValueError`, never `AttributeError`).
5. Security: the adapter's message is `source '<config.name>' does not support operation 'update'`
   — source + operation only. The Protocol docstring binds implementations to keep the identity
   value, any `changes` value, and any returned row value out of exceptions/logs, and explicitly
   states credential access routes through the audited `AuthStrategy` (`dander.security.base`), not
   through this method. The Protocol adds no credential surface. No credential-shaped literal
   anywhere in the diff (grepped); test fakes use `auth_strategy="none"` with a
   `.example.test` base URL and non-sensitive fixture rows; no `.env.example` change needed.
6. Six unit tests, no network: detection-positive, detection-negative, bare `isinstance` structural
   check, successful merged-record update, identity-only return variant, and the
   unsupported-operation raise with an explicit no-leak assertion over identity/changes/secret
   markers.
7. Conventions: `from __future__ import annotations`, `TYPE_CHECKING`-guarded `collections.abc`
   imports, full annotations, Google-style docstrings on the Protocol/method, module and registry
   docstrings updated with the DANDER-74 history line, `__init__.py` export added to both the import
   block and `__all__`. Design fidelity is exact — one enum member, one Protocol, one registry
   entry, one export, one test module; the documented not-found asymmetry with DANDER-65 is the
   Design's own recommended option (a missing target is exceptional for a write, and the alternative
   would risk tempting an implementation to embed the identity in a raised message).

**Tooling (re-run independently, not taken on trust):**
`uv run ruff check` (touched files + repo-wide `src tests`) — all checks passed;
`ruff format --check` — 3 files already formatted; `mypy --strict` on
`capabilities.py` + the new test module — no issues; `pytest tests/ingestion/` — 106 passed,
including the 6 new tests.

**Non-blocking observations (no action required for this ticket):**
- `tests/ingestion/test_capabilities.py::test_default_registry_is_empty_for_plain_source` still
  carries a DANDER-64-era name/docstring ("registry is empty") though `CAPABILITY_REGISTRY` now has
  six entries. The assertion itself remains valid and meaningful (its `_FakeSource` implements no
  optional capability), and the staleness predates this ticket — worth renaming when a later
  capability ticket touches that module.
- `src/dander/ingestion/README.md` describes write-back generically ("plus later opt-in write-back
  members") rather than naming `create`/`update`; acceptable, but the documentation agent may want
  to enumerate them once DANDER-73..76 all land.
- Repo-wide `mypy src tests` reports one pre-existing error at
  `tests/pipeline/test_field_operations.py:316` (DANDER-71 scope), unrelated to this diff.

Status set to `done`.
