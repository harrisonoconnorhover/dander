---
id: DANDER-285
title: Isolate plugin commands and share durable run identity
status: in-code
component: python
depends_on: []
created: 2026-09-30
---

## Context

A repository-wide structural scan found that offline plugin discovery loaded deployment commands,
and the S3 and PostgreSQL run stores each maintained the same submission-identity comparison.
Both add unnecessary coupling when changing otherwise independent features.

## Acceptance Criteria

- [x] Move plugin commands to their own module while preserving options, output, and installation.
- [x] Dispatch plugin help/search without importing bootstrap, deployment, or provider modules.
- [x] Have both run stores use one identity rule with the existing compared fields unchanged.
- [x] Verify rejected identity changes leave persisted records unchanged and lifecycle saves work.
- [x] Record review scope, checks, and remaining maintenance priorities.

## Implementation Notes

The existing lightweight Control dispatcher pattern also selects `plugins`. Manifest loading stays
inside installation; help and catalog discovery need neither deployment configuration nor SDKs.
`same_run_identity` belongs to the provider-neutral orchestration contract. Storage serialization,
schema versions, installer pins, and public CLI syntax are unchanged.

See [the quality review](../docs/quality-review-2026-09-30.md) for findings and validation.
