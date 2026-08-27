---
id: DANDER-244
title: Compile canonical graphs into bounded deterministic physical plans
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-243]
created: 2026-08-27
---

## Context

DANDER-241 introduced a canonical physical-plan contract, and DANDER-242/243 proved one fixed
Managed Spark backend and artifact pair. Physical plans were still assembled by operator code.
This slice makes graph-to-plan compilation a deterministic Control capability without changing
pipeline logic or expanding the qualified Spark runtime.

## Acceptance Criteria

- [x] Compile one canonical graph into a fused plan that preserves the existing single-container
  path and includes every graph node exactly once.
- [x] Compile exactly one source-to-transform-to-target chain into a fixed two-stage distributed
  plan with two partitions and one object-store exchange.
- [x] Derive identical plans and revisions from equivalent graph documents regardless of node or
  edge declaration order.
- [x] Append the exact canonical physical plan to an execution template without hand-assembling
  the hidden runtime argument.
- [x] Keep backend selection in the immutable execution plan and require Managed Spark plans to
  select distributed physical execution.
- [x] Fail closed for unsupported node types, distributed graph shapes, joins, duplicate plan
  binding, and insufficient planned parallelism.
- [x] Preserve the physical plan and backend selection across canonical execution-plan
  serialization and restart loading.

## Boundaries

- No image publication, provider calls, cloud qualification, or changes to the accepted Spark
  driver/image pair.
- No arbitrary Spark operator runtime, joins, dynamic topology, dynamic partitions, resource
  estimation, autoscaling, Kubernetes, or job-cluster management.
- No automatic cost/locality placement changes; existing immutable environment and backend plans
  continue to own that selection.
