---
id: DANDER-223
title: Qualify RC29 Snowflake incremental merge
status: in_progress
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
- [ ] Merge the objective through protected review and pass exact-main CI before provider mutation.
- [ ] Run the protected objective, clean all owned objects, and record the sanitized result.

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
