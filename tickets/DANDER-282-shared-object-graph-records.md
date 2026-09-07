---
id: DANDER-282
title: Share identical object-store graph records
status: done
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
- [x] Merge through protected checks and verify exact-main CI.

## Verification

[PR #529](https://github.com/harrisonoconnorhover/dander/pull/529) merged as `26f1fcf`.
All six [exact-main checks](https://github.com/harrisonoconnorhover/dander/actions/runs/34152279904)
passed, following 181 combined storage, startup, and CLI regression tests on the integrated source.

## Boundary

This extraction leaves provider operation bodies unchanged. GCS retains its separate
generation-based revision semantics. Additional orchestration consolidation remains optional.
