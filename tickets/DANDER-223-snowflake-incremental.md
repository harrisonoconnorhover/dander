---
id: DANDER-223
title: Qualify RC29 Snowflake incremental merge
status: done
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-204, DANDER-216, DANDER-222]
created: 2026-08-17
---

## Context

Snowflake canonical correctness and bulk COPY pass on exact RC29, but the required provider-scale
matrix still lacks an incremental report. The next class reuses the accepted PostgreSQL workload:
a 3,000-row delta against a 300,000-row target, with half updates and half inserts.

## Acceptance Criteria

- [x] Extend the credential-free tested Snowflake scale harness without changing the bulk workload.
- [x] Bind exact RC29 to the 300,000-row seed and 3,000-row incremental delta.
- [x] Require a 100:1 target/delta ratio, exact updates/inserts, and cursor-regression rejection.
- [x] Bound COPY parts, runtime resources, automatic retries, provider objects, and resource lifetime.
- [x] Reserve no more than USD 0.50 and keep cost pending until provider-measured usage posts.
- [x] Merge the objective through protected review and pass exact-main CI before provider mutation.
- [x] Run the protected objective, clean all owned objects, and record the sanitized result.

## Design

Invoke the reviewed harness through RC29's source-free `dander qualification-run` entrypoint. A
disposable X-Small warehouse, database, role, and schema receive a 300,000-row seed, 1,500 updates,
1,500 inserts, and one rejected cursor regression. The writer uses bounded COPY parts only.

## Implementation Notes

- The seed and delta match the accepted local PostgreSQL class; no prior provider result transfers.
- COPY parts are capped at 50,000 rows and 16 MiB in a 2 CPU/512 MiB local container.
- One manual run is normal. A second is allowed only for a classified non-application failure;
  automatic retry remains disabled.
- Interactive auth must pass before owned resources. Cleanup starts by minute 30 and completes by
  minute 60; a later interactive blocker aborts and tears down immediately.
- The USD 0.50 reservation leaves USD 2.75 unreserved under the additional USD 10 authorization.
- PR #372 merged the objective as protected main `5bc3c6f`; exact-main run `32046930482`
  passed all five jobs before mutation.
- One exact-RC29 candidate applied 1,500 updates and 1,500 inserts to the 300,000-row seed in
  49.596 seconds with zero retries. Exact readback, cursor monotonicity, COPY telemetry, and
  throughput objectives passed.
- Cleanup left zero named database, warehouse, role, staging objects, and candidate containers
  within a 5.19-minute resource-lifetime upper bound. The full USD 0.50 remains held until
  Snowflake metering posts.

## Review

### 2026-08-17 — PASS

The result binds exact RC29 and protected objective `5bc3c6f`, records one permitted zero-retry
candidate, and proves exact cleanup. The normalized report remains `not_evaluated` only for delayed
provider cost; no support or broader Phase 8 completion claim is made.
