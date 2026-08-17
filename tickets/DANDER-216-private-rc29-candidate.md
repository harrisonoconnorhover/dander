---
id: DANDER-216
title: Prepare the private RC29 Phase 8 candidate
status: in_progress
component: release
epic: cloud-portability-phase-8
depends_on: [DANDER-214, DANDER-215]
created: 2026-08-17
---

## Context

The protected source now quotes validated portable identifiers for Snowflake and carries the
container security refresh required by the protected image gate. The materially affected Azure
correctness lane needs one replacement source-free candidate; immutable RC28 must not rerun.

## Acceptance Criteria

- [x] Package and lock metadata prepare private `0.9.0rc29` without changing public RC20.
- [x] Release notes name only the Snowflake identifier correction and container security refresh
  while keeping qualification and support gates open.
- [ ] A protected authorization record binds publication to the exact preparation commit and PR,
  with a conservative incremental publication reserve no greater than USD 0.25.
- [ ] Wheel, source distribution, amd64/arm64 image, SBOM, provenance, and source-free inspection
  pass before candidate evidence is accepted.
- [ ] Protected CI and review pass before preparation merge; exact-main CI passes before
  publication.

## Design

Use the existing private source-free publication path from exact protected main. Do not publish
from this branch. After preparation merges and exact-main CI passes, build and inspect one
amd64/arm64 GAR index in a separate publication lane, then record sanitized evidence independently.

## Implementation Notes

- PR #360 merged the deterministic application correction as protected main `a2b72f8`; exact-main
  run `31987252875` passed all five jobs before this preparation.
- Preserve accepted RC27/RC28 and other unaffected evidence. Only Azure correctness reruns before
  the eventual final-candidate closure matrix.
- Automatic provider retry remains disabled. This preparation performs no registry or live-provider
  mutation and makes no cost, qualification, public-release, or support claim.
