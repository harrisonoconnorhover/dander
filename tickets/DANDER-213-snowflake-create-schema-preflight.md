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

Extend the existing canonical Azure preflight rather than add an optional side command. It validates
the complete Snowflake warehouse configuration, connects with the configured OAuth runtime role,
checks the active database/role/warehouse, and reads only that role's explicit grants. Qualification
setup adds the single missing `CREATE SCHEMA` grant on the disposable database; the creator's schema
ownership supplies the matching cleanup authority.

## Implementation Notes

- `SHOW GRANTS TO ROLE` is read-only and does not require schema creation. Dander retains no grant
  rows and converts connector failures to a stable sanitized message.
- Successful CLI output contains only configured database, role, warehouse, and two pass booleans.
- Missing authority names `CREATE SCHEMA`, the database, and the role before any Azure job starts.
- No Azure, Snowflake, PostgreSQL, Terraform, or candidate operation is part of this correction PR.

## Review Log

Pending.
