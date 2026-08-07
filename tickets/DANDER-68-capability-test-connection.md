---
id: DANDER-68
title: Add test_connection connector capability protocol
status: done
component: python
epic: connector-capabilities
depends_on: [DANDER-64]
created: 2026-08-04
---

## Reconciliation note (2026-08-05)

Satisfied on the current trunk (`teammate/main`, adopted as local `main`): `SupportsTestConnection`
/ `SourceCapabilities.test_connection` (with `ConnectionStatus`) in
`src/dander/ingestion/capabilities.py`. No further action needed. See `docs/decisions.md`,
"2026-08-05 — Optional source capabilities remain structural and read-only."

## Context

Before a scheduled run or during bootstrap, it is valuable to validate that a connector's
credentials and connectivity work **without pulling any data** — a fast preflight that resolves the
auth strategy (`steering/01-security.md`) and makes one cheap authenticated call. Not every API has
a natural no-data probe endpoint, so this is an **optional capability** rather than part of the
mandatory `Source` contract in `src/dander/ingestion/source.py`.

This ticket defines the `test_connection` capability Protocol and registers it with the DANDER-64
`ConnectorAdapter`/`ConnectorOperation` mechanism.

## Acceptance Criteria

- [ ] A `runtime_checkable` `Protocol` for `test_connection` (e.g. `SupportsTestConnection`) is
      defined in `src/dander/ingestion/capabilities.py` with a method that validates connectivity/
      credentials and returns a defined result (e.g. a boolean or a small typed status object) and
      raises/reports failure without leaking credential material.
- [ ] The Protocol is registered against `ConnectorOperation.TEST_CONNECTION` in the DANDER-64
      registry and detected by `ConnectorAdapter`.
- [ ] The contract explicitly pulls **no** source records (documented; the return type is a
      status, not a record stream).
- [ ] A source without the capability is reported unsupported and requesting it raises the
      DANDER-64 unsupported-operation error.
- [ ] No secret value or credential appears in the result, logs, or error messages
      (`steering/01-security.md`); credential access still routes through the audited auth strategy.
- [ ] Unit tests cover detection-positive, detection-negative, and both success and failure
      results via a fake source (no network, per `steering/02-engineering.md`).
- [ ] No steering violations (secrets, style, docs).

## Design

### Approach

This is a **Layer-1 capability extension** that plugs into the DANDER-64 mechanism, so it adds
exactly two things to `src/dander/ingestion/capabilities.py`: a `runtime_checkable` capability
`Protocol` (`SupportsTestConnection`) and one entry in the DANDER-64 registry mapping
`ConnectorOperation.TEST_CONNECTION → SupportsTestConnection`. No change to `ConnectorAdapter`
detection logic, no change to the mandatory `Source` ABC — this is composition/opt-in per
`steering/02-engineering.md`, exactly the seam DANDER-64's registry was built for.

The capability contract is a **source-level connectivity/credential preflight**, not an
endpoint-level operation. Unlike DANDER-65..67 (`get_single_object`/`get_deleted`/`count`, which
all take an endpoint name), `test_connection` validates that the *source's* resolved auth strategy
and base connectivity work with a single cheap authenticated call. The endpoint used for the probe
(if the concrete source needs one) is an implementation detail of that source, not part of the
Protocol signature — keeping the interface minimal (ISP) and honest about what "test the connection"
means. If a future source genuinely needs a caller-chosen probe endpoint, that is a follow-up
signature change, not something to speculatively build now.

**Return type — a typed status, never a raw bool and never a record stream.** The method returns a
frozen value object `ConnectionStatus` (a `@dataclass(frozen=True)` per the Python house rules for
internal value objects), carrying `ok: bool` and an optional non-sensitive `detail: str | None`.
This satisfies AC "returns a defined result" while making the no-data contract structural: the
return type is a scalar status object, categorically not an `Iterator[Mapping]`, so a
`test_connection` implementation *cannot* return source records. That is the enforcement mechanism
the AC asks for ("the return type is a status, not a record stream").

