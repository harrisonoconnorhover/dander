---
id: DANDER-214
title: Preserve portable identifiers in Snowflake rendering
status: done
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-212, DANDER-213]
created: 2026-08-17
---

## Context

The second RC28 Azure correctness attempt passed the complete provider and canonical preflight,
then failed deterministically in Snowflake. Dander persists canonical columns as quoted lowercase
identifiers, while portable model SQL rendered logical columns and aliases unquoted. Snowflake
uppercased those references and rejected the lowercase source column before publication.

## Acceptance Criteria

- [x] Snowflake rendering quotes every validated portable logical identifier without changing the
  authored provider-neutral subset or the other warehouse dialects.
- [x] Focused tests cover lowercase source columns, aliases, qualified columns, joins, and CTEs.
- [x] Existing transform, strict typing, lint, contract-drift, and proportionate full checks pass.
- [x] Protected review and exact-main CI pass before publishing a replacement immutable candidate.
- [x] Accepted unaffected evidence remains valid; only the materially affected Azure correctness
  lane reruns on the replacement candidate.

## Design

Clone the already validated portable SQL AST only for Snowflake rendering and mark its identifiers
quoted before dialect serialization. Keep provider-native SQL behavior unchanged and add no live
provider operation to the correction PR.

## Implementation Notes

- RC28 execution `dander-35e4e06fda09-sme7gpt` reached Python and Snowflake with canonical preflight
  passing, then returned non-retryable `transform_failed` and Snowflake error 904.
- The source metadata proves quoted lowercase `id` and `title` exist and uppercase `ID` does not.
- RC28 is immutable and must not rerun; a replacement candidate follows the focused correction.
- Snowflake now renders a cloned portable AST with every identifier quoted. Regression coverage
  proves lowercase columns, aliases, qualified joins, CTEs, transform compilation, and unchanged
  BigQuery, Redshift, and PostgreSQL rendering.

## Review Log

- 2026-08-17 — PASS: PR #360 merged as protected main `a2b72f8`; exact-main CI run
  `31987252875` passed all five jobs with no unresolved review thread.
