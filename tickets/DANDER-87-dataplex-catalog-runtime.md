---
id: DANDER-87
title: Route Dataplex publication through catalog capabilities
status: completed
component: catalog
epic: cloud-portability
depends_on: [DANDER-86]
created: 2026-08-08
---

## Context

Named profiles declared Dataplex or no cloud catalog, but every CLI publication path still
constructed Dataplex directly and importing the catalog package loaded its Google SDK. Catalog
selection must become independent without changing the accepted metadata spine or GCP behavior.

## Acceptance Criteria

- [x] Version 1 retains implicit Dataplex selection and version 2 carries `dataplex` or `none`.
- [x] Dataplex and no-catalog runtimes use the existing lazy API-v1 provider registry.
- [x] Dataplex implementation and SDK modules load only after Dataplex is selected.
- [x] Every CLI Dataplex publication path constructs its publisher through the provider boundary.
- [x] Existing aspect generation, publication, unrelated-field preservation, and readback pass.
- [x] No-catalog selection has no publisher and performs no external publication.
- [x] Local semantic-registry and durable metadata-snapshot behavior remains unchanged.
- [x] Focused provider, Dataplex, CLI, and project-resolution tests pass.
- [x] Full local validation and isolated GCP no-drift pass.
- [x] Protected CI passes.

## Design

Compose one small `CatalogRuntime` containing an optional publisher and explicit capabilities.
Keep `CatalogPublishError` provider-neutral and lazily expose legacy Dataplex imports. This is a
construction change only; canonical non-BigQuery catalog assets and Glue remain later tickets.

## Review Log

Merged through protected PR #118 as `7c6c09ccb19a4e2126f3d204dba7b3dbfdd4d2a5`.
