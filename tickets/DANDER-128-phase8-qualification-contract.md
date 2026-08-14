---
id: DANDER-128
title: Publish the Phase 8 qualification contract
status: in-review
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
- [x] Bounded-memory `passed` enforces the ten-times-input and 80-percent-peak objectives.
- [x] The current canonical and pairwise matrix records accepted lifecycle evidence separately from
  open Phase 8 qualification.
- [x] Focused tests pass locally.
- [ ] Protected CI passes and the independently reviewed PR merges.

## Design

Keep best-effort runtime telemetry backward compatible. Add an explicit `RunPerformance` record and
one versioned qualification envelope; do not infer qualification from historical benchmark fields.

## Implementation Notes

- Rebased onto protected-main descendant `6af0e64`; the contract remains additive to runtime
  telemetry and historical partial reports remain `not_evaluated`.
- Passed reports require measured, non-estimated USD cost evidence and provider-specific metrics
  cannot duplicate common measurement names.
- Phase 8 tickets moved from 118–125 to 128–135 because current `main` assigned the former range to
  the Druff control-plane work before this checkpoint was integrated.
