---
id: DANDER-104
title: Qualify the experimental Snowflake warehouse
status: completed
component: warehouse
epic: cloud-portability
depends_on: [DANDER-103]
created: 2026-08-08
---

## Context

Canonical relations and bounded Parquet artifacts are merged. The first Snowflake slice should
exercise those contracts without claiming transforms, every write mode, infrastructure, or live
support.

## Acceptance Criteria

- [x] Snowflake owns database/schema-to-`RelationRef` translation.
- [x] OAuth and key-pair configuration contain references rather than credentials.
- [x] Bounded Parquet parts load through explicit logical/binary `COPY` settings.
- [x] SCD1 publication, load history, and the exact target fence commit atomically.
- [x] Declared scalar schemas and nullable additive evolution fail closed on drift.
- [x] Temporary remote staging and local artifacts clean up after success and handled failure.
- [x] Lost fencing ownership blocks target publication.
- [x] BigQuery and PostgreSQL compatibility remains unchanged.
- [x] Full local quality suite and retained GCP no-drift pass.
- [x] Protected CI passes.

## Boundary

This ticket adds no Snowflake transforms, graphs, semi-structured fields, other write modes,
Terraform, live deployment, package publication, or support-status promotion.

## Subsequent qualification slices

Later focused PRs added all five scalar writer modes, explicit JSON-to-`VARIANT`, fenced portable
models and graphs, bounded direct/COPY selection, and operation telemetry. Before live execution,
the qualification slice also:

- [x] selects the target schema explicitly before connector-managed direct binding;
- [x] rejects `VARIANT` fields as keys, cursors, or snapshot identity;
- [x] provides a disposable-schema live harness with sanitized output and exact cleanup;
- [x] records a real Snowflake execution under an approved paid-test ceiling.

The separate shared four-warehouse result comparison passed on protected-main commit
`c0f3e2cb671eb6ddf1c34c60bc9e761d220cb9ad`. Snowflake remains experimental, views remain
unsupported, and direct thresholds retain their zero defaults until crossover evidence is recorded
in Phase 8.
