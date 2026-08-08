---
id: DANDER-97
title: Add destination-side target-fence protocol
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-96]
created: 2026-08-08
---

## Context

The durable-state backend and destination warehouse may be different systems. A state-side lease
check cannot therefore be transactionally coupled to target publication.

## Acceptance Criteria

- [x] Give every state deployment a stable authority ID and positive authority epoch.
- [x] Claim a target before staging using authority, epoch, pipeline, target, run, and token.
- [x] Accept only a newer token or an exact idempotent retry from the current authority and epoch.
- [x] Verify the complete ownership tuple in the same transaction as destination DML.
- [x] Mark a successful target publication committed inside that transaction.
- [x] Implement the protocol for BigQuery and PostgreSQL destinations.
- [x] Prove that an older PostgreSQL claimant cannot publish after a newer claim.
- [x] Keep unsupported cross-backend runtime combinations fail-closed until all callers are wired.

## Design

State providers issue provider-neutral `FencingToken` values. Warehouse adapters own
`dander_target_commits` and return a `TargetFence` only after an atomic destination claim. BigQuery
prepares a parameterized transaction script. PostgreSQL exposes the same claim contract and runs
the touch, publication, and completion statements in one Psycopg transaction.

## Review Log

This ticket adds no cloud resources or Terraform. The existing PostgreSQL-state/BigQuery runtime
guard remains in place; wiring every writer and materialization path is the next focused change.
