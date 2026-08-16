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

The PostgreSQL native profile and Helm lifecycle contract pass locally. Exact private RC27 passes
the named local profile and a five-class normalized scale slice on disposable Kubernetes 1.32.2;
the remaining launcher classes, hosted scale/cost, and soak remain open.

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
- PR #338 merged five fresh objective approvals as protected main `6ff041f`; exact-main run
  `31942160724` passed before the disposable cluster was created.
- Exact RC27 then passed correctness, bulk, incremental, transform, and PostgreSQL-specific failure
  on named kind 1.32.2 arm64 with PostgreSQL state/warehouse, catalog `none`, an existing Secret
  projection, TLS PostgreSQL 15.18, 2 CPU/512 MiB, a 600-second deadline, zero retries,
  reporter-sidecar collection, and non-estimated USD 0 cost. Exact cleanup removed the cluster,
  namespace, in-cluster Secrets/TLS material, database state, and temporary image tag with zero Warning
  events. Hosted scale/cost, remaining launcher classes, and soak remain open.
- PR #345 merged the remaining local crossover evidence as protected main `366ce8a`; exact-main
  run `31951009601` passed all five jobs. A fresh objective now binds the protected bounded-memory
  workload to one disposable zonal GKE Standard cluster with a USD 0.50 ceiling. This is the hosted
  scale/cost final audit only; scheduled soak remains separate.
