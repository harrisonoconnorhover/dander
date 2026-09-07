---
id: DANDER-284
title: Preserve BigQuery graph and ingestion job measurements
status: done
component: python
depends_on: [DANDER-283]
created: 2026-09-07
---

## Context

DANDER-283 completed real BigQuery work but reported an empty operation list. The legacy graph
runner and SCD1 ingestion writer discarded completed job statistics, so their zero byte totals
could not support cost comparisons.

## Acceptance Criteria

- [x] Return completed graph query and SCD1 load/query statistics through existing telemetry.
- [x] Reuse the BigQuery normalizer and preserve query submission, fencing, cleanup, and retry policy.
- [x] Drain ingestion measurements once and isolate successive graph builds.
- [x] Verify counters after completion, retry counts, and failure behavior with focused tests.
- [x] Merge through protected checks and verify exact-main CI.

## Scope and Verification

A shared BigQuery job collector uses the existing normalizer and mutation retry helper. It records
successful job IDs, row counts, bytes processed/billed, elapsed time, and retry counts; it never
records SQL or source rows. SCD1-derived incremental writes inherit the same collection. Graph
publication records its submitted parent jobs once, without fetching or adding child-job totals.

The 110 focused provider, graph, writer, retry, executor, runtime, and completion-contract tests
passed. Ruff lint/format, strict typing across 494 files, and diff checks passed.
Reading DANDER-283's existing completed comparison job preserved its actual 4,712 bytes processed
and 20,971,520 bytes billed. No new query or worker execution was submitted for that readback.

These are job measurements, not a complete invoice. Other legacy write modes, non-graph model
execution, state/catalog operations, retained infrastructure, storage, discounts, and capacity
pricing remain outside this collector's coverage. The historical DANDER-283 results stay unchanged.

The original graph's three completed parent jobs were also inspected directly. Their measured
billed bytes were 10,485,760 for staging, zero for empty target creation, and 41,943,040 for the
transactional publish. The normalizer preserved each value without adding child-job totals.
[PR #534](https://github.com/harrisonoconnorhover/dander/pull/534) merged as `1aa1f60` after all six
protected checks passed. All six
[exact-main checks](https://github.com/harrisonoconnorhover/dander/actions/runs/34164220943)
passed at `1aa1f6024497e6f94d75d3cf0adcff83910c395a`.
