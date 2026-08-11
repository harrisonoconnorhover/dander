---
id: DANDER-107
title: Gate Phase 5 on equal four-warehouse canonical rows
status: completed
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
- [x] Same protected-main live evidence from all four providers compares equal under reviewed
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
evidence. Its implementation commit passed all five protected checks on PR #183. Equal
same-commit live evidence was the remaining explicit Phase 5 blocker before the run below.

The authorized 2026-08-11 run used protected-main commit
`c0f3e2cb671eb6ddf1c34c60bc9e761d220cb9ad` and the reviewed ceilings BigQuery $1,
PostgreSQL $0, Snowflake $2, and Redshift $3. All four records contain fixture hash
`23767566255eaf86140f56e3418f7b55a62d3e27859f7dc953b670d3a011d83a`, canonical-schema hash
`e7789f4068ac9564ddf83e22884bafec64b822d1e4f77d04aef4bd1feb6c4ccd`, and normalized-row hash
`86d374f2d75aeac324b72a0b7870a876fba6301c9499a7b1961fe3c25274523c` for three rows. Exact
replay and owned cleanup passed for every provider, and the
[comparison record](../docs/evidence/warehouse-correctness/2026-08-11/comparison.json) reports
`all_rows_equal=true` and `all_cleanup_verified=true`.

BigQuery reported zero owned tables; PostgreSQL's disposable container was removed; Snowflake
reported zero matching users, databases, warehouses, roles, and resource monitors; and the
Redshift Terraform state, workgroup, namespace, bucket, role, and VPC inventory returned zero.
Fresh retained GCP stage-zero and current-equivalent platform plans each reported exact
`No changes.` No provider coordinate, credential, private key, DSN, or row value is committed.
