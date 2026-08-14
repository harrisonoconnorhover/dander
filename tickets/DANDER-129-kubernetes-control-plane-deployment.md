---
id: DANDER-129
title: Add the Kubernetes hosted Control-plane deployment
status: done
component: python
epic: druff-control-plane
depends_on: [DANDER-128]
created: 2026-08-14
---

## Context

D7 continues with one existing-cluster Kubernetes profile after the accepted local Compose proof.
It must reuse the D6 trust, service, and GraphStore boundaries without changing the established
batch-runtime Helm chart or pretending that a single-writer PVC is horizontally available.

## Acceptance Criteria

- [x] Render one closed immutable non-secret input into exact active/rollback Helm values and
      aligned Control OIDC, local GraphStore, Druff bootstrap, public-client, and deployment files.
- [x] Add a separate packaged chart with Control/Druff Deployments and Services, ingress-nginx
      Ingress/TLS, token-free ServiceAccounts, and one durable `ReadWriteOnce` GraphStore PVC.
- [x] Keep Control single-replica with `Recreate`; initialize the volume with root plus only CHOWN
      and FOWNER while keeping long-running containers non-root, read-only, and capability-free.
- [x] Hash consumed config and Control identity into pod templates; disable Ingress access logs.
- [x] Add deterministic preflight, bounded read-only live verification, and focused tests without
      changing the existing Kubernetes CronJob chart or launcher.
- [x] Qualify HTTPS synthetic OIDC, canonical browser graph persistence, equal repeated Helm
      rendering, stable second upgrade, digest rollback/restore, and exact disposable cleanup.

## Design

The first Kubernetes profile is an existing-cluster Helm deployment. Terraform state/saved plans,
cloud federation, and cloud cost controls do not apply. The closed `LocalGraphStoreBinding` is
mounted on one `ReadWriteOnce` PVC, so the profile makes no HA or horizontal-scale claim. A future
cloud-specific profile may select the already-typed object-store binding in its own provider PR.

Only ingress-nginx is accepted because its reviewed per-Ingress annotation disables access logs;
the front proxy must not record authorization codes, OIDC state, or rejected query tokens. The
input derives the Ingress hostname from one exact HTTPS origin and requires exact callback,
logout, and single-origin CORS topology.

## Implementation Notes

- Added `io.dander.kubernetes-control-plane/v1`, deterministic active/rollback values, saved
  byte-equal Helm rendering, and a read-only resource/HTTPS verifier.
- Kept the existing `infra/kubernetes/chart/dander` runtime chart byte-unchanged; hosted Control
  lives in the separate `dander-control` chart.
- The chart owns no Secret or identity-provider registration. It references an existing TLS Secret
  and gives Druff a distinct token-free ServiceAccount.
- Control config/OIDC/GraphStore/identity hashes and the Druff bootstrap hash trigger only the
  workload that consumed the changed projection.

## Review Log

The adversarial pre-review accepted the single-writer PVC boundary for a one-replica `Recreate`
Deployment and classified Terraform/cloud controls as not applicable to an existing-cluster Helm
profile. It required three focused corrections before implementation: close the public URL and
callback topology, add config/identity rollout digests, and pin ingress-nginx with access logging
disabled. Those corrections are included in the implementation and tests. The completion review
then found ingress-nginx's default 1 MiB request limit below Control's accepted graph-envelope
bound. The focused correction fixes the Ingress limit at 6 MiB and makes the read-only verifier
and chart tests require that exact value; no oversized live graph or broader proxy abstraction was
added. Live qualification remains pending.

The first live verifier run then exposed one serialization-boundary mismatch: Kubernetes
ConfigMap data serves the exact compact bootstrap document without the generator's file-ending
newline. The verifier now compares against that exact served ConfigMap value; its semantic and
field-level strictness is unchanged. PR #280 merged that correction at protected-main commit
`682f0fa5270fcd1e0f6379770dfc91778a331c13`; CI run `31830986502` passed all five jobs.

The resumed live qualification passed the complete Kubernetes criterion on a disposable kind
v0.32.0/Kubernetes v1.32.2 cluster with the pinned ingress-nginx v1.15.1 controller. Repeated
active and rollback Helm renders were byte-equal. A second identical upgrade preserved both
Deployment identities and generations. Exact active, rollback, and restored image pairs each
passed the bounded read-only verifier. A browser OIDC code/PKCE session created a canonical graph;
the same graph and content hash survived a Control pod replacement, digest rollback, and active
restoration.

The identity provider was the same bounded synthetic issuer used for local qualification, not a
real-provider registration. It proved separate API and SPA audiences, RS256, the admin role, a
five-minute access token, and no refresh token. The Helm release, namespace/PVC, disposable
cluster, synthetic Cloud Run issuer, registry and local issuer images, build cache/source,
generated projections, and TLS key were removed and independently absent. Accepted Dander/Druff
application images remain locally retained to avoid an unnecessary rebuild. The coordinate-free
record is `docs/evidence/kubernetes/2026-08-14/d7-control-plane.json`. This closes only the D7
existing-cluster single-writer Kubernetes profile; it promotes neither real-provider identity,
HA, horizontal scale, nor cloud-hosted support.
