---
id: DANDER-116
title: Promote the accepted runtime index into OCIR
status: in-review
component: python
epic: cloud-portability-phase-7
depends_on: [DANDER-115]
created: 2026-08-12
---

## Context

Phase 7 must run the same accepted source-free release digest on OCI. OCI-specific rebuilding would
invalidate that artifact identity, while long-lived OCIR user auth tokens would violate the
keyless operator boundary.

## Acceptance Criteria

- [x] Require the exact accepted runtime artifact record and re-verify its source platform map.
- [x] Require the selected destination repository to exist, be private, immutable, and available.
- [x] Derive a short-lived `pull,push` token scoped to only that repository from an authenticated
  OCI `SecurityToken` profile.
- [x] Preserve existing source-registry helpers in a mode-`0600` temporary Docker configuration;
  never put the token in arguments or persistent configuration.
- [x] Copy without rebuilding and require equal index and per-platform digests at the destination.
- [x] Treat an existing equal immutable tag as idempotent and reject all mismatches.
- [x] Require explicit CLI confirmation and write only a local sanitized artifact record.
- [x] Focused contracts cover copy, idempotency, repository policy, token lifetime, digest rewrite,
  platform drift, source-record mismatch, cleanup, and CLI confirmation.
- [x] Protected CI passes and this implementation merges before live OCIR publication.
- [ ] Separately approved live publication proves the real registry preserves the accepted index.

## Design

Use OCI's Container Registry access-token endpoint with signature-header authentication from the
existing SecurityToken session. The returned identity token remains memory/temporary-file only and
is removed even when copy or verification fails.

## Review Log

Protected implementation PR #216 merged at `fae47a3cf860ba74a7c40b63c84ca21b9db7c6a2`, and the
protected-main CI run passed. Live publication remains pending.
