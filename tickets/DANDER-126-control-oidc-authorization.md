---
id: DANDER-126
title: Secure hosted Control API with external OIDC
status: open
component: python
epic: druff-control-plane
depends_on: [DANDER-121]
created: 2026-08-13
---

## Context

Hosted human identity must be external and independent of provider workload identity.

## Acceptance Criteria

- [ ] Validate JWT signature, issuer, audience, expiry, and required claims on hosted requests.
- [ ] Map viewer/editor/operator/admin roles centrally to read/edit/validate-preview/run-cancel-replay/
      administrative capabilities and enforce mutations server-side.
- [ ] Generate exact origin/callback/public bootstrap and server trust projections from one typed
      deployment input and verify equality.
- [ ] Reject missing/invalid auth, URL/log/localStorage tokens, and browser cloud credentials.
- [ ] Preserve no-OIDC loopback mode and pass each authorization-capability test.

## Design

Druff uses a public OIDC client and authorization code plus PKCE; Dander is the trust and
authorization boundary. OIDC registration/live login requires separate human approval.

## Implementation Notes

_Pending._

## Review Log

_Pending._
