---
id: DANDER-228
title: Add Snowflake failure qualification harness
status: done
component: python
epic: cloud-portability-phase-8
depends_on: [DANDER-204, DANDER-214, DANDER-227]
created: 2026-08-17
---

## Context

Exact RC29 has protected functional Snowflake bulk, incremental, concurrency, and transform
results. The next dependency-ordered class needs a credential-free tested failure harness before a
separate objective can bind live provider names, budget, candidate identity, and execution rails.

## Acceptance Criteria

- [x] Bind bounded closed-connection, invalid-credential, stale-fence, and statement-timeout probes.
- [x] Exercise the production Snowflake connection, session, and target-fence boundaries.
- [x] Require replacement-connection recovery and rejection of an invalid OAuth credential without
  persisting credential material.
- [x] Require stale-publication rejection and explicit rollback after a warehouse statement timeout.
- [x] Remove the disposable schema on success or failure and verify zero staging residue.
- [x] Emit the normalized Phase 8 report with provider cost pending until measured usage posts.
- [x] Cover configuration bounds, approval drift, report semantics, and sanitized CLI failure
  without provider credentials.

## Design

Extend the existing Snowflake scale harness with one `failure` class. Four bounded probes close and
replace a connection, reject an in-memory invalid OAuth token, reject an older fencing token, and
force a one-second statement timeout before explicit rollback and fresh-connection readback. The
run owns one UUID-scoped schema and always removes it.

## Implementation Notes

- The harness adds no live objective, provider names, secrets, cloud mutation, or RC29 change.
- A later focused objective must bind the protected harness and reserve its own cost ceiling before
  any Snowflake resource is created.
- Connector, session, and provider-fence behavior are in scope. Launcher retry, process termination,
  state/catalog outage, and other profile-specific cases remain separate Phase 8 gates.
- The invalid token exists only in a temporary process environment entry, which is removed or
  restored before the probe returns.

## Review

### 2026-08-17 — PASS

PR #380 merged the credential-free harness as protected main `2e45ca4`; exact-main CI run
`32065584378` passed all five jobs. No live objective, provider mutation, or candidate change was
included.
