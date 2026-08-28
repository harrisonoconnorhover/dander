---
id: DANDER-249
title: Select bounded Managed Spark size classes from BigQuery metadata
status: in-review
component: python
epic: control-orchestration
depends_on: [DANDER-247]
created: 2026-08-28
---

## Context

DANDER-247 compiled immutable static Spark size classes but required the caller to supply an input
estimate. DANDER-249 makes the existing Control route choose among those plans from read-only
BigQuery table metadata for the canonical graph sources.

## Acceptance Criteria

- [x] Derive the supported graph's raw BigQuery table names from canonical source bindings and sum
      bounded `tables.get` `numBytes` metadata without reading table data.
- [x] Select the smallest existing immutable Spark size class, while explicit API sizing bypasses
      estimation and metadata failure uses the existing configured default.
- [x] Record the estimate source and observation time in a versioned size decision and durable run
      snapshot while preserving canonical v4 snapshot reads.
- [x] Replay a durable idempotent API start before re-reading mutable metadata, including when the
      source size has crossed a class boundary.
- [x] Derive BigQuery estimator coordinates from the immutable Dataproc execution template and give
      the AWS Control identity only the BigQuery Metadata Viewer role.
- [x] Preserve unsized fused Fargate execution, explicit size overrides, static Spark allocation,
      and existing pipeline logic.
- [ ] Publish one exact-main immutable main runtime image while reusing the accepted DANDER-248
      Spark artifact.
- [ ] Run exactly two Managed Spark cells that prove metadata-derived small and large selection,
      results parity, durable evidence, and cleanup.

## Boundaries

- Automatic input sizing requires one exact selected environment; it does not combine with the
  existing multi-provider `auto` placement mode in this slice.
- No table-data reads, estimator registry, new graph shapes, dynamic Spark allocation, autoscaling,
  Kubernetes, job clusters, cost/locality changes, or new reconciler.
- No Fargate live rerun, extra qualification cells, status-only PR, or evidence framework.
