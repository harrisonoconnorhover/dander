---
id: DANDER-225
title: Qualify RC29 Snowflake concurrent pipelines
status: done
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-204, DANDER-216, DANDER-223]
created: 2026-08-17
---

## Context

Snowflake canonical correctness, bulk COPY, and incremental merge pass functionally on exact RC29,
but the required provider-scale matrix still lacks a concurrency report. The next class runs four
independent COPY targets plus controlled contention on one target.

## Acceptance Criteria

- [x] Protect a credential-free tested Snowflake concurrency harness without changing RC29.
- [x] Bind exact RC29 and the protected harness to four independent 5,000-row pipelines.
- [x] Require exact readback, controlled two-claim contention, and stale-publication rejection.
- [x] Bound COPY parts, runtime resources, automatic retries, provider objects, and resource lifetime.
- [x] Reserve no more than USD 0.50 and keep cost pending until provider-measured usage posts.
- [x] Merge the objective through protected review and pass exact-main CI before provider mutation.
- [x] Run the protected objective, clean all owned objects, and record the sanitized result.

## Design

Invoke the protected harness through RC29's source-free `dander qualification-run` entrypoint. A
disposable X-Small warehouse, database, role, and schema receive four independent 5,000-row COPY
targets. Two controlled claims then contend on one target and the stale publication must write zero
rows.

## Implementation Notes

- Harness PR #374 merged as protected main `606e19c`; exact-main run `32051585864` passed all five
  jobs. No application or candidate change transfers.
- COPY parts are capped at 5,000 rows and 16 MiB in a 2 CPU/512 MiB local container.
- One manual run is normal. A second is allowed only for a classified non-application failure;
  automatic retry remains disabled.
- Interactive auth must pass before owned resources. Cleanup starts by minute 30 and completes by
  minute 60; a later interactive blocker aborts and tears down immediately.
- The USD 0.50 reservation leaves USD 2.25 unreserved under the additional USD 10 authorization.
- PR #375 merged the objective as protected main `da1b536`; exact-main run `32052812102` passed
  all five jobs before mutation.
- Operator-only preflights that never reached the harness preserved zero candidate attempts. The
  one resource-bearing preflight was cleaned immediately after an interactive-auth timeout.
- One exact-RC29 candidate completed four 5,000-row pipelines in 39.407 seconds with zero retries,
  rejected one stale publication, and left zero staging objects.
- Cleanup left zero named database, warehouse, role, candidate container, or provider process
  within 4.29 minutes. The full USD 0.50 remains held until Snowflake metering posts.

## Review

### 2026-08-17 — PASS

The result binds exact RC29, protected objective `da1b536`, and protected harness `606e19c`. All
five non-cost objectives passed and cleanup is exact. The normalized report remains
`not_evaluated` only for delayed provider cost; no support or broader Phase 8 completion claim is
made.
