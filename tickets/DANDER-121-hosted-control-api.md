---
id: DANDER-121
title: Add the hosted Dander Control API
status: open
component: python
epic: druff-control-plane
depends_on: [DANDER-119, DANDER-120]
created: 2026-08-13
---

## Context

Add a separately named hosted service over GraphStore and extracted Dander application operations
without widening the loopback server or duplicating semantics.

## Acceptance Criteria

- [ ] Add versioned capabilities; project/graph CRUD; validation/preview; run/status/bounded-log/
      cancel/replay; and connector/plugin/operation catalog routes.
- [ ] Reuse canonical graph, compiler, catalog, state, and provider boundaries through normalized
      application ports rather than the current GCP graph wrapper.
- [ ] Enforce sizes, pagination, idempotency, optimistic concurrency, safe errors, correlation,
      health/readiness, graceful shutdown, and lazy selected-provider imports.
- [ ] Preserve loopback behavior and pass hosted in-memory/local multi-graph tests.
- [ ] No provider payload, credential, secret value, SQL, row, or unrestricted log is exposed.

## Design

Current Cloud Run/BigQuery graph status/run/preview code remains a local compatibility adapter.
Hosted lifecycle and planning use provider-neutral application DTOs and capability diagnostics.

## Implementation Notes

_Pending._

## Review Log

_Pending._
