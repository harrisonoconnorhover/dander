---
id: DANDER-115
title: Control OCI Container Instances lifecycle
status: in-review
component: python
epic: cloud-portability-phase-7
depends_on: [DANDER-84]
created: 2026-08-12
---

## Context

OCI Container Instances has no native batch-job controller. Phase 7 therefore requires one narrow
Function that projects the existing provider-neutral runtime contract without adding warehouse,
state, SQL, ingestion, planner, or orchestration semantics.

## Acceptance Criteria

- [x] Object Storage conditional writes provide deterministic delivery idempotency, one active run
  per pipeline, sanitized terminal history, and bounded per-attempt logs.
- [x] Each attempt creates a new private, resource-principal Container Instance with restart policy
  `NEVER`; only runtime exit code 75 receives a bounded whole-task retry.
- [x] The controller owns the absolute deadline, interruption, replay, log capture, and deletion.
- [x] Per-pipeline UTC Resource Scheduler and lifecycle-event rules invoke only the matching
  Function; Function dynamic-group membership uses exact Function OCIDs.
- [x] Runtime processes resolve only validated OCI Vault references, never put values in Terraform
  or Function configuration, and remove injected values after execution.
- [x] Manifest-bound `run`, `status`, `logs`, `cancel`, and `replay` operations use only an expiring
  SecurityToken profile and require confirmation for mutations.
- [x] The source-free Python 3.12 controller recipe installs one exact Dander wheel and uses pinned
  `linux/amd64` Function base images matching Terraform's `GENERIC_X86` application.
- [x] Focused Python and Terraform tests pass without OCI access.
- [ ] Protected CI passes and this implementation merges before any live OCI deployment.
- [ ] A separately approved live proof covers schedule, retry, overlap, interruption, replay,
  secret rotation, rollback, cleanup, and no drift.

## Design

Keep lifecycle state provider-local: immutable projection objects and compare-and-swap active run
records in Object Storage, one short Function shim, and one run-scoped Container Instance per
attempt. The existing runtime command remains the task boundary. Provider output is normalized at
the adapter edge and repository records reject unknown fields.

## Implementation Notes

The controller reserves five minutes of the one-hour detached Function ceiling, caps tasks at
3,300 seconds, and retains at most 256 KiB of logs per attempt. Resource Scheduler's provider limit
is represented honestly as UTC-only and no more frequent than hourly. Cross-cloud identity is not
implemented and static keys remain rejected.

## Review Log

Protected review and live acceptance remain pending.
