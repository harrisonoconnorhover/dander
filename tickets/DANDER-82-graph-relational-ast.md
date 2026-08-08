---
id: DANDER-82
title: Compile graph operations through the relational AST
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-81]
created: 2026-08-07
---

## Context

The graph compiler assembled BigQuery CTE, join, cast, filter, and operation strings directly.
Models now use a provider-neutral sqlglot boundary, so graph nodes need the same relational shape.

## Acceptance Criteria

- [x] Graph source relations remain structured catalog/namespace/name coordinates.
- [x] Sources, CTEs, projections, joins, conditions, casts, and operations compile as AST nodes.
- [x] Scalar expression validation returns the parsed expression instead of rendered SQL.
- [x] BigQuery remains the default rendering and current writer runtime remains unchanged.
- [x] The returned AST is isolated from caller mutation.
- [x] Cast-free graphs render and parse for BigQuery, Snowflake, Redshift, and PostgreSQL.
- [x] Targets that cannot preserve safe-cast behavior fail clearly before rendering.
- [x] Existing graph bridge and compiler tests remain green.
- [x] Full local validation passes.
- [ ] Protected CI passes.

## Design

Use sqlglot's expression tree as the shared relational representation rather than adding a second
custom AST. Keep provider execution dispatch out of this ticket; rendering syntax is not support.

## Implementation Notes

Graph type tokens and expression input remain BigQuery-compatible during migration. Generic target
writers, provider type mapping, and runtime dispatch are separate adapter work.

## Review Log

Pending protected review.
