---
id: DANDER-227
title: Qualify RC29 Snowflake transform scale
status: done
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-204, DANDER-214, DANDER-226]
created: 2026-08-17
---

## Context

Exact RC29 has protected Snowflake bulk, incremental, and concurrency results. The next required
provider-scale class runs portable scan, join, aggregation, incremental merge, and generic tests
through the protected native Snowflake transform runner.

## Acceptance Criteria

- [x] Protect the credential-free tested Snowflake transform harness without changing RC29.
- [x] Bind exact RC29 and the protected harness to 100,000 fact and 100 dimension rows.
- [x] Require exact scan, join, aggregation, incremental, and generic-test results.
- [x] Bound COPY parts, runtime resources, automatic retries, provider objects, and resource lifetime.
- [x] Reserve no more than USD 0.50 and keep cost pending until provider-measured usage posts.
- [x] Merge the objective through protected review and pass exact-main CI before provider mutation.
- [x] Run the protected objective, clean all owned objects, and record the sanitized result.

## Design

Invoke the protected harness through RC29's source-free `dander qualification-run` entrypoint. A
disposable X-Small warehouse, database, role, source schema, and target schema receive 100,000 fact
rows and 100 dimension rows through bounded COPY. Four portable models then prove exact scan, join,
aggregation, two-row incremental merge, generic tests, fencing telemetry, and cleanup.

## Implementation Notes

- Harness PR #377 merged as protected main `5947d792`; exact-main run `32057603919` passed all five
  jobs. No application or candidate change transfers.
- COPY parts are capped at 50,000 rows and 16 MiB in a 2 CPU/512 MiB local container.
- One manual run is normal. A second is allowed only for a classified non-application failure;
  automatic retry remains disabled.
- Interactive auth must pass before owned resources. Cleanup starts by minute 30 and completes by
  minute 60; a later interactive blocker aborts and tears down immediately.
- The USD 0.50 reservation leaves USD 1.75 unreserved under the additional USD 10 authorization.
- Objective PR #378 merged as protected main `bb06aa8`; exact-main run `32059122714` passed all
  five jobs before provider mutation.
- Two bounded interactive-auth preflights consumed no candidate attempt. The only owned object was
  the minimum runtime role, which was removed and verified absent inside 47.812 seconds.
- One exact-RC29 candidate passed all six functional objectives with zero retries. Cleanup verified
  zero database, warehouse, role, staging object, or candidate container inside 125.769 seconds.

## Review

Complete. The sanitized report and operator record are in
`docs/evidence/phase8/2026-08-17/snowflake-rc29-transform-attempt.json` and
`docs/evidence/phase8/2026-08-17/snowflake-rc29-transform-execution.json`. Provider cost remains
pending under the held USD 0.50 bound; no support promotion follows.
