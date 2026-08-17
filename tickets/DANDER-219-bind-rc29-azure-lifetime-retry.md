---
id: DANDER-219
title: Bind the RC29 Azure lifetime retry objective
status: in_progress
component: infrastructure
epic: cloud-portability-phase-8
depends_on: [DANDER-218]
created: 2026-08-17
---

## Context

RC29 passed the Azure manual execution, replay, and exact normalized Snowflake readback. The result
did not qualify because the disposable resource group exceeded its 120-minute lifetime. The same
immutable candidate needs one procedural rerun with interactive authorization completed first.

## Acceptance Criteria

- [x] Bind unchanged RC29 and its exact digest to a fresh Azure, PostgreSQL, and Snowflake namespace.
- [x] Verify Snowflake interactive access before the first disposable object and obtain the scoped
  runtime token before Azure provisioning.
- [x] Require immediate teardown if any later interactive blocker appears and start cleanup by 75 minutes.
- [x] Preserve one manual run, one success-conditional replay, zero automatic retries, exact cleanup,
  and the 120-minute maximum lifetime.
- [ ] Protected review and exact-main CI pass before any owned provider resource is created.

## Design

Reuse the known-good immutable RC29 artifact without code or candidate changes. Create Snowflake's
fresh named objects only after a read-only interactive-session check, obtain the runtime-role token
in memory, and only then begin Azure provisioning. Start cleanup by minute 75 so Azure retains 45
minutes of provider-deletion margin.

## Implementation Notes

- Fresh ACR, storage-account, Key Vault, PostgreSQL, and resource-group names are available.
- A signed-in Snowflake worksheet is presently visible, but execution must freshly verify the
  account and operator immediately before the first owned-object mutation.
- The new USD 2 conservative bound leaves USD 3.75 unreserved after both prior Azure bounds and the
  RC29 publication reserve remain held.
- An Azure aggregate cost recheck was throttled; the latest attributable RC29 row is USD 0.0024353794,
  so the full conservative bounds remain in force.
