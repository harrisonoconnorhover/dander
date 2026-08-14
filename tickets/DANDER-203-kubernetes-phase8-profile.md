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
passes the complete lifecycle and a five-class normalized scale slice on disposable Kubernetes
1.32.2 clusters; the remaining launcher classes and soak remain open.

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
- The lifecycle portion remains closed. The later normalized slice does not close the remaining
  launcher classes, hosted cost, or scheduled soak, so the ticket and experimental status remain.
- A later exact-RC22 Job passed normalized correctness, bulk-throughput, incremental, transform,
  and PostgreSQL-specific failure reports under 2 CPU/512 MiB, a 600-second deadline, and zero
  retries. Both Jobs, PostgreSQL, Secrets, namespace, TLS material, and cluster were deleted. The
  first successful Job's ephemeral reports could not be copied after completion; the unchanged
  reporter-sidecar rerun retained all five reports and the attempt ledger preserves both outcomes.
