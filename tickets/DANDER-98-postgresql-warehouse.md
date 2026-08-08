---
id: DANDER-98
title: Implement bounded PostgreSQL SCD1 warehouse writes
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-97]
created: 2026-08-08
---

## Context

PostgreSQL is the reference portable warehouse. Its first vertical slice must prove bounded,
schema-safe destination publication before profile selection, transforms, or Kubernetes expand the
surface area.

## Acceptance Criteria

- [x] Register a lazy PostgreSQL warehouse provider without importing Psycopg in base installs.
- [x] Keep the DSN outside manifests and require TLS for runtime-created connections.
- [x] Map canonical schema v1 to PostgreSQL scalar, array, record, and JSON types.
- [x] Stream bounded batches through `COPY` without materializing the endpoint.
- [x] Preserve deterministic last-record-wins SCD1 behavior within and across batches.
- [x] Verify and commit destination fencing in the same transaction as target DML.
- [x] Drop handled staging at transaction completion and leave no permanent staging relation.
- [x] Permit only explicitly declared nullable additive columns and reject other drift.
- [x] Prove replay, empty batches, typed values, schema evolution, and cleanup on PostgreSQL 15.
- [x] Keep profile selection and PostgreSQL transforms explicitly out of this slice.

## Design

The existing `PipelineRunner` owns endpoint batching. `PostgreSQLScd1Writer` accepts one bounded
batch, streams it to a temporary table with an ordinal, reduces duplicate keys with `DISTINCT ON`,
and performs one fenced `INSERT ... ON CONFLICT`. A selected PostgreSQL runtime exposes only SCD1
and `COPY` capabilities; unsupported sandbox and transform requests fail clearly.

## Review Log

No Terraform, cloud resource, retained project, or public support claim changes in this ticket.
