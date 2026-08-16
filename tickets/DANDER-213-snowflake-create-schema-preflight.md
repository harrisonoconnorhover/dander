---
id: DANDER-213
title: Verify Snowflake staging-schema authority before qualification
status: open
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-212]
created: 2026-08-16
---

## Context

RC28 Azure qualification setup granted ownership of the configured target schema, but the writer
creates owned staging schemas and therefore also requires `CREATE SCHEMA` on the database. The
canonical Azure preflight passed without proving that authority, and the one allowed manual run
failed closed after reaching Snowflake.

## Acceptance Criteria

- [ ] Qualification setup grants only the database-level schema-creation authority the writer
  requires, scoped to the owned disposable database.
- [ ] A read-only preflight proves the runtime role can perform the required staging-schema
  lifecycle before a candidate allowance is consumed.
- [ ] Failure output identifies the missing privilege without exposing tokens, DSNs, or SQL rows.
- [ ] Focused tests and documentation distinguish setup/preflight failure from candidate failure.
- [ ] Protected review and exact-main CI pass before a replacement objective or provider mutation.

## Design

Pending focused implementation on a fresh branch from protected main.

## Implementation Notes

Pending.

## Review Log

Pending.
