---
id: DANDER-209
title: Project the selected Azure platform into the immutable runtime
status: done
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-200]
created: 2026-08-16
---

## Context

Phase 8 Azure qualification preflight found that the selected Snowflake/PostgreSQL/no-catalog/
Key-Vault deployment was available to Terraform but not to the source-free runtime image. The
Container Apps Job would therefore select only image-baked platform coordinates, unlike the
accepted AWS runtime-overlay path.

## Acceptance Criteria

- [x] Azure planning renders the selected warehouse, state, catalog, secrets, launcher, runtime,
  safety, pipeline, and secret-reference configuration as a validated non-secret overlay.
- [x] Each Container Apps Job receives only its scoped deployment overlay through the existing
  `DANDER_PLATFORMS_CONFIG_JSON` runtime boundary.
- [x] Runtime selection uses the deployment name even when it differs from the platform name.
- [x] Focused Azure projection, bootstrap, and CLI tests pass with no provider access.
- [x] Protected CI and PR review pass before merge.

## Design

Reuse the existing 32 KiB validated runtime-overlay contract rather than changing the immutable
image or introducing an Azure-only configuration path. Terraform renders one pipeline-scoped
`DanderPlatforms` document from the already validated manifest, and the launcher exposes it as a
normal non-secret environment value. Secret values remain in Key Vault bindings.

## Implementation Notes

- No Azure API or Terraform operation is part of this correction.
- The overlay is parsed and normalized by `DanderPlatforms` before Terraform can receive it.
- Direct bootstrap callers must select either the named Azure canonical composition or the named
  Azure/GCP federation composition before Terraform can run.
- Existing image-baked Azure/GCP federation behavior remains compatible; planned deployments now
  receive their exact external platform selection as well.
- PR #350 implementation head `1830765` passed all five protected jobs in run `31959446996`.

## Review Log

### 2026-08-16 — PASS

Completion review first found that a direct bootstrap caller could construct a schema-valid but
unsupported provider composition. The focused correction now rejects anything except the named
Azure canonical or Azure/GCP federation composition before Terraform. The corrected exact head
passed all protected jobs; final review found no remaining material defect, comment, or thread.
