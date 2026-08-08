---
id: DANDER-85
title: Route BigQuery execution through warehouse capabilities
status: completed
component: warehouse
epic: cloud-portability
depends_on: [DANDER-84]
created: 2026-08-08
---

## Context

Named profiles and lazy provider factories existed, but `dander run` still constructed BigQuery
writers and transform runners directly. The first Phase 3 slice must select the proven warehouse
through the provider boundary without mixing state, catalog, secrets, or launcher migration into
the same review.

## Acceptance Criteria

- [x] BigQuery configuration remains dependency-light and its implementation loads only on build.
- [x] One typed runtime composes relation, schema, writer, transform, fence, telemetry, and declared
  capability surfaces.
- [x] Hosted SCD1, sandbox replace, model, and graph runner construction preserve existing behavior.
- [x] Version 1 projects resolve implicitly to BigQuery and version 2 profiles retain their
  selected warehouse ID and location.
- [x] The CLI no longer constructs a BigQuery writer or transform runner directly.
- [x] Focused provider, migration, warehouse, and CLI composition tests pass.
- [x] Full local validation and isolated GCP no-drift pass.
- [x] Protected CI passes.

## Design

Use one small `WarehouseRuntime` composition rather than a large provider interface. Existing
BigQuery implementations remain intact behind factories, limiting this PR to construction and
contract movement. State, Dataplex, GCP secrets, and Cloud Run stay on their proven paths until
their own Phase 3 tickets.

## Review Log

Merged through protected PR #116 as `5478e1ba5ad7aaa66c4258f23ea01949942221d1`.
