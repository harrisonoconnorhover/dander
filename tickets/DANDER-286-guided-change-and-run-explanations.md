---
id: DANDER-286
title: Explain unsaved graph changes and recorded run outcomes
status: in-code
component: python
depends_on: []
created: 2026-10-01
---

## Context

The guided connect, preview, run, and understand journey needs a read-only data-impact preview
distinct from infrastructure deployment, and a readable account of actual run evidence.

## Acceptance Criteria

- [x] Compare an unsaved candidate against the exact saved graph revision without mutation.
- [x] Explain downstream effects, all full-run outputs, destination moves, and output removal.
- [x] Keep row/cost estimates unknown and distinguish static impact from execution eligibility.
- [x] Explain normalized run status without invented causes or missing-as-zero measurements.
- [x] Reuse existing authentication, bounded graph parsing, lifecycle, and generated contracts.
- [x] Verify stale revision, authorization, no-save behavior, and deterministic contract generation.

## Implementation Notes

Pure modules own comparisons and explanations. Control exposes `/change-preview` for a saved
graph plus candidate and `/explanation` for a run. Published RC19 clients remain paired with their
published producer; the new source bundle requires a newly generated client from its immutable
release artifact. This ticket does not complete the overall guided journey: Druff integration and
real date-scoped output repair remain follow-on work.

## Verification

Focused comparison, explanation, HTTP, OIDC, and contract tests passed. Canonical strict typing
passed across 513 files. Ruff and generated contract drift checks passed.
