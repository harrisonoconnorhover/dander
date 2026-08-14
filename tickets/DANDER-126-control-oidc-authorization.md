---
id: DANDER-126
title: Secure hosted Control API with external OIDC
status: done
component: python
epic: druff-control-plane
depends_on: [DANDER-121]
created: 2026-08-13
---

## Context

Hosted human identity must be external and independent of provider workload identity.

## Acceptance Criteria

- [x] Validate JWT signature, issuer, audience, expiry, and required claims on hosted requests.
- [x] Map viewer/editor/operator/admin roles centrally to read/edit/validate-preview/run-cancel-replay/
      administrative capabilities and enforce mutations server-side.
- [x] Generate exact origin/callback/public bootstrap and server trust projections from one typed
      deployment input and verify equality.
- [x] Reject missing/invalid auth, URL/log/localStorage tokens, and browser cloud credentials.
- [x] Preserve no-OIDC loopback mode and pass each authorization-capability test.

## Design

Druff uses a public OIDC client and authorization code plus PKCE; Dander is the trust and
authorization boundary. OIDC registration/live login requires separate human approval.

## Implementation Notes

- Added one frozen, closed deployment input that projects exact private trust, public SPA
  registration, CORS origins, and a versioned secret-free bootstrap DTO. Client ID and API audience
  are separate; the browser registration permits only code flow with PKCE S256 and no secret.
- Added strict API-audience JWT validation and a bounded fixed-URI JWKS resolver with asymmetric
  algorithm pinning, single-flight refresh, shared unknown-key cooldown, response/key limits, and
  last-good retention. JWKS network work runs in FastAPI's synchronous dependency thread pool.
- Centralized the four roles over five capabilities and attached one server-side capability to
  every hosted route. Hosted capability discovery is filtered to the caller; loopback behavior is
  unchanged.
- Added exact non-credentialed CORS, hosted security headers, URL-token rejection, query-free
  Uvicorn logging, and optional verified subject/email/group allowlists. Dander keeps no browser
  cookie/session and never maps human claims to provider workload identity.
- Added the generated `control-bootstrap` schema/fixture. No external issuer was registered and no
  live provider, public artifact, credential, or paid resource was changed.

## Review Log

- 2026-08-13: pre-implementation adversarial review required separate SPA client/API audiences, a
  bounded single-flight JWKS resolver instead of raw client defaults, explicit query-free server
  logging, and exact non-credentialed CORS with readable ETags. All four corrections are included
  in the implementation and focused tests.
