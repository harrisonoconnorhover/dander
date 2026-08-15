---
id: DANDER-125
title: Add OCI Object Storage GraphStore
status: done
component: python
epic: druff-control-plane
depends_on: [DANDER-120]
created: 2026-08-13
---

## Context

Add OCI Object Storage behind the accepted GraphStore semantics.

## Acceptance Criteria

- [x] Use provider-native conditional/version controls and bounded list pagination.
- [x] Pass shared mock conformance and a separately approved live restart/conflict/cleanup proof.
- [x] Use resource principal identity and keep OCI-native metadata inside the adapter.

## Design

Any OCI-specific conditional limitation must fail closed and remain explicit rather than inventing
false cross-provider parity.

## Implementation Notes

- Added a lazily loaded `OCIObjectGraphStore` behind one immutable namespace, bucket, and
  deterministic prefix. Default construction accepts only OCI resource-principal identity;
  developer profiles remain possible through explicit client injection.
- Exact ETags remain opaque revisions. `if-none-match: *` owns creates; exact `if-match` values
  fence replacement, bounded reads, journal transitions, and current-object deletion.
- Public cursors use exclusive `start_after`; provider continuation follows OCI's returned
  `nextStartWith` with inclusive `start`. Every healthy summary uses one bounded HEAD request and
  never downloads the graph body.
- Hashed journals and an ETag-matched delete fence preserve exact replay across concurrency,
  crashes, and later recreation. Deletes never pass a version ID; a versioned bucket therefore
  retains older versions and installs a delete marker for the exact current object.
- OCI's object API can return either `NotAuthorizedOrNotFound` or a code-less 404 for absence.
  Object-addressed HEAD treats the named response as absence; a code-less 404 requires one bounded
  list probe to prove the bucket remains accessible before it is treated as absence. List/bucket
  failures and all other codes remain sanitized provider errors, while disappearance after
  observation is a conflict. This unavoidable ambiguity is explicit rather than presented as
  false parity.
- The existing `oci>=2.184.1,<3` optional extra provides the required operations and pagination
  contract, so no dependency or lock change was needed. Corrected protected-main source passed the
  separate live bucket policy, restart/conflict/versioning/cleanup proof. Public rc18 predates this
  adapter, and the correction postdates the public artifacts, so no distribution was qualified.

## Review Log

- Pre-implementation adversarial review required resource-principal-only default construction,
  OCI-code-specific error handling, inclusive native continuation, HEAD-before-ranged-GET reads,
  and current-object-only deletion without version enumeration. All five constraints are in the
  implementation and focused tests.
- Shared fake-provider conformance and focused OCI condition, pagination, concurrency, crash,
  bounded-read, version-marker, identity-boundary, malformed-response, and sanitization tests pass
  locally. The enabled independent completion review returned PASS with no material findings;
  PR #262 and exact protected-main CI run `31760157381` passed. The ticket remains in progress only
  for its separately approved provider live proof; Phase D3's one-live-provider exit requirement
  is already satisfied by DANDER-122.
- The first disposable live attempt reached the exact protected-main adapter but stopped before
  writing graph data because OCI SDK 2.184.1 returned a code-less 404 for a missing object. The
  empty bucket was deleted and absence verified. The narrow correction probes bucket accessibility
  only for that live response shape and keeps missing-bucket/permission failures closed.
- PR #307 and all five exact-main CI jobs passed at `f43188f7`. The corrected disposable proof then
  passed create/read, fresh-client replay, list-revision update, restart persistence, stale
  update/delete conflicts, exact delete replay, absence, version/delete-marker inventory, and
  exact cleanup. The retained stage-zero plan reported no changes; unchanged Phase 7 foundation
  source refreshed with only provider-computed usage counters and empty metadata normalization,
  with no managed-resource action or state write. The coordinate-free
  [evidence](../docs/evidence/oci/2026-08-15/druff-oci-object-graph-store.json) promotes no OCI or
  public-package support.
