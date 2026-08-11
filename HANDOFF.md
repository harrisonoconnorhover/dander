# Morning Handoff

## Finished

- Merged the BigQuery and Redshift binary correctness fixes through protected PRs #184 and #185.
- Ran BigQuery, PostgreSQL, Snowflake, and Redshift from protected-main commit `c0f3e2c`.
- Produced one equal normalized three-row hash after exact replay on all four warehouses.
- Verified provider-owned cleanup and removed all disposable PostgreSQL, Snowflake, and AWS assets.
- Recorded sanitized evidence and reconciled the Phase 5 roadmap, tickets, matrix, and limitations.

## Try It

Run `uv run python -m scripts.benchmarks.warehouse_correctness compare`, passing each committed
provider record with its own `--evidence` argument and a temporary `--output` path.

## Checks

- Four live provider records pass; the comparison reports equal rows and verified cleanup.
- Retained GCP stage-zero and current-equivalent platform plans report exact `No changes.`
- PRs #184, #185, and #186 merged through protection; final protected-main CI passed all five jobs.
- Evidence JSON shape and forbidden-field checks pass.

## Decisions

- The live proof covers only the common canonical scalar intersection.
- Snowflake, Redshift, and PostgreSQL/Kubernetes remain experimental despite Phase 5 correctness.
- Scale, throughput, crossover, cost, soak, pairwise profiles, and release qualification stay in
  Phase 8 or provider-promotion gates.

## Remaining

- Phase 6 may begin under the revised Phase 5 exit gate, but this task deliberately starts neither
  Phase 6 nor Azure implementation.
- Complete provider-promotion conformance and Phase 8 qualification before making broader support,
  scale, cost, soak, or release claims.

## Review First

- `docs/evidence/warehouse-correctness/2026-08-11/comparison.json`
- `tickets/DANDER-107-four-warehouse-correctness.md`
- `docs/warehouse-correctness-conformance.md`
