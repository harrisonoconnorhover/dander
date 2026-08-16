---
id: DANDER-212
title: Record the RC28 Azure correctness attempt
status: done
component: docs
epic: cloud-portability-phase-8
depends_on: [DANDER-211]
created: 2026-08-16
---

## Context

The protected RC28 Azure canonical objective allowed one manual candidate execution and a replay
only after manual success. The manual run reached Python and Snowflake but failed closed before any
row write because the runtime role lacked database-level `CREATE SCHEMA` privilege.

## Acceptance Criteria

- [x] Preserve exact candidate, objective, protected-main, plan, execution, and failure identity.
- [x] Record that the manual allowance was consumed and the success-conditional replay did not run.
- [x] Prove exact Snowflake and Azure cleanup without exposing credentials or private inputs.
- [x] Keep provider cost `not_evaluated` until invoice data posts.
- [x] Keep Azure qualification and support open and require a separate focused setup/preflight rail.

## Design

Add one sanitized attempt record and update only the authoritative Phase 8 status and handoff
documents. Preserve RC28 publication evidence and do not reinterpret the failure as a candidate
implementation defect.

## Implementation Notes

- The execution `dander-35e4e06fda09-hultcck` reached Python once, wrote zero rows, and did not
  replay or retry.
- Snowflake query `01c66f67-0001-b8f1-0003-c0870004e0f6` proved the missing privilege.
- Reviewed destroy plans removed 7 platform, 6 network/PostgreSQL, and 6 stage-zero resources;
  named active inventories are empty and only the expected inactive Key Vault tombstone remains.
- Azure Cost Management returned no posted rows, so cost remains pending rather than USD 0.

## Review Log

### 2026-08-16 — PASS

The record is internally consistent with the protected objective, exact provider identifiers,
candidate logs, Snowflake query history, reviewed cleanup plans, and post-cleanup inventories. It
makes no qualification, support, replay, or provider-cost claim.
