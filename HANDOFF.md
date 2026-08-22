# Morning Handoff

## Finished

- Added a bulk-only mode to the existing PostgreSQL Phase 8 harness.
- Preserved the existing all-class and correctness-only behavior.
- Bound exact RC31 to the accepted 500,000-row narrow and 200,000-row wide workload.
- Enforced one candidate execution, zero automatic/provider retries, and exact cleanup.
- Kept provider-posted cost as the only delayed gate after functional success.

## Try It

Run `uv run pytest -q tests/portability/test_postgresql_phase8_benchmark.py`.

## Checks

- Focused harness tests cover the hosted-GKE bulk cost-pending and posted-cost states.
- The harness rejects objective, hash, retry-policy, and cost-ceiling drift.
- Provider mutation remains blocked until this objective merges and exact-main CI passes.

## Decisions

- Reused the PostgreSQL runtime, COPY writer, and existing bulk fixture.
- Added no new benchmark abstraction or candidate.
- Reserved the existing USD 0.50 GKE per-cell ceiling.

## Remaining

- Merge the focused objective after all five protected jobs pass.
- Run exactly one RC31 GKE bulk execution and clean every owned resource.
- Record sanitized evidence; reconcile delayed provider cost without rerunning.
- Continue the smallest eligible Phase 8 gate from protected main.
- Snowflake bounded-memory remains blocked on role-scoped interactive authorization.

## Review First

- `scripts/benchmarks/postgresql_phase8.py`
- `tests/portability/test_postgresql_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-21/gke-standard-rc31-postgresql-bulk-objectives.json`
- `tickets/DANDER-204-phase8-scale-matrix.md`
