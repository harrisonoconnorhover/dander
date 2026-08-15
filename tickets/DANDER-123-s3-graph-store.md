---
id: DANDER-123
title: Add S3 GraphStore
status: done
component: python
epic: druff-control-plane
depends_on: [DANDER-120]
created: 2026-08-13
---

## Context

Add S3 storage behind the accepted GraphStore semantics.

## Acceptance Criteria

- [x] Use provider-native conditional/version controls and bounded list pagination.
- [x] Pass shared mock conformance and a separately approved live restart/conflict/cleanup proof.
- [x] Keep credentials, rows, plans, state, and provider-native revisions out of committed evidence.

## Design

Resolve any narrow S3 conditional-write quirk inside this adapter without weakening GraphStore.

## Implementation Notes

- Added a general-purpose-bucket-only `S3GraphStore` with lazy AWS SDK loading and an optional
  exact expected-owner binding.
- Exact quoted ETags remain opaque revisions. `If-None-Match: *` owns creates; `If-Match` fences
  replacements, reads, journal transitions, and deletes.
- Hashed durable journals and an ETag-matched delete fence preserve exact replay and prevent a
  delayed delete retry from removing a later recreation.
- Healthy list summaries use bounded `ListObjectsV2` pages plus `HeadObject` metadata, never graph
  bodies. Reads pin an ETag, request one bounded byte range, and close the response stream.
- Raised only the `aws` and `runtime-all` boto3 floors to `1.35.69`, the first verified model in the
  supported line that exposes all required put/delete conditions.
- Live AWS policy, versioning, encryption, cleanup, and no-drift evidence remain a separate paid
  approval gate; this implementation does not qualify public rc18.

## Review Log

- Pre-implementation adversarial review required the compatible boto3 floor, operation-specific
  404/409/412 handling, and an explicit general-purpose bucket boundary. All three were applied.
- Shared fake-provider conformance and focused S3 condition, pagination, concurrency, crash,
  bounded-read, and sanitization tests pass locally.
- Completion review found that a meaningful `NoSuchBucket` 404 could be misclassified as a missing
  graph. Error helpers now prefer the AWS error code, and a focused regression covers the fix.
- Live qualification on protected-main source commit `16f0954c` passed create/read, list, update,
  exact replay across fresh adapters, stale update/delete rejection, deletion replay, restart
  persistence, versioning, provider-default encryption, public-access blocking, and exact cleanup.
  The [coordinate-free evidence](../docs/evidence/aws/2026-08-15/d7-control-plane.json) retains
  hashes and booleans only. It qualifies this source boundary without promoting S3 support.