**Success vs. failure — expected outcomes are reported in the status; only unexpected faults may
raise.** A reachable-but-unauthorized or reachable-but-refused probe is a *normal* verdict of the
preflight, so it is represented as `ConnectionStatus(ok=False, detail=<non-secret reason>)` rather
than an exception — this is what lets a bootstrap/preflight caller branch on the result cleanly, and
it is what the unit tests exercise as the "failure result." The Protocol docstring makes two
security rules binding on every implementer (they are the contract, per `python.md`'s rule that the
Protocol carries the contract): (1) `detail` must be a human-readable, non-secret summary — never a
token, header, credential, response body, or row value; (2) any exception an implementation *does*
let propagate (genuine transport faults) must likewise carry no credential material. The Protocol
itself resolves no secrets; the concrete implementation on a source (e.g. a future
`DltRestSource.test_connection`) reuses that source's already-wired `AuthStrategy`
(`src/dander/security/base.py`), so credential access continues to route through the audited auth
strategy exactly as `steering/01-security.md` requires — this ticket adds no new credential path.

**Unsupported path is inherited, not reimplemented.** A source that does not implement
`SupportsTestConnection` is simply not detected by `ConnectorAdapter` (its `isinstance` check
against the registered Protocol fails), so `supports(ConnectorOperation.TEST_CONNECTION)` is
`False` and requesting the operation raises DANDER-64's `UnsupportedConnectorOperationError`. This
ticket writes none of that logic; it only adds the Protocol + registry entry so the existing
mechanism covers it. The unit tests assert the wiring end-to-end.

### Interfaces / classes

- **`ConnectionStatus`** — `@dataclass(frozen=True)` value object in `capabilities.py`.
  - `ok: bool` — whether credentials + connectivity validated.
  - `detail: str | None = None` — optional non-secret, human-readable summary (reason on failure,
    e.g. `"unauthorized"` / `"host unreachable"`; may be `None` on success). Docstring states the
    no-secret invariant explicitly.
- **`SupportsTestConnection`** — `@runtime_checkable` `typing.Protocol` in `capabilities.py`
  (matching the `SupportsCount`/`SupportsGetSingleObject` sibling naming from DANDER-65..67).
  - `def test_connection(self) -> ConnectionStatus: ...`
  - Docstring is the binding contract: pulls **no** source records; returns a status object;
    represents expected reachable-but-refused/unauthorized outcomes as `ok=False`; must not place
    any secret/credential/row value in the returned `detail`, in logs, or in any raised exception;
    concrete implementations resolve credentials only through the source's audited `AuthStrategy`.
- **Registry entry (DANDER-64-owned map)** — add
  `ConnectorOperation.TEST_CONNECTION: SupportsTestConnection` to the single capability-registry
  mapping DANDER-64 defines in `capabilities.py`. No edit to `ConnectorAdapter` itself.

### Files to touch / create

