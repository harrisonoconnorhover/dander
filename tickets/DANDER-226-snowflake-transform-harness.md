---
id: DANDER-226
title: Add Snowflake transform qualification harness
status: done
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-204, DANDER-214, DANDER-225]
created: 2026-08-17
---

## Context

Exact RC29 has protected Snowflake bulk, incremental, and concurrency harnesses. The next
dependency-ordered class needs a credential-free transform harness before a separate objective can
bind live provider names, budget, candidate identity, and execution rails.

## Acceptance Criteria

- [x] Bind a deterministic 100,000-row fact and 100-row dimension workload.
- [x] Exercise scan, join, aggregation, incremental merge, and generic tests through the native
  Snowflake transform runner.
- [x] Seed source relations through bounded COPY and report load and transform time separately.
- [x] Require exact initial and incremental readback, ownership checks, and zero staging residue.
- [x] Remove both disposable schemas on success or failure and verify cleanup before reporting.
- [x] Emit the normalized Phase 8 report with provider cost pending until measured usage posts.
- [x] Cover configuration, approval drift, fixture compilation, report semantics, and sanitized CLI
  failure without provider credentials.

## Design

Extend the existing Snowflake scale harness with one `transform` class. COPY creates the bounded
raw fact and dimension relations. Four portable models then build through the production
`SnowflakeTransformRunner`; a two-row source delta proves one update and one insert through the
incremental path. Generic tests, exact readback, fencing telemetry, provider query ids, and cleanup
feed the normalized report.

## Implementation Notes

- The harness adds no live objective, provider names, secrets, cloud mutation, or RC29 change.
- A later focused objective must bind the protected harness and reserve its own cost ceiling before
  any Snowflake resource is created.
- COPY parts default to 50,000 rows and 16 MiB; the objective may only select reviewed bounds.
- Transform temporary tables and COPY staging objects are both included in residue checks.

## Review

### 2026-08-17 — PASS

The harness selects the production Snowflake writer and transform runner, keeps provider imports at
the script boundary, fails closed on approval drift or malformed readback, and sanitizes provider
errors. It makes no live or support claim.
