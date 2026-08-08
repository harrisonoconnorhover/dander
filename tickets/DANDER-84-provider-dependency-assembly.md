---
id: DANDER-84
title: Assemble optional provider dependencies in the full runtime image
status: in-review
component: packaging
epic: cloud-portability
depends_on: [DANDER-83]
created: 2026-08-07
---

## Context

Provider factories load implementations lazily, but Python packages and the official OCI image
need deterministic SDK dependency sets before concrete adapters can be qualified independently.

## Acceptance Criteria

- [x] Public extras exist for BigQuery, Snowflake, Redshift, PostgreSQL, GCP, AWS, Azure, and OCI.
- [x] `runtime-all` is the exact union of provider dependency distributions.
- [x] The OCI extra remains reserved and excludes the SDK that conflicts with the audited
  cryptography line.
- [x] Repository and generated source-free Dockerfiles install `runtime-all`.
- [x] Full-image assembly fails when a declared distribution is missing.
- [x] Dependency validation reads package metadata and does not import provider SDKs.
- [x] The capability manifest still advertises only implemented, proven adapters.
- [x] Tests prevent drift between public extras, the runtime union, and generated Dockerfiles.
- [x] The full image builds and runs conformance on linux/amd64 and linux/arm64.
- [x] Full local validation and dependency/security audits pass.
- [ ] Protected CI passes.

## Design

Keep dependency availability separate from provider registration. Extras install SDKs; lazy
factories select adapters; the capability manifest and live qualification define support.

## Implementation Notes

The default dependency set remains backward compatible. `runtime-all` deliberately prepares one
release image for later adapters without adding provider configuration or runtime dispatch here.
OCI has no first-class adapter and its reserved empty extra avoids an unsafe dependency override.
PostgreSQL uses pure-Python Psycopg with the maintained Debian `libpq5` runtime package; this keeps
the image scan clean without suppressing findings from a wheel-bundled native library.

## Review Log

Pending protected review.
