---
id: DANDER-86
title: Route BigQuery durable state through provider capabilities
status: completed
component: state
epic: cloud-portability
depends_on: [DANDER-85]
created: 2026-08-08
---

## Context

BigQuery warehouse execution used the provider registry, but hosted runs still constructed leases,
watermarks, run history, and metadata stores directly in the CLI. Durable state needs one selected
runtime and explicit schema versions before another backend can satisfy the same correctness
contract.

## Acceptance Criteria

- [x] Version 1 projects retain implicit BigQuery state and version 2 profiles carry the selection.
- [x] The BigQuery state implementation loads only after its factory is selected.
- [x] One runtime composes leases, watermarks, history, optional metadata, migrations, and declared
  correctness capabilities.
- [x] Existing datasets and table identities remain unchanged.
- [x] Schema version 1 is recorded only after shared tables migrate successfully.
- [x] Re-running a completed migration performs no table-schema mutations.
- [x] Hosted CLI composition uses the selected runtime; SQLite sandbox behavior remains unchanged.
- [x] Focused state, project migration, and CLI composition tests pass.
- [x] Full local validation and isolated GCP no-drift pass.
- [x] Protected CI passes.

## Design

Keep the established BigQuery stores and expose their existing idempotent table setup as explicit
migrations. A small `_dander_state_schema` ledger records only completed migration versions. Lease
tables remain lazy and per pipeline because changing their identity would add migration risk with
no portability benefit in this slice.

## Review Log

Merged through protected PR #117 as `825472095efd1a8d5127ac3db16bdc3f2dbfdb2a`.
