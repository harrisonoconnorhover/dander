---
id: DANDER-101
title: Add the existing-cluster Kubernetes and Helm launcher
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-89, DANDER-100]
created: 2026-08-08
---

## Context

The execution projection and native PostgreSQL profile had no cloud-neutral scheduled launcher.
Kubernetes must target an existing cluster without becoming another cluster-provisioning program.

## Acceptance Criteria

- [x] Register a validated, lazily loaded Kubernetes launcher without cloud-specific SDK imports.
- [x] Package one versioned Helm chart in source-free projects.
- [x] Render ConfigMap, ServiceAccount, CronJobs/Job templates, optional RBAC, external Secret
      references, resources, deadlines, retries, history limits, and completed-Job TTL.
- [x] Default schedules to `Forbid` overlap and pods to `restartPolicy: Never`.
- [x] Accept cloud-neutral ServiceAccount annotations and pod labels.
- [x] Add non-mutating `dander kubernetes plan` and read-only deployment verification.
- [x] Validate the chart with Helm in protected CI.
- [x] Keep cluster creation, chart installation, live cloud mutation, and support claims out of scope.

## Design

Version 2 owns the context, namespace, release, ServiceAccount, external Secret name, and metadata.
The provider maps `io.dander.execution/v1` into deterministic non-secret Helm values. The chart
uses stdout and native cluster primitives instead of bundling an observability or identity system.

## Review Log

Local Helm rendering and protected CI remain before merge. A disposable existing-cluster
PostgreSQL run belongs to the separate Phase 4 profile-acceptance slice.
