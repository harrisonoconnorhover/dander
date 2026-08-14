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

The PostgreSQL native profile and Helm lifecycle contract pass locally. Exact private RC22 now
passes the complete lifecycle on a disposable Kubernetes 1.32.2 cluster; scale and soak remain open.

## Acceptance Criteria

- [x] One named Kubernetes 1.27+ cluster runs the final candidate with PostgreSQL state/warehouse,
  catalog `none`, and an existing Secret projection.
- [x] Manual/scheduled execution, replay, overlap, interruption/deadline, rotation, alerting,
  upgrade, rollback, cleanup, and chart verification pass.
- [ ] Scale and soak evidence use the normalized qualification contract.
- [x] Only sanitized evidence is committed; cluster and external Secret ownership remain explicit.

## Implementation Notes

- Private RC22 index `sha256:ce395d…47c3` passed the contract-valid correctness and failure
  objective sets on local kind; the cluster, namespace, database, and Secrets were deleted.
- The Kubernetes Warning event reached a live operator-owned watch. Dander still does not provision
  a monitoring stack or alert target, and hosted-provider alerting is not implied.
- This closes the lifecycle portion only. Normalized scale/cost and scheduled soak remain open, so
  the ticket and experimental profile status do not change.
