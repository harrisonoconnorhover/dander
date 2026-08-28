---
id: DANDER-250
title: Compose automatic placement with metadata-derived Spark sizing
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-249]
created: 2026-08-28
---

## Context

Control could independently choose a locality/cost-bounded environment or a metadata-derived
Managed Spark size class. It rejected their composition, so an `environment=auto` deployment could
not use the qualified BigQuery metadata estimator.

## Acceptance Criteria

- [x] Estimate each configured sized environment before applying the existing automatic placement
      policy, while retaining unsized single-container candidates.
- [x] Select one immutable plan using its existing locality, cost, size-class, and resource bounds.
- [x] Persist the existing placement and sizing evidence without changing their canonical schemas.
- [x] Keep estimator success or fallback mode scoped to its exact environment.
- [x] Replay one prior automatic API run before mutable metadata is read again after restart.
- [x] Reject manual/default or multiple environment-scoped idempotency claims during auto replay.
- [x] Preserve explicit environment selection, manual sizing, schedules, and provider payloads.

## Boundaries

- No new cost model, estimator registry, execution backend, reconciler, or public API schema.
- No dynamic Spark allocation, autoscaling, Kubernetes, job clusters, or generalized topology.
- No image publication, Terraform change, live provider qualification, status-only PR, C27, RC32,
  or Phase 8 work.
