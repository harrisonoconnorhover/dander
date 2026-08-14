---
id: DANDER-130
title: Add the GCP hosted Control-plane deployment
status: in_progress
component: python
epic: druff-control-plane
depends_on: [DANDER-129]
created: 2026-08-14
---

## Context

D7 continues with one GCP Cloud Run profile after the accepted local and existing-cluster
Kubernetes proofs. It must reuse the D6 trust, service, and GraphStore contracts, preserve the
retained GCP stack, and keep every disposable resource inside a separate state root.

## Acceptance Criteria

- [x] Render one closed immutable non-secret input into exact active/rollback Terraform values and
      aligned Control OIDC, GCS GraphStore, Druff bootstrap, public-client, and deployment files.
- [x] Add a separately packaged partial-backend Terraform root for two public Cloud Run services,
      distinct keyless service identities, numeric Secret Manager config versions, and one private
      versioned GCS GraphStore bucket.
- [x] Disable soft-delete retention only on the disposable graph bucket and verify the provider
      policy before graph writes; do not alter the retained state bucket.
- [x] Add deterministic backend-free preflight, bounded read-only live verification, focused
      Python/Terraform tests, and protected-CI coverage.
- [ ] Qualify synthetic OIDC, canonical browser graph persistence, equal no-change plans,
      immutable digest rollback/restore, exact state-prefix/resource cleanup, and retained-GCP
      no-drift.

## Design

The provider input derives both deterministic Cloud Run origins before apply and requires exact
Druff callback/logout plus single-origin CORS topology. Control alone receives
`roles/storage.objectUser` on the disposable graph bucket. Neither runtime identity receives a
project role or user-managed key. Public Cloud Run ingress is safe only because hosted OIDC
protects every Control API route; Druff receives no GraphStore permission.

Startup configuration is non-secret but delivered as numeric Secret Manager versions so an exact
revision cannot silently consume a later value. Mounts remain outside Druff's immutable `/app`
export. The graph bucket is versioned for fencing and uses `soft_delete_policy` zero because the
live profile is disposable. Terraform state uses a unique prefix in the existing retained state
bucket and never shares resource ownership with `infra/`.

## Review Log

The adversarial pre-review accepted the separate root, provider projection, narrow IAM, and one
synthetic browser journey. It found one cleanup defect: new GCS buckets otherwise retain deleted
objects and buckets through the default soft-delete window even after Terraform destroy. The
smallest correction sets zero retention on this disposable graph bucket, verifies it before any
graph write, and requires post-destroy absence of live versions and a soft-deleted bucket. The
retained state bucket is deliberately unchanged. Live qualification remains pending.

The independent completion review then found that the first verifier mixed the Cloud Run v1
`gcloud` response with a v2 secret field and inspected the desired template without proving which
revision served traffic. The resumed minimum correction now validates v1 `secretName` plus the
exact revision-level alias mapping, reads each mounted numeric Secret Manager version instead of
`latest`, requires observed generation equality and matching latest-created/latest-ready revision,
and accepts only one 100% traffic target for that revision. Focused regressions use the real v1
shape and reject wrong-version content, stale reconciliation, and split traffic.

The protected implementation and exact-main CI passed before provider access. A subsequent
read-only GCP preflight exposed one separate CLI boundary: `gcloud storage buckets describe`
emits its bucket policy fields in snake case, unlike the camel-case Storage API representation.
The focused follow-up consumes that exact CLI shape and keeps the same fail-closed checks for
uniform access, public-access prevention, versioning, and zero soft-delete retention. No cloud
resource was created or changed before this correction.
