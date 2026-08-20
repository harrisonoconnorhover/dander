---
id: DANDER-229
title: Qualify RC29 Snowflake failure behavior
status: in_progress
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-204, DANDER-214, DANDER-228]
created: 2026-08-17
---

## Context

Exact RC29 has protected functional Snowflake bulk, incremental, concurrency, and transform
results. The next required provider class proves bounded recovery and fail-closed behavior across
the native Snowflake connector, session, and target-fence boundaries.

## Acceptance Criteria

- [x] Protect the credential-free tested Snowflake failure harness without changing RC29.
- [x] Bind exact RC29 and the protected harness to four bounded provider-failure probes.
- [x] Require closed-connection recovery, invalid-credential rejection, stale-fence rejection, and
  rollback after a warehouse statement timeout.
- [x] Bound runtime resources, automatic retries, provider objects, and resource lifetime.
- [x] Reserve no more than USD 0.50 and keep cost pending until provider-measured usage posts.
- [x] Merge the objective through protected review and pass exact-main CI before provider mutation.
- [x] Run the protected objective, clean all owned objects, and record the sanitized result.

## Design

Invoke the protected harness through RC29's source-free `dander qualification-run` entrypoint. One
disposable X-Small warehouse, database, role, and UUID-scoped schema exercise a closed connection,
an invalid in-memory OAuth credential, two ordered fencing claims, and a one-second statement
timeout followed by explicit rollback and fresh-connection readback.

## Implementation Notes

- Harness PR #380 merged as protected main `2e45ca4`; exact-main run `32065584378` passed all five
  jobs. No application or candidate change transfers.
- One manual run is normal. A second is allowed only for a classified non-application failure;
  automatic retry remains disabled.
- Interactive auth must pass before owned resources. Cleanup starts by minute 15 and completes by
  minute 30; a later interactive blocker aborts and tears down immediately.
- The USD 0.50 reservation leaves USD 1.25 unreserved under the additional USD 10 authorization.
- Launcher retry, process termination, and state/catalog outage remain separate profile gates.

## Review

### 2026-08-20 — BLOCKED BY PROTECTED HARNESS GAP

PR #381 merged the objective as `8550ef1`; exact-main run `32067021084` passed all five jobs. The
one permitted exact-RC29 candidate then reached Snowflake, created its disposable schema, passed
closed-connection recovery, and proved that the invalid in-memory OAuth credential failed closed.
Snowflake rejected that credential while the provider registry was constructing the runtime, but
the protected harness catches rejection only after construction returns. The intended rejection
therefore escaped and the candidate produced its sanitized failure record before the stale-fence
and timeout probes.

No retry ran. Cleanup began 33.94 seconds after the first owned resource and completed with the
database, warehouse, role, disposable schema, and candidate container absent after 36.278 seconds.
The full USD 0.50 bound remains held pending delayed provider metering. Exact evidence is recorded
in `docs/evidence/phase8/2026-08-20/snowflake-rc29-failure-execution.json`. RC29 failed closed and
requires no product or candidate change; DANDER-229 remains open because the protected harness did
not complete the final two probes.
