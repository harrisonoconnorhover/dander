---
id: DANDER-285
title: Isolate plugin commands, share run identity, and refresh vulnerable dependencies
status: in-code
component: python
depends_on: []
created: 2026-09-30
---

## Context

A repository-wide structural scan found that offline plugin discovery loaded deployment commands,
and the S3 and PostgreSQL run stores each maintained the same submission-identity comparison.
Both add unnecessary coupling when changing otherwise independent features.
Protected CI also identified published advisories in four existing dependencies.
The image scan subsequently found 13 advisories in four inherited Debian packages.

## Acceptance Criteria

- [x] Move plugin commands to their own module while preserving options, output, and installation.
- [x] Dispatch plugin help/search without importing bootstrap, deployment, or provider modules.
- [x] Have both run stores use one identity rule with the existing compared fields unchanged.
- [x] Verify rejected identity changes leave persisted records unchanged and lifecycle saves work.
- [x] Record review scope, checks, and remaining maintenance priorities.
- [x] Update the four affected dependencies and pass the strict runtime dependency audit.
- [x] Refresh the four affected system packages in runtime and starter images and verify the OS layer.

## Implementation Notes

The existing lightweight Control dispatcher pattern also selects `plugins`. Manifest loading stays
inside installation; help and catalog discovery need neither deployment configuration nor SDKs.
`same_run_identity` belongs to the provider-neutral orchestration contract. Storage serialization,
schema versions, installer pins, and public CLI syntax are unchanged.
The dependency patch updates AnyIO, urllib3, PyJWT, and OAuthLib, with the direct PyJWT minimum
raised to 2.15.1. No audit exclusions or new dependencies were added.

See [the quality review](../docs/quality-review-2026-09-30.md) for findings and validation.
