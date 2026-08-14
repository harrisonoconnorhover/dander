---
id: DANDER-203
title: Qualify the Kubernetes canonical profile
status: open
component: infrastructure
epic: cloud-portability-phase-8
depends_on: [DANDER-201, DANDER-202]
created: 2026-08-13
---

## Context

The PostgreSQL native profile and Helm lifecycle contract pass locally, but no exact-candidate live
cluster has passed the complete Phase 8 profile.

## Acceptance Criteria

- [ ] One named Kubernetes 1.27+ cluster runs the final candidate with PostgreSQL state/warehouse,
  catalog `none`, and an existing Secret projection.
- [ ] Manual/scheduled execution, replay, overlap, interruption/deadline, rotation, alerting,
  upgrade, rollback, cleanup, and chart verification pass.
- [ ] Scale and soak evidence use the normalized qualification contract.
- [ ] Only sanitized evidence is committed; cluster and external Secret ownership remain explicit.
