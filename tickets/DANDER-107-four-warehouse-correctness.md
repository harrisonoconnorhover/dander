---
id: DANDER-107
title: Gate Phase 5 on equal four-warehouse canonical rows
status: in-review
component: warehouse
epic: cloud-portability
depends_on: [DANDER-104, DANDER-105]
created: 2026-08-10
---

## Context

The re-baselined Phase 5 exit gate requires one deterministic fixture to produce equal normalized
rows across BigQuery, PostgreSQL, Snowflake, and Redshift. Existing provider qualifications prove
their individual capabilities but do not compare one shared fixture.

## Acceptance Criteria

- [x] One versioned fixture uses only the common canonical scalar intersection.
- [x] All provider runs use the existing registry, runtime, schema, writer, and qualification
      boundaries.
- [x] Canonical normalization requires equal expected rows before and after exact replay.
- [x] Comparison requires one same-commit passing record from each of the four warehouses.
- [x] Provider-specific transports, types, fallbacks, and materializations remain separate.
- [x] Evidence excludes credentials and row data and records reviewed cost ceilings.
- [x] Every provider run attempts exact owned cleanup and requires cleanup readback.
- [x] Credential-free fixture, normalization, lifecycle, evidence, and comparison tests pass.
- [ ] Same protected-main live evidence from all four providers compares equal under reviewed
      ceilings.
- [x] Retained GCP no-drift passes after the live executions.
- [x] Protected CI passes.

## Design

Keep the gate in the benchmark/qualification layer. One core fixture validates the canonical
schema, builds the selected provider's SCD1 writer, writes two waves, reads normalized results,
replays, and cleans. Thin provider sessions own readback and cleanup only. One provider evidence
record contains hashes and booleans; a separate comparison accepts exactly four records and fails
on any candidate, schema, fixture, result, count, replay, or cleanup mismatch.

## Implementation Notes

BigQuery uses load jobs, PostgreSQL uses COPY, and Snowflake/Redshift use their bounded direct
paths. Transport equality is intentionally not asserted. The CLI requires explicit ceiling and
approval arguments before any mutation. Failed runs persist only sanitized stage, exception-type,
cleanup, candidate, timestamp, and ceiling metadata; they cannot satisfy comparison.

## Review Log

Implementation review and protected CI passed on PR #182 at commit
`f1033f6652fd5deaef3778436468d2ea39b31e5c`. An authorized one-attempt run on protected-main
commit `4927d5f66c787c6d5da700baa06edcef8b4e4c6b` produced one passing PostgreSQL record and three
failed records, so no four-provider equality is claimed and paid providers were not rerun.
Provider-owned cleanup was completed and verified, and retained GCP stage-zero and platform plans
both reported exact `No changes.` The correction prompted by that attempt fixes BigQuery's 10 MiB
metadata-query minimum, expands cleanup around fence acquisition, and adds sanitized failure
evidence. Equal same-commit live evidence remains the explicit Phase 5 blocker.
