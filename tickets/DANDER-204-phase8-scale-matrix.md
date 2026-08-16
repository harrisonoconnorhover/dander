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
- Seven PostgreSQL classes pass on exact protected RC22. RC22 cannot satisfy crossover because its
  PostgreSQL factory exposes COPY only; those reports remain accepted baseline evidence.
- Private arm64 RC23 observed exact COPY/DIRECT equality, selected-transport telemetry, cleanup,
  and USD 0 local cost across five sizes and five repetitions. Completion review invalidated its
  10-row/1,400-byte recommendation because it omitted writer-counted field-name bytes. The corrected
  harness derives 1,490 bytes; RC23's threshold objective remains invalid.
- Private multi-platform RC24 passed the committed corrected crossover objective against disposable
  TLS PostgreSQL 15.18. Both transports produced equal rows, but DIRECT lost at the first sampled
  size, so no contiguous DIRECT-winning prefix exists and the measured threshold remains disabled
  at zero. All seven objectives passed with exact cleanup and USD 0 local cost. The later AWS-native
  corrections required private RC27, so no RC24 benchmark transfers. Applicable RC27 reruns,
  hosted cost, other warehouses, and every first-class launcher remain open.
- The Kubernetes portable launcher passed normalized correctness, bulk, incremental, transform,
  and PostgreSQL-specific failure Jobs on kind 1.32.2 under its reviewed deadline, retry, CPU, and
  memory controls. Remaining launcher classes, hosted scale/cost, and soak stay open.
- AWS access is restored. The exact RC22 AWS-native correctness objectives and USD 3 allocation are
  committed before mutation. The first disposable data-plane plan applied and cleaned up exactly,
  but read-only candidate inspection found RC22 lacks the selected AWS deployment before a Fargate
  plan or execution. Private RC27 packages the reviewed runtime-overlay, Fargate identity, explicit
  Redshift staging-role grant, and Serverless startup corrections and passes candidate inspection;
  the exact RC27 manual/replay correctness result now passes with duplicate-free canonical output
  and exact cleanup. Provider-measured cost and AWS scale remain open.
- The final-candidate Kubernetes rerun has fresh RC27-bound objective files for the five accepted
  launcher-scale classes. Protected review and exact-main CI remain mandatory before its USD 0
  disposable kind execution.
