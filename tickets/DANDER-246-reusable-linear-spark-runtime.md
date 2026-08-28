---
id: DANDER-246
title: Execute one reusable linear graph through Managed Spark
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-245]
created: 2026-08-27
---

## Context

DANDER-243 qualified a fixed Spark artifact pair, and DANDER-244/245 moved physical-plan and
execution-plan compilation into Control. The Spark driver still generated qualification rows and
hard-coded its BigQuery output. This slice replaces that fixture with one graph-driven runtime.

## Acceptance Criteria

- [x] Execute the existing Greenhouse source-to-transform-to-BigQuery graph through the static
  two-stage distributed physical plan without changing its fused-container result.
- [x] Load one content-addressed canonical graph configuration and explicit source-relation binding
  from the existing Control configuration reference.
- [x] Bind the configuration's canonical graph SHA to the immutable Control execution-plan command
  and require its physical operators to match the graph.
- [x] Support type-preserving direct projections and renames, one round-robin two-partition GCS
  exchange, and an unpartitioned BigQuery replace target.
- [x] Fail closed for other graph shapes, joins, operations, casts, partial mappings, alternate
  writer modes, cross-project relations, mutable configuration, and changed executor shape.
- [x] Preserve byte-identical driver/image identity, canonical Control results, verified exchange
  cleanup, and the existing single-container graph execution path.
- [ ] Publish one immutable main runtime image and one immutable Spark image/driver from the same
  exact-main commit, with their digests recorded.
- [ ] Run exactly one fused Fargate cell followed by one Dataproc cell against the same raw BigQuery
  snapshot, prove exact output parity, capture Control evidence, and clean disposable resources.

## Boundaries

- No joins, graph operations, arbitrary Spark operators, dynamic partitions or sizing, dynamic
  allocation, autotuning, Kubernetes, cluster management, or new reconciler.
- No extra acceptance cells, soak, status-only PR, evidence framework, release, C27, RC32, or
  Phase 8 work.
