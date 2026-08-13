---
id: DANDER-122
title: Execute the approved Phase 8 scale matrix
status: open
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-118, DANDER-120]
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
