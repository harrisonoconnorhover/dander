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
- Public Dander RC20 now packages the D6 startup seam and these D7 local assets, but it did not
  publish an exact current Dander container image and DRUFF-29 retained no durable image. Exact
  reviewed images must be supplied separately; the profile does not silently build or promote
  either checkout.

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

On 2026-08-14, immutable `dander-platform==0.9.0rc20` published the D6/D7 Python and packaged-asset
boundary from protected-main commit `75c5654e95439eaf18e90fbacc849799f4fe42b6`. Public package
verification passed, but the unchecked live criterion remains pending exact current Dander and
Druff container images; no local or provider support was promoted.
