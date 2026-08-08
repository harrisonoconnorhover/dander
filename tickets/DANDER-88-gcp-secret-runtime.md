---
id: DANDER-88
title: Route GCP secret resolution through provider capabilities
status: completed
component: security
epic: cloud-portability
depends_on: [DANDER-87]
created: 2026-08-08
---

## Context

Named profiles declared a secret provider, but execution constructed GCP and environment stores
directly and importing the implementation eagerly loaded the Google SDK. Secret selection must use
the shared provider boundary without changing the accepted GCP runtime or Terraform resources.

## Acceptance Criteria

- [x] Version 1 and migrated version 2 projects retain GCP Secret Manager selection.
- [x] GCP and environment runtimes use the existing lazy API-v1 provider registry.
- [x] Hosted execution and connector capability operations use the selected secret runtime.
- [x] Sandbox execution explicitly selects environment-only resolution.
- [x] GCP resource-name and environment-indirection behavior remains unchanged.
- [x] Credential-access auditing remains unchanged and never records a value.
- [x] Environment selection loads neither the GCP runtime nor Google Secret Manager SDK.
- [x] Cloud Run continues to reject environment-only secret profiles.
- [x] Focused provider, resolution, CLI, and project-profile tests pass.
- [x] Full local validation and isolated GCP no-drift pass.
- [x] Protected CI passes.

## Design

Compose one small `SecretRuntime` containing the existing `SecretStoreProvider` protocol plus
explicit capabilities. Keep legacy public classes available, make Google client construction lazy,
and leave secret references, IAM, Terraform, and launcher projection unchanged.

## Review Log

Full local validation, isolated retained-project no-drift, and protected CI passed. PR #119 merged
as `c57c948dd4af83f51ec3385487429e0472049dd5`.
