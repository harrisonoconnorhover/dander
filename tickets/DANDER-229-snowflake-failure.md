---
id: DANDER-229
title: Qualify RC29 Snowflake failure behavior
status: done
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
- Harness-correction PR #386 merged as protected main `6bd496d`; exact-main run `32389095161`
  passed all five jobs and covers invalid OAuth rejection at both construction and connection.
- One manual run is normal. A second is allowed only for a classified non-application failure;
  automatic retry remains disabled.
- Interactive auth must pass before owned resources. Cleanup starts by minute 15 and completes by
  minute 30; a later interactive blocker aborts and tears down immediately.
- The USD 0.50 reservation leaves USD 1.25 unreserved under the additional USD 10 authorization.
- Launcher retry, process termination, and state/catalog outage remain separate profile gates.

## Review

### 2026-08-20 — HISTORICAL PROTECTED HARNESS GAP

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
requires no product or candidate change. At that point DANDER-229 remained open because the
protected harness had not completed the final two probes.

### 2026-08-20 — PASSED ON CLASSIFIED NON-APPLICATION RERUN

PR #386 corrected only the protected harness rejection boundary; RC29, its digest, objective,
provider configuration, retry policy, and resource bounds remained unchanged. Exact-main run
`32389095161` passed all five protected jobs before the one allowed classified rerun. Interactive
ACCOUNTADMIN and restricted-role OAuth both passed with the approved private coordinates, and
Snowflake account-usage metering was visible before the disposable database and warehouse existed.

The source-free RC29 container exited zero after all four probes passed: closed-connection recovery,
invalid-credential fail-closed rejection, stale-fence rejection, and rollback/readback after the
one-second warehouse timeout. No retry ran. Cleanup began 275.118 seconds after the first owned
role, Snowflake inventories were empty after 279.275 seconds, and the container and temporary OAuth
tokens were absent after 290.759 seconds. The USD 0.50 conservative bound remains held pending
delayed provider billing; reconciling that cost does not require another workload run. Sanitized
evidence is in `docs/evidence/phase8/2026-08-20/snowflake-rc29-failure-execution.json`.
