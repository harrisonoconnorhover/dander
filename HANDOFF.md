# Morning Handoff

## Finished

- Completed the D7 local hosted Control proof on exact active/rollback Dander and Druff digests.
- Proved synthetic OIDC/PKCE, separate audiences, browser access, and restart-safe graph persistence.
- Proved equal Compose renders, stable second-up IDs, digest rollback/restoration, and verifier success.
- Removed all disposable local/GCP resources, generated projections, and localhost TLS material.
- Reconciled the changed RC21 retained baseline with fresh stage-zero and platform no-drift plans.

## Try It

Read `docs/evidence/local/2026-08-14/d7-control-plane.json`; rerender from the example in
`infra/local/README.md` only when starting a new disposable local proof.

## Checks

- Protected-main CI run `31819455797` passed all five jobs on the exact local-profile correction.
- Active, rollback, and restored-active bounded verifiers passed all ten runtime checks.
- Browser and API graphs retained exact content hashes across Control restart and image switching.
- Independent cleanup queries found zero Compose containers, networks, volumes, or registry containers.
- Fresh retained-GCP stage-zero and current-equivalent RC21 platform plans returned `No changes`.

## Decisions

- The pinned NAV issuer proves only synthetic protocol behavior; no real-provider claim was made.
- Keep accepted local image objects to avoid rebuilding them for the next D7 profile.
- Record only hashes and booleans; tokens, cookies, graph bodies, private keys, state, and plans stay local.

## Remaining

- Complete independent completion review, protected PR CI, merge, and exact-main verification.
- Begin the D7 Kubernetes profile in its own focused protected PR.
- Keep Phase 8's private RC21 diagnostic lane separate from D7 provider qualification.

## Review First

- `docs/evidence/local/2026-08-14/d7-control-plane.json`
- `tickets/DANDER-128-local-control-plane-deployment.md`
- `docs/control-contracts.md`
