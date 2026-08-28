---
id: DANDER-251
title: Execute one static multistage linear graph through Managed Spark
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-250]
created: 2026-08-28
---

## Context

Control could compile and execute one source-to-transform chain and one bounded two-source join.
This slice proves that the same immutable planning, sizing, placement, backend, and reconciliation
contracts can execute more than one sequential transform stage without introducing a general DAG
engine or changing pipeline selection.

## Acceptance Criteria

- [x] Compile exactly one source-to-two-transform-to-target chain into three static stages and two
      round-robin object-store exchanges using the existing selected partition count.
- [x] Produce the same physical plan and revision regardless of graph node or edge declaration
      order, while preserving fused-container compilation for the same graph.
- [x] Execute type-preserving direct mappings across both materialized exchanges and one BigQuery
      replace target through a separate versioned Spark configuration contract.
- [x] Bind graph, physical-plan, executor, and configuration identities before provider data work.
- [x] Clean both exchange prefixes after success or failure and emit canonical Control results with
      additive multistage Spark evidence.
- [x] Fail closed for another topology, joins, operations, casts, partial mappings, changed plans,
      alternate writers, dynamic allocation, and changed executor counts.
- [x] Preserve existing one-transform, join, fused-container, API, scheduler, sizing, placement,
      retry, cancellation, and restart-recovery behavior.
- [ ] Publish one immutable main runtime image and Spark driver/image pair from repaired exact main.
- [ ] Run exactly one fused Fargate cell and one Dataproc cell against the same raw snapshot, prove
      exact output parity, capture Control evidence, and clean disposable resources.

## Boundaries

- No arbitrary DAG planner, additional graph shape, operation runtime, or generalized exchange
  framework.
- No dynamic allocation, autoscaling, Kubernetes, job clusters, new reconciler, provider payload,
  Terraform, public API, scheduling, cost model, or placement-policy change.
- No preliminary or status-only PR, extra acceptance cells, soak, release, C27, RC32, or Phase 8
  work.
