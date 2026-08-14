---
id: DANDER-200
title: Publish the Phase 8 qualification contract
status: done
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-117]
created: 2026-08-13
---

## Context

Existing benchmark records use different shapes and sometimes encode unavailable provider metrics
as zero. Phase 8 needs one fail-closed report boundary before any new scale result is accepted.

## Acceptance Criteria

- [x] Common performance metrics distinguish measured values from unavailable values.
- [x] A report records the exact candidate, provider coordinates, workload shape, approval, raw
  provider job IDs, objectives, provider metrics, and cost evidence.
- [x] `passed` rejects incomplete measurements, missing or failed objectives, and cost overrun.
- [x] `passed` requires results to match one exact independently approved objective-name set bound
  to the report's benchmark, profile, workload configuration, and immutable candidate.
- [x] Bounded-memory `passed` enforces the ten-times-input and 80-percent-peak objectives.
- [x] The current canonical and pairwise matrix records accepted lifecycle evidence separately from
  open Phase 8 qualification.
- [x] Focused tests pass locally.
- [x] Protected CI passes and the independently reviewed PR merges.

## Design

Keep best-effort runtime telemetry backward compatible. Add an explicit `RunPerformance` record and
one versioned qualification envelope; do not infer qualification from historical benchmark fields.

## Implementation Notes

- Merged through PR #269 at protected-main commit `fe325ff`; the contract remains additive to
  runtime telemetry and historical partial reports remain `not_evaluated`.
- Passed reports require measured, non-estimated USD cost evidence and provider-specific metrics
  cannot duplicate common measurement names; boolean values cannot masquerade as numeric evidence.
- An approved objective manifest binds its names and stable approval reference to the benchmark
  class, profile, workload hash, release, commit, and image digest, preventing reuse across contexts
  or omission of an applicable SLO while retaining `passed`.
- Phase 8 tickets moved to 200–207 after concurrent Druff work assigned the checkpoint's earlier
  numeric ranges before this branch could merge.
