---
id: DANDER-124
title: Add Azure Blob GraphStore
status: in_progress
component: python
epic: druff-control-plane
depends_on: [DANDER-120]
created: 2026-08-13
---

## Context

Add Azure Blob Storage behind the accepted GraphStore semantics.

## Acceptance Criteria

- [x] Use Blob ETag conditional controls and bounded list pagination.
- [ ] Pass shared mock conformance and a separately approved live restart/conflict/cleanup proof.
- [x] Keep managed identity and Blob-native metadata inside Dander/provider boundaries.

## Design

The API exposes opaque revisions and canonical hashes, never Azure request payloads.

## Implementation Notes

- Added a lazily loaded `AzureBlobGraphStore` behind one immutable HTTPS account URL,
  container, and deterministic prefix. The default client uses `DefaultAzureCredential`.
- Exact Blob ETags remain opaque revisions. Creates use `overwrite=False`; replacements,
  bounded reads, delete fences, journal transitions, and deletes use exact ETags with
  `IfNotModified`.
- Native inclusive `start_from` paging requests metadata and follows continuation tokens,
  including short pages. Healthy summaries require neither property reads nor body downloads.
- Hashed journals and an ETag-matched delete fence preserve exact replay and protect later
  recreations. Deletes target only the current base blob; snapshots and versions are never
  silently removed, and `SnapshotsPresent` fails closed as a provider-policy error.
- Raised only the `azure` and `runtime-all` Blob SDK floor to `12.28`, where inclusive
  `start_from` support was introduced. Live container policy, versioning, cleanup, and no-drift
  evidence remain a separately approved paid gate; public rc18 predates this adapter.

## Review Log

- Pre-implementation adversarial review required the compatible SDK floor, Azure-code-specific
  error handling, and exact current-base-blob deletion without snapshot/version expansion. All
  three constraints are implemented.
- Shared fake-provider conformance and focused Azure condition, pagination, concurrency, crash,
  bounded-read, policy-failure, and sanitization tests pass locally.
