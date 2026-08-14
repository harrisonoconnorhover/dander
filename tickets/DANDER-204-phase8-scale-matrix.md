---
id: DANDER-204
title: Execute the approved Phase 8 scale matrix
status: open
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-200, DANDER-202]
created: 2026-08-13
---

## Context

Historical PostgreSQL, Snowflake, and Redshift reports are correctness or regression evidence, not
the required exact-candidate provider scale and cost qualification. BigQuery lacks a normalized
scale report.

## Acceptance Criteria

- [ ] Approved provider-specific SLOs and paid ceilings exist before mutation.
- [ ] Correctness, bounded-memory, bulk, incremental, concurrency, transform, failure, crossover,
  and cost reports cover every first-class warehouse and launcher.
- [ ] Every report records exact artifact, provider, workload, job, cost, cleanup, and objective
  evidence without credentials or row data.
- [ ] Optimization occurs only for a measured failed SLO and retains canonical equality.

## Implementation Notes

- Exact private RC22 passed the local PostgreSQL bounded-memory and four-pipeline concurrency
  objective sets on PostgreSQL 15.18 over TLS. The externally enforced 256 MiB run processed
  2.7248 GB logical input with 176,734,208 bytes peak RSS and left no staging relations.
- The initial 192 MiB bounded-memory attempt exceeded the approved 80% RSS threshold without an
  OOM. It remains in the attempts ledger; the proportional 256 MiB retry is the passing report.
- Exact RC22 also passed the pre-approved local bulk class with 500,000 narrow and 200,000 wide
  COPY rows, and the incremental class with a 3,000-row delta against a 300,000-row target. Both
  left zero staging relations and removed their disposable TLS PostgreSQL schemas.
- The exact-candidate correctness fixture also matched its approved normalized SHA-256 before and
  after replay, then removed its disposable schema and staging relations.
- Exact RC22's transform class passed scan, join, aggregation, incremental merge, and 21 generic
  assertion executions over 100,000 facts and 100 dimensions. The initial harness-only seed
  failure remains in the attempts record; it did not execute candidate transform code.
- The PostgreSQL-specific failure class passed bounded pool exhaustion, terminated-connection
  replacement, recovered state operations, warehouse cancellation rollback, and cleanup. The
  connector and launcher failure cases remain assigned to their own profile gates.
- Seven PostgreSQL classes now pass. Hosted cost, the other
  warehouses, and every first-class launcher remain open. Crossover cannot pass on RC22 because
  its PostgreSQL factory exposes COPY only and has no bounded direct transport to compare.
