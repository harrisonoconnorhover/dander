---
id: DANDER-81
title: Add explicit model dialects and portable SQL validation
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-80]
created: 2026-08-07
---

## Context

Existing transform SQL is intentionally BigQuery-specific. Cross-warehouse execution needs an
explicit portable contract rather than silently translating arbitrary provider SQL.

## Acceptance Criteria

- [x] Model metadata declares `portable`, `bigquery`, `snowflake`, `redshift`, or `postgres`.
- [x] Existing models remain exact BigQuery SQL and repository models state that contract.
- [x] Portable SQL parses to one read-only AST and rejects nodes outside a closed allowlist.
- [x] The subset covers projections, filters, joins, `UNION ALL`, aggregations, windows, casts, and
  selected scalar functions.
- [x] Physical relations must resolve from declared Dander `ref()` calls.
- [x] Explicit null ordering and unique window tie-breakers are required.
- [x] Identifier, Unicode NFC, decimal, and timestamp precision rules fail closed.
- [x] Valid portable ASTs render to BigQuery, Snowflake, Redshift, and PostgreSQL syntax.
- [x] Exact provider SQL refuses a mismatched target.
- [x] Full local validation passes.
- [ ] Protected CI passes.

## Design

Keep the authored SQL and target warehouse separate. Exact SQL is parsed only with its declared
dialect. Portable SQL uses a closed sqlglot expression allowlist; a dependency update cannot admit
a new construct without an intentional code and fixture change.

## Implementation Notes

This ticket does not route runtime materialization to a new warehouse, add provider variants, or
compile graph operations. Those remain separate, reviewable slices.

## Review Log

Pending protected review.
