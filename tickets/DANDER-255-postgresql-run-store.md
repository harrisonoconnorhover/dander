---
id: DANDER-255
title: Persist hosted runs and attempts in PostgreSQL
status: done
component: python
epic: hdfs-enterprise
depends_on: [DANDER-254]
created: 2026-08-30
---

## Acceptance Criteria

- [x] Atomically claim scoped submission idempotency and detect conflicting reuse.
- [x] Save canonical run snapshots through opaque compare-and-swap revisions.
- [x] Append immutable attempt history with exact replay and conflict behavior.
- [x] Persist mutation idempotency and deterministic bounded pagination.
- [x] Pass strict typing plus focused and live PostgreSQL 15 conformance.

## Boundaries

- The injected database owns its pool; composition must close it exactly once.
- No scheduler, startup selection, Kubernetes mutation, backup automation, or support promotion.
