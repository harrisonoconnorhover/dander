---
id: DANDER-99
title: Add transactionally fenced PostgreSQL transforms
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-98]
created: 2026-08-08
---

## Context

The PostgreSQL warehouse can ingest bounded raw records, but it is not a useful Dander warehouse
until the same model DAG can materialize governed outputs and run the existing generic assertions.

## Acceptance Criteria

- [x] Compile portable and explicitly PostgreSQL-authored read-only models for PostgreSQL.
- [x] Render database-local quoted relations without changing BigQuery defaults.
- [x] Materialize stable tables and views inside a destination-fenced transaction.
- [x] Materialize incremental models with deterministic keyed upserts and a required unique index.
- [x] Run not-null, unique, accepted-values, and relationship assertions from existing metadata.
- [x] Reject builds without lease ownership and reject stale transform tokens before publication.
- [x] Preserve sanitized test failures without returning record values.
- [x] Prove table, view, incremental, replay, assertion, and stale-owner behavior on PostgreSQL 15.
- [x] Keep graph execution, profile selection, and Kubernetes out of this slice.

## Design

`PostgreSQLTransformRunner` reuses `TransformProject` with a PostgreSQL target dialect. Each model
claims its output relation, then executes an ordered provider-native statement group between the
target-fence touch and commit operations. Assertions are read-only and use bound parameters for
accepted values.

## Review Log

No Terraform, cloud resource, retained project, or public support claim changes in this ticket.
