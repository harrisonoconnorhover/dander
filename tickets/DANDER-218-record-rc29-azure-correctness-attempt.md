---
id: DANDER-218
title: Record the RC29 Azure correctness attempt
status: done
component: docs
epic: cloud-portability-phase-8
depends_on: [DANDER-217]
created: 2026-08-17
---

## Context

The protected RC29 Azure objective allowed one manual execution and one success-conditional replay
inside a 120-minute disposable-resource lifetime. Both executions passed, but the orchestration
kept the Azure resource group active for about 431 minutes before deletion began.

## Acceptance Criteria

- [x] Preserve exact candidate, objective, protected-main, plan, execution, replay, and readback identity.
- [x] Record the pre-candidate RBAC, OAuth, and operator-IP incidents without consuming another candidate.
- [x] Prove exact named Snowflake and active Azure cleanup without exposing credentials or row data.
- [x] Fail qualification on the committed resource-lifetime limit while preserving functional results.
- [x] Keep cost pending under the USD 2 bound and require a fresh objective, not a new candidate.

## Design

Add one sanitized attempt record and update only the authoritative Phase 8 status and handoff
documents. Do not reinterpret passing candidate behavior as full qualification when a committed
safety rail failed.

## Implementation Notes

- Manual execution `dander-rc29-35e4e06fda09-3taf04d` and replay
  `dander-rc29-35e4e06fda09-tjy7z4v` both exited zero with no retry.
- Exact normalized readback matched `a56919e9…7288`; replay left three unique rows and one target
  commit in both raw and model relations.
- Reviewed destroy plans removed 7 platform, 6 network/PostgreSQL, and 6 stage-zero resources;
  named active inventories are empty and only the inactive Key Vault tombstone remains.
- Deletion began 430.91 minutes after resource-group creation, so the 120-minute maximum failed.
- ActualCost posted USD 0.0024353794 for August 17, but delayed attribution keeps the full USD 2
  conservative bound held and cost `not_evaluated`.

## Review Log

### 2026-08-17 — PASS

The record preserves the passing RC29 functional result and exact cleanup while failing the
qualification rail that did not pass. It makes no cost-pass, support, or result-transfer claim.
