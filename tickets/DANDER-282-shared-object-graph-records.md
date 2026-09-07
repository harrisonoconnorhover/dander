---
id: DANDER-282
title: Share identical object-store graph records
status: in-review
component: python
depends_on: []
created: 2026-09-07
---

## Context

S3, Azure Blob, and OCI copy the same six graph metadata and journal models. A record change
should have one definition while each adapter retains its provider-specific storage operations.

## Acceptance Criteria

- [x] Share the identical record definitions without changing their JSON fields or validation.
- [x] Preserve provider read/write operations, revisions, pagination, and error handling.
- [x] Pass existing shared and provider GraphStore conformance tests, Ruff, and strict typing.
- [ ] Merge through protected checks and verify exact-main CI.

## Boundary

This extraction leaves provider operation bodies unchanged. GCS retains its separate
generation-based revision semantics. Additional orchestration consolidation remains optional.
