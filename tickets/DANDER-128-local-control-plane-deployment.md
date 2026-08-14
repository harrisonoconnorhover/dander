---
id: DANDER-128
title: Add the local hosted Control-plane deployment
status: in_progress
component: python
epic: druff-control-plane
depends_on: [DANDER-127]
created: 2026-08-14
---

## Context

D7 begins with a disposable local Docker Compose deployment before Kubernetes or any cloud
provider. The profile must exercise the same D6 service/OIDC/GraphStore boundaries without
inventing local Terraform or accepting mutable artifacts.

## Acceptance Criteria

- [x] Render one closed immutable non-secret input into aligned Control OIDC, Druff bootstrap,
      public-client, local GraphStore, deployment, active-image, and rollback-image files.
- [x] Use exact digest-addressed images only; Compose contains no checkout build path.
- [x] Publish only a loopback HTTPS edge; keep long-running containers non-root, read-only, and
      capability-free.
- [x] Initialize the named GraphStore volume with one networkless root service holding only CHOWN.
- [x] Add bounded preflight/live verification and exact active/rollback selection.
- [ ] Qualify HTTPS OIDC, graph restart persistence, equal repeated Compose rendering, unchanged
      second-up container IDs, rollback/restore, and exact disposable cleanup with current images.

## Design

The local profile reuses `HostedOIDCDeploymentInput`, `project_hosted_oidc`,
`ResolvedControlServiceRequest`, and `LocalGraphStoreBinding`. It has no Terraform state, provider
workload identity, cloud cost ceiling, or generic deployment framework. Generated files and TLS
material stay under ignored `.dander/`; the repository contains only static assets and a sanitized
example.

## Implementation Notes

- Added the deterministic `io.dander.local-control-plane/v1` renderer and verifier plus packaged
  Compose/Caddy/OpenSSL assets.
- The edge routes `/v1/*`, `/healthz`, and `/readyz` to Control and all other paths to the exact Druff
  static image, preserving the image's committed CSP/cache behavior.
- Control retains outbound HTTPS for the fixed JWKS URI, while only Caddy binds the host loopback.
- Active and rollback files require the same repository with distinct immutable digests. The live
  verifier checks exact image refs, users, read-only roots, published ports, readiness, bootstrap
  bytes, and security headers without returning logs or credentials.
- Generated non-secret files are mode `0444` inside a mode-`0700` operator directory, making their
  read-only bind mounts consumable by UID/GID 65532 without exposing them to other host users.
- Live qualification is explicitly deferred: public Dander RC19 predates D6 and DRUFF-29 retained
  no durable image. Exact artifacts must be supplied in a separately reviewed step; this PR does
  not silently build or promote them.

## Review Log

The pre-implementation adversarial review found two concrete prerequisites: no current immutable
D6/DRUFF-29 images exist, and an empty named volume would be root-owned while Control runs as UID
65532. The focused correction makes live qualification pending rather than fictional and adds one
same-image initializer with root plus CHOWN only, no network, and no other capability. TLS files
are host-generated under a sealed directory, mounted read-only, and verified readable. Local
no-drift is defined as equal Compose renders and stable container IDs, not Terraform ceremony.

The completion review passed after confirming the final mode-`0444` correction makes every
non-secret bind-mounted file readable by UID/GID 65532 inside the sealed mode-`0700` directory. It
found no material defect, excess scope, secret/artifact leak, or unsupported live/cloud claim.
