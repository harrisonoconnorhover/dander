---
id: DANDER-100
title: Compose the native PostgreSQL runtime profile
status: in-review
component: python
epic: cloud-portability
depends_on: [DANDER-96, DANDER-97, DANDER-98, DANDER-99]
created: 2026-08-08
---

## Context

PostgreSQL state, destination fencing, bounded SCD1 ingestion, and transforms existed as isolated
capabilities. A version 2 deployment could not yet select and execute them as one Dander pipeline.

## Acceptance Criteria

- [x] Select BigQuery or PostgreSQL warehouse configuration through the version 2 profile schema.
- [x] Let `dander run` and `dander runtime execute` select one named deployment explicitly.
- [x] Require a GCP project only when the selected providers actually use GCP.
- [x] Claim PostgreSQL ingestion targets before extraction and preserve legacy BigQuery behavior.
- [x] Compile metadata with the selected warehouse dialect.
- [x] Run bounded ingestion, replay, transforms, assertions, metadata, history, cursors, and leases
      together against PostgreSQL 15.
- [x] Keep PostgreSQL-state/BigQuery-warehouse execution fail-closed until its writers adopt the
      destination-side target fence.
- [x] Make no Terraform, cloud-resource, or hosted-support change.

## Design

The resolved project retains the non-secret warehouse provider block and separates the warehouse
catalog from the optional GCP project. `PipelineRunner` claims targets only for writers that declare
the destination-fence requirement. The selected transform runner supplies its SQL dialect to the
metadata compiler.

## Implementation Notes

The PostgreSQL native integration uses one disposable database with distinct raw, model, and state
schemas. It forces one-row batches and replays the inclusive cursor boundary without duplicates.

## Review Log

Protected CI and pull-request review remain before merge. Kubernetes/Helm is the next separate
Phase 4 slice.
