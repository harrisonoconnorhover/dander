# Morning Handoff

## Finished

- Ran exact RC27 once on a disposable one-node zonal GKE Standard 1.35.6 cluster.
- Processed 2.7248 GB in 356.685 seconds at 7,289.345 rows/second under the 256 MiB limit.
- Passed every non-cost objective with 179,863,552 bytes peak RSS, zero retries/restarts, and exact
  PostgreSQL cleanup.
- Preserved the provisional raw report and two-infrastructure-attempt ledger without claiming cost.
- Removed all owned cluster, compute, network, secret, service-account, IAM, API, and kubeconfig state.

## Try It

Review the two `gke-standard-rc27-postgresql-bounded-memory` evidence files; no live resource remains.

## Checks

- Protected objective run `31952323045` and execution-base run `31953203115` passed all five jobs.
- Candidate and reporter exited zero; post-run schema and staging counts were both zero.
- Evidence semantics, release metadata, 17 focused tests, Ruff, canonical mypy, Control drift, and
  diff review pass.

## Decisions

- Keep the report `not_evaluated` until provider-posted billing proves the USD 0.50 cost objective.
- Treat the first immutable-runtime-path error as infrastructure because candidate code never started.
- Preserve raw `catalog=postgresql`; correct it explicitly only in a later derived final report.

## Remaining

- Merge this focused evidence PR and verify exact-main CI.
- Finalize GKE cost from provider-posted billing in a fresh PR without rerunning unaffected evidence.
- Complete remaining provider/profile scale and cost cells.
- Complete Kubernetes soak and the final-candidate closure matrix.

## Review First

- `docs/evidence/phase8/2026-08-16/gke-standard-rc27-postgresql-bounded-memory-attempts.json`
- `docs/evidence/phase8/2026-08-16/gke-standard-rc27-postgresql-bounded-memory.json`
- `docs/cloud-portability-phase8-qualification.md`
