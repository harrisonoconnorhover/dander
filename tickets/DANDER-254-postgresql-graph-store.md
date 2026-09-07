---
id: DANDER-254
title: Persist Control graphs in PostgreSQL
status: done
component: python
epic: hdfs-enterprise
depends_on: [DANDER-253]
created: 2026-08-30
---

## Acceptance Criteria

- [x] Add a dedicated versioned PostgreSQL Control schema and migration ledger.
- [x] Store exact canonical graph bytes with opaque compare-and-swap revisions.
- [x] Make create/delete idempotency reservations and replay results transactional.
- [x] Preserve list, get, create, put, delete, restart, conflict, and corruption semantics.
- [x] Pass focused and live PostgreSQL 15 conformance.

## Boundaries

- No startup selection, scheduler, backup automation, or support promotion.
