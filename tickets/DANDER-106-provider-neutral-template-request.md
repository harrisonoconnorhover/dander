---
id: DANDER-106
title: Resolve launcher templates through one provider-neutral request
status: completed
component: deployment
epic: cloud-portability
depends_on: [DANDER-89, DANDER-90]
created: 2026-08-10
---

## Context

The Phase 5.5 architecture checkpoint found one narrow pre-Phase-6 correction: the launcher
factory protocol exposed GCP project and guarded-free-tier primitives to every launcher. Kubernetes
callers consequently supplied placeholder GCP values even though its projection did not consume
them.

## Acceptance Criteria

- [x] `ExecutionTemplateFactory` accepts one immutable provider-neutral resolved request.
- [x] Cloud Run, Fargate, and Kubernetes projections consume that request.
- [x] GCP-only construction values remain typed and outside the provider-neutral request.
- [x] Non-GCP callers pass no dummy GCP project or guarded-free-tier values.
- [x] Existing Cloud Run output remains exactly equal to the accepted compatibility projector.
- [x] Existing Fargate and Kubernetes projection and fail-closed behavior remains covered.
- [x] Focused launcher, bootstrap, Kubernetes CLI, and chart tests pass.
- [x] Full protected CI passes.
- [x] Independent completion review passes.

## Design

Introduce a frozen `ResolvedTemplateRequest` at the existing deployment boundary and recursively
copy its resolved pipeline containers into read-only equivalents. Capture the typed GCP data-plane
context when Cloud Run or Fargate is selected through the existing provider-registry construction
context. Keep direct compatibility projectors and all manifest/configuration schemas unchanged.

## Implementation Notes

Cloud Run delegates the request to the unchanged GCP projector. Fargate reads GCP-only values from
its frozen factory context. Kubernetes receives only the selected profile and common execution
intent; its existing guarded-free-tier rejection remains at manifest resolution. No extension bag,
Azure/OCI code, warehouse work, SQL, or user-visible projection change is included.

## Review Log

### 2026-08-10 — PASS

Independent read-only completion review found no material defect or unnecessary scope. Protected
CI passed all five required jobs on PR #181.
