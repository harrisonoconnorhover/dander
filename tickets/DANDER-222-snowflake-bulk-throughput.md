---
id: DANDER-222
title: Qualify RC29 Snowflake bulk throughput
status: in-review
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-204, DANDER-216]
created: 2026-08-17
---

## Context

Snowflake has exact-candidate canonical correctness evidence but no normalized Phase 8 scale
report. The next provider-specific class is the same narrow/wide bulk workload accepted for local
PostgreSQL, executed through RC29's source-free qualification entrypoint.

## Acceptance Criteria

- [x] Add a credential-free tested harness that streams the bounded workload through Snowflake COPY.
- [x] Bind the exact RC29 commit and image digest to 500,000 narrow and 200,000 wide rows.
- [x] Record exact readback, throughput, peak RSS, query identity, staging cleanup, and schema cleanup.
- [x] Reserve no more than USD 0.50 and keep cost pending until provider-measured usage posts.
- [ ] Merge the objective through protected review and pass exact-main CI before provider mutation.
- [ ] Run the protected objective, clean all owned objects, and record the sanitized result.

## Design

Mount the reviewed harness and objective read-only into the immutable RC29 image, invoke them through
`dander qualification-run`, and use a disposable X-Small warehouse, database, role, and schema.
The writer receives a generator and emits bounded Parquet parts; direct insertion is disabled.

## Implementation Notes

- The workload matches the prior 500,000-row/32-byte narrow and 200,000-row/1,024-byte wide shape.
- Parts are bounded to 50,000 rows and 16 MiB; the candidate container is bounded to 2 CPU/512 MiB.
- One manual run is normal. A second is allowed only after a non-application failure classification;
  automatic retries remain disabled.
- The USD 0.50 reservation leaves USD 3.25 unreserved under the additional USD 10 authorization.

## Review Log

Pending protected review.
