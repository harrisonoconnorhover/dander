---
id: DANDER-96
title: Implement PostgreSQL durable state
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-86]
created: 2026-08-08
---

## Context

Portable launchers and warehouses need a durable state backend that is not tied to BigQuery.
PostgreSQL is the roadmap's reference implementation for leases, watermarks, history, and metadata.

## Acceptance Criteria

- [x] Select PostgreSQL state from a version 2 platform profile without storing its connection.
- [x] Apply an idempotent, advisory-locked migration and reject newer schema versions.
- [x] Use server time, atomic lease ownership, and monotonically increasing fencing tokens.
- [x] Compare-and-set watermarks atomically and reject stale boundaries.
- [x] Persist sanitized run history, reconcile interrupted runs, and retain interrupted records.
- [x] Persist deterministic JSONB metadata snapshots.
- [x] Bound connection pooling and fail boundedly when the pool is exhausted.
- [x] Preserve the implicit version 1 BigQuery state path.
- [x] Fail closed for BigQuery execution until its cross-backend destination fence exists.
- [x] Run live PostgreSQL 15 contention and state-conformance tests in protected CI.

## Design

Register one lazy `postgresql` state provider. Its manifest block stores a safe schema name, bounded
pool settings, retention, and the name of the environment variable containing the DSN. Keep all
PostgreSQL implementation and Psycopg imports inside the selected provider module. Share one pool
across small store classes implementing the existing state contracts.

## Implementation Notes

Migration 1 creates the five shared control tables in one transaction. Terminal retention excludes
active and interrupted rows. Linux keeps the audited system-libpq package path; non-Linux installs
the binary Psycopg runtime so the public extra works without local linker setup.

## Review Log

No cloud resource or retained-project mutation is part of this ticket.