- `src/dander/ingestion/capabilities.py` *(created by DANDER-64; this ticket appends to it)* — add
  `ConnectionStatus`, `SupportsTestConnection`, and the one registry entry. Keep the
  `ConnectorOperation.TEST_CONNECTION` member (already required by DANDER-64's AC) as the key.
- `src/dander/ingestion/__init__.py` — export `ConnectionStatus` and `SupportsTestConnection`
  alongside the other public ingestion symbols (kept alphabetical in `__all__`), so callers and the
  metadata/bootstrap layers can import the capability types without reaching into the module.
- `tests/ingestion/test_capabilities.py` *(shared capability-test module introduced by DANDER-64;
  add cases here, or a focused `test_capability_test_connection.py` if that file grows unwieldy)* —
  add the DANDER-68 cases.

### Test seams

All unit, no network (`steering/02-engineering.md`). Use in-file fakes:
- `_FakeConnectableSource(Source)` implementing `test_connection()` returning
  `ConnectionStatus(ok=True)` — detection-positive + success result.
- A second fake (or a constructor flag on the same fake) returning
  `ConnectionStatus(ok=False, detail="unauthorized")` — failure result; assert `ok is False` and
  that `detail` contains no secret-shaped content.
- A plain `Source` fake implementing only `extract`/`discover` — detection-negative: assert
  `ConnectorAdapter(...).supports(ConnectorOperation.TEST_CONNECTION)` is `False` and that
  requesting the operation raises DANDER-64's `UnsupportedConnectorOperationError` naming the
  source + operation (not `AttributeError`).
- Assert `isinstance(fake, SupportsTestConnection)` is `True`/`False` respectively, proving the
  `runtime_checkable` Protocol drives detection.

The fakes construct with a minimal valid `SourceConfig`; no real `AuthStrategy` or secret store is
needed because the Protocol layer resolves nothing — credential wiring belongs to the concrete
source implementation, which is out of scope for this Protocol-definition ticket.

### Trade-offs

- **Typed status object vs. bare `bool`.** The AC permits either; the status object is chosen so
  the no-record-stream contract is structurally enforced and so a caller gets a non-secret reason on
  failure. Cost: one small dataclass. Worth it — a bare `bool` throws away the failure reason and
  invites callers to pass credential detail through some side channel instead.
- **Report expected failure vs. raise.** Returning `ok=False` for reachable-but-refused makes the
  preflight branchable and keeps exception handling for genuine faults only; the AC's "raises/reports
  failure" phrasing explicitly allows this and it is the more useful shape for a bootstrap gate.
- **No endpoint parameter.** Diverges deliberately from the sibling endpoint-scoped capabilities to
  keep "test the connection" a source-level, minimal-surface probe. Flagged as a conscious choice;
  revisit only if a concrete source proves it needs a caller-chosen probe target.

> **Dependency note:** DANDER-64 (`ConnectorOperation` `StrEnum`, `ConnectorAdapter`, the capability
> registry map, and `UnsupportedConnectorOperationError`) must land first — this ticket only adds a
> Protocol + registry entry + tests against that contract and introduces no new credential path.
>
> **Under-specified AC flagged:** the ticket says the method "validates connectivity/credentials"
> but does not fix whether the probe is source-level or endpoint-level. This design resolves it as
> source-level with no endpoint parameter (rationale above); if the epic owner intends an
> endpoint-scoped probe to mirror DANDER-65..67, the signature becomes
> `test_connection(self, endpoint: str) -> ConnectionStatus` and the fakes/tests adjust accordingly.

## Implementation Notes

Implemented exactly per Design, no deviations.

- **`src/dander/ingestion/capabilities.py`**: added `ConnectionStatus` (`@dataclass(frozen=True,
  slots=True)`, `ok: bool`, `detail: str | None = None`), the `@runtime_checkable`
  `SupportsTestConnection` Protocol (`def test_connection(self) -> ConnectionStatus`, no `endpoint`
  parameter — source-level probe as designed), and one new entry
  `ConnectorOperation.TEST_CONNECTION: SupportsTestConnection` in `CAPABILITY_REGISTRY`. No change
  to `ConnectorAdapter` itself, per the design's "no reimplementation" note — the existing
  `isinstance`-based detection and `require()` unsupported-op error apply for free. Module
  docstring updated with a DANDER-68 scope note (mirrors the existing DANDER-65/66/67 notes) and
  the DANDER-65..67 lineage sentence extended to mention DANDER-68.
- **`src/dander/ingestion/__init__.py`**: exported `ConnectionStatus` and `SupportsTestConnection`
  from the package, inserted alphabetically among the existing capability exports in both the
  import block and `__all__` (matching the pre-existing, not-fully-alphabetical ordering already
  present in this file, e.g. `CursorPagination` before `ConnectorAdapter`).
- **`tests/ingestion/test_capabilities_test_connection.py`** (new file, matching the
  `test_capabilities_count.py` / `test_capabilities_get_*.py` one-file-per-capability convention):
  - `FakeConnectableSource` (`test_connection` → `ConnectionStatus(ok=True)`),
    `FakeUnauthorizedSource` (`test_connection` → `ConnectionStatus(ok=False,
    detail="unauthorized")`), and `FakePlainSource` (mandatory contract only).
  - Detection-positive (`adapter.supports(...)` is `True`, `isinstance(..., SupportsTestConnection)`
    is `True`) and detection-negative (both `False`) cases.
  - Unsupported-operation case asserts `UnsupportedConnectorOperationError` (not `AttributeError`)
    naming the source (`fake_plain`) and operation (`test_connection`), and that no secret-shaped
    marker (`token`/`secret`/`key`/`password`/`Authorization`) appears in the message.
  - Success-result and failure-result cases assert the returned `ConnectionStatus` fields directly
    and that the failure `detail` contains no secret-shaped marker.
  - `ConnectionStatus` frozen-value-object check (`AttributeError` on mutation) and a
    "pulls no source records" check (`ConnectionStatus` has no `__iter__`) to make the structural
    no-record-stream contract explicit in tests, not just in the type signature.
- No new credential path introduced; the Protocol resolves nothing, per Design.

**Toolchain (run via `uv run` at repo root):** `ruff check .` — all checks passed. `ruff format
--check` on changed files — already formatted. `mypy src/` — success, no issues found in 73 source
files. `pytest` (full repo suite) — all 670 tests passed, including the 8 new
`test_capabilities_test_connection.py` cases and the pre-existing `tests/ingestion/` suite
(unchanged, still green).

## Review Log

_Append-only. PR-Review adds entries below._

### 2026-08-04 — **PASS**

Reviewed `src/dander/ingestion/capabilities.py`, `src/dander/ingestion/__init__.py`,
`src/dander/ingestion/README.md`, and `tests/ingestion/test_capabilities_test_connection.py`
against the acceptance criteria, the Design, `steering/01-security.md`,
`steering/02-engineering.md`, and `steering/languages/python.md`.

**Acceptance criteria — all met:**
1. `SupportsTestConnection` is a `@runtime_checkable` `typing.Protocol` in `capabilities.py`
   (`capabilities.py:316-355`) with `def test_connection(self) -> ConnectionStatus`. The result is
   the frozen value object `ConnectionStatus` (`ok: bool`, `detail: str | None`,
   `capabilities.py:295-313`), whose docstring binds the no-secret invariant on `detail`.
2. Registered as `ConnectorOperation.TEST_CONNECTION: SupportsTestConnection` in
   `CAPABILITY_REGISTRY` (`capabilities.py:362`); `ConnectorAdapter` is unchanged, so detection is
   the inherited `isinstance` pass — verified green by the detection tests.
3. The no-record contract is both documented ("Pulls **no** source records") and structural: the
   return annotation is a scalar dataclass, categorically not an `Iterator[Mapping]`. Module
   docstring carries the DANDER-68 scope note (`capabilities.py:42-48`).
4. Detection-negative source raises `UnsupportedConnectorOperationError` (not `AttributeError`)
   naming source + operation — `test_unsupported_operation_raises_without_leaking_secret_or_row_values`.
5. Security: no credential literal anywhere in the diff (grepped for credential-shaped literals —
   no hits); no new secret key needed, `.env.example` correctly untouched; no logging added; the
   Protocol resolves nothing itself and the docstring binds implementers to the source's already
   audited `AuthStrategy`, so no new credential path is introduced. Fakes use
   `auth_strategy="none"` with a `.example.test` base URL — no real or secret-shaped fixture data.
6. Tests: 8 unit cases, no network — detection-positive, detection-negative, `isinstance` proof of
   the `runtime_checkable` drive, unsupported-op error, success result, failure result
   (`ok=False, detail="unauthorized"` with secret-marker assertions), frozen-value-object check,
   and an explicit "no record stream" check.
7. Steering/conventions: `uv run ruff check .` clean; `ruff format --check` on the three changed
   files clean; `uv run mypy src/` — success, 73 files; full `pytest` suite — **670 passed**
   (matches Implementation Notes; the CLI test failures visible without `NO_COLOR=1` are an ANSI
   colour artifact of a non-TTY capture, not a code regression). Google-style docstrings on every
   public module/class/method; `@dataclass(frozen=True, slots=True)` matches the sibling
   `CountResult` house pattern for internal value objects.

**Design fidelity:** implemented exactly as designed — Protocol + one registry entry + exports +
tests, zero edits to `ConnectorAdapter` or the mandatory `Source` ABC (Open/Closed, ISP, composition
over inheritance per `steering/02-engineering.md`). The source-level (no `endpoint` parameter) probe
deviation from the DANDER-65..67 siblings is the deliberate, documented Design choice and is
restated in the Protocol docstring, so callers cannot misread it.

**Non-blocking observations (no action required for this ticket):**
- `tests/ingestion/test_capabilities.py:118-122` is still named
  `test_default_registry_is_empty_for_plain_source` with a docstring asserting the registry "is
  empty in DANDER-64". The assertion itself (a plain source supports nothing) remains correct and
  green, but the name/docstring went stale once DANDER-65 populated the registry — pre-existing to
  this ticket, worth a one-line rename in a future capability ticket.
- `src/dander/ingestion/__init__.py` `__all__` keeps the pre-existing `CursorPagination` /
  `ConnectorAdapter` mis-ordering; the new exports were inserted consistently with it, as the
  Implementation Notes state. Not enforced by Ruff here.
