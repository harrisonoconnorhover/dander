---
id: DANDER-247
title: Select bounded Managed Spark worker shapes from supplied input estimates
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-246]
created: 2026-08-27
---

## Context

DANDER-246 qualified one static two-executor linear Spark plan. Control already had durable named
size-class selection, but plan compilation could not derive different distributed partition and
worker shapes. This slice connects those existing pieces without adding runtime autoscaling or an
input estimator.

## Acceptance Criteria

- [x] Compile one graph and base Dataproc template into deterministic, revision-distinct named
      size-class plans regardless of class declaration order.
- [x] Bind each class's executor count, per-executor CPU/memory, physical stage/exchange partitions,
      and caller-supplied maximum input bytes into the immutable plan and existing size candidate.
- [x] Select the smallest fitting class from `estimated_input_bytes`, persist the decision, and
      preserve an unsized fused Fargate route for requests that do not ask for sizing.
- [x] Reject a sizing request for an unsized environment and reject any Dataproc submission whose
      executor count differs from its physical partitions.
- [x] Execute the linear Spark runtime with exactly the planned static executor/partition count and
      keep Spark dynamic allocation disabled.
- [x] Render the generated plan JSON and size candidates through the existing Control startup
      arguments without changing pipeline logic.
- [ ] Publish one immutable Spark image/driver pair from repaired exact main.
- [ ] Run exactly two Managed Spark cells using controlled small and large byte estimates against
      one raw BigQuery snapshot; prove selection, submitted worker properties, output parity, and
      cleanup.

## Boundaries

- `estimated_input_bytes` remains a caller-supplied estimate; automatic measurement is deferred.
- No Spark dynamic allocation, autoscaling, autotuning, joins, new graph shapes, Kubernetes,
  job-cluster management, cost/locality changes, or new reconciler.
- No main-runtime image publication, extra live cells, soak, status-only PR, or evidence framework.
