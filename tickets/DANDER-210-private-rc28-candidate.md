---
id: DANDER-210
title: Prepare the private RC28 Phase 8 candidate
status: in_progress
component: release
epic: cloud-portability-phase-8
depends_on: [DANDER-208, DANDER-209]
created: 2026-08-16
---

## Context

The protected source now includes the stable qualification entrypoint and the Azure immutable
runtime platform handoff. Azure Phase 8 execution needs one private source-free candidate built
from exact protected main before any objective can bind to those corrections.

## Acceptance Criteria

- [x] Package and lock metadata prepare private `0.9.0rc28` without changing public RC20.
- [x] Release notes name only the two current-source qualification rails and keep support gates open.
- [ ] A protected authorization record binds publication to the exact preparation commit and PR.
- [ ] Wheel, source distribution, amd64/arm64 image, SBOM, provenance, and source-free inspection
  pass before candidate evidence is accepted.
- [ ] Protected CI and review pass before preparation merge; exact-main CI passes before publication.

## Design

Use the existing private source-free publication path and cumulative Phase 8 ceiling. Do not
publish from this branch. After protected preparation merges and exact-main CI passes, build from
that exact commit in a separate publication lane and record sanitized evidence in another PR.

## Implementation Notes

- RC27 evidence is preserved. The new rails materially affect Azure and future hosted harness
  invocation; unaffected accepted evidence reruns only in the eventual final-candidate matrix.
- This preparation is not a candidate publication, live-provider result, cost result, public
  release, or support claim.
