---
id: DANDER-117
title: Publish the OCI lifecycle controller from the reviewed wheel
status: in-review
component: python
epic: cloud-portability-phase-7
depends_on: [DANDER-115, DANDER-116]
created: 2026-08-12
---

## Context

The OCI Function controller is intentionally distinct from the portable task runtime. Its image
must contain Phase 7 controller code without allowing uncommitted checkout files or a different
wheel to enter the live deployment.

## Acceptance Criteria

- [x] Require an exact lowercase SHA-256 for one valid `dander-platform` wheel before OCI access.
- [x] Require the wheel to contain its own OCI controller Dockerfile, shim, dependency pins, and
  package metadata.
- [x] Extract only those reviewed files and the wheel into an ephemeral source-free build context.
- [x] Build and publish only `linux/amd64` with a deterministic wheel-bound tag in the reviewed
  private immutable OCIR repository.
- [x] Use the repository-scoped SecurityToken-derived access token only through the temporary
  mode-`0600` Docker configuration.
- [x] Verify the digest and runnable platform, then write a sanitized local artifact record.
- [x] Fail closed on a pre-existing tag unless a local artifact record binds the same wheel hash,
  tag, and digest.
- [x] Focused contracts cover wheel integrity, source-free context, idempotency, platform checks,
  CLI confirmation, and cleanup after failure.
- [ ] Protected CI passes and this implementation merges before live controller publication.
- [ ] Separately approved live publication proves the real OCIR and OCI Functions path.

## Design

Treat the wheel as the reviewed controller source boundary. The deterministic tag supports safe
reruns, while the immutable digest remains the only deployment identity.

## Review Log

Protected review and live publication remain pending.
