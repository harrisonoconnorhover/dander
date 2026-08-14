---
id: DANDER-128
title: Add the local hosted Control-plane deployment
status: done
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
- [x] Initialize the named GraphStore volume with one networkless root service holding only CHOWN
      and FOWNER.
- [x] Add bounded preflight/live verification and exact active/rollback selection.
- [x] Qualify HTTPS OIDC, graph restart persistence, equal repeated Compose rendering, unchanged
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
same-image initializer with root plus only CHOWN and FOWNER, no network, and no other capability.
TLS files
are host-generated under a sealed directory, mounted read-only, and verified readable. Local
no-drift is defined as equal Compose renders and stable container IDs, not Terraform ceremony.

The completion review passed after confirming the final mode-`0444` correction makes every
non-secret bind-mounted file readable by UID/GID 65532 inside the sealed mode-`0700` directory. It
found no material defect, excess scope, secret/artifact leak, or unsupported live/cloud claim.

On 2026-08-14, immutable `dander-platform==0.9.0rc20` published the D6/D7 Python and packaged-asset
boundary from protected-main commit `75c5654e95439eaf18e90fbacc849799f4fe42b6`. Public package
verification passed, but the unchecked live criterion remains pending exact current Dander and
Druff container images; no local or provider support was promoted.

The first Docker Desktop live start exposed that `chmod 0700` fails after dropping all capabilities
and restoring only CHOWN. FOWNER is the narrowly required Linux capability for changing the named
volume root's mode. The initializer now receives exactly CHOWN and FOWNER; its command, root user,
network isolation, read-only root filesystem, and no-new-privileges boundary are unchanged.
Docker inspection reports those accepted capabilities as `CAP_CHOWN` and `CAP_FOWNER`; the live
verifier now checks that exact engine representation rather than the shorter Compose spelling.
With the correction applied, the empty-volume initializer exited zero, Control became healthy, and
the bounded active-environment verifier passed. PR #274 merged the correction at protected-main
commit `ee03b942c278ba63098bcea30a97f2a9ab05a553`; CI run `31819455797` passed all five jobs.

The resumed live qualification then passed the complete local criterion. Exact active and rollback
Dander/Druff digests served one loopback HTTPS origin; a pinned synthetic OIDC issuer proved PKCE,
separate API and SPA audiences, RS256, the admin role, a five-minute access token, and no refresh
token. An API-created graph and a browser-created graph survived a Control restart, the accepted
digest rollback pair, and active restoration with unchanged content hashes. Two Compose renders
were byte-equal and a second active `up` preserved all three running service IDs. The synthetic
issuer, registry copies, Compose containers, network, named GraphStore volume, generated files,
and localhost TLS key were removed and independently absent. Accepted local image objects remain
in Docker's content store for the next D7 provider profile, avoiding an unnecessary rebuild.

After cleanup, fresh retained-GCP stage-zero and current-equivalent RC21 platform plans each
reported exact `No changes.` D7 did not apply retained infrastructure and kept no saved plan or
copied state. The coordinate-free record is
`docs/evidence/local/2026-08-14/d7-control-plane.json`. This closes only the D7 local profile; it
does not qualify a real identity provider, Kubernetes, or any cloud-hosted Control service.
