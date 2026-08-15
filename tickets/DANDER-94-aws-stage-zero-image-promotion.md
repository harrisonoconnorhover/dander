---
id: DANDER-94
title: Bootstrap AWS state and promote source-free artifacts
status: in-review
component: deployment
epic: cloud-portability
depends_on: [DANDER-93]
created: 2026-08-08
---

## Context

Manifest-aware Fargate planning required an existing S3 backend, DynamoDB lock table, and ECR
image, while the platform root still attempted to own ECR. This slice resolves that bootstrap
cycle without applying infrastructure or claiming Fargate support.

## Acceptance Criteria

- [x] Plan stage zero locally before any AWS resource exists.
- [x] Create only encrypted/versioned state, locking, immutable registry, and deployment-role prerequisites.
- [x] Apply only a reviewed saved plan after confirmation.
- [x] Migrate initial local state into the created encrypted S3 backend after apply.
- [x] Preserve a local recovery copy if state migration fails.
- [x] Make stage zero, not the Fargate platform root, own ECR.
- [x] Copy an accepted source-free OCI index into ECR without rebuilding it.
- [x] Fail promotion unless the index and platform digests remain identical.
- [x] Focused Python tests, Terraform tests, real read-only AWS plan, and security scan pass.
- [x] Full local validation and retained GCP no-drift pass.
- [ ] Protected CI passes.

## Design

Copy the packaged stage-zero root into an operator-owned directory outside the checkout. Use local
state for the first saved plan, then migrate state after that exact plan creates S3 and DynamoDB.
Use the resulting short-lived deployment role for later platform plans and registry promotion.

## Review Log

The credentialed read-only AWS plan proposed 12 creates, zero updates, and zero deletes. No AWS or
GCP apply occurred.

The first 2026-08-14 stage-zero apply exposed that Terraform's `encrypt = true` backend setting
selected SSE-S3 for the state object even though the bucket default uses the root's
customer-managed key. The backend projection now pins the deterministic stage-zero KMS alias;
the live current object and retained no-drift proof remain part of the follow-up evidence run.
