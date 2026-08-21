# Morning Handoff

## Finished

- Added a correctness-only mode to the existing PostgreSQL Phase 8 harness.
- Preserved the existing all-class local behavior and zero-cost contract.
- Bound exact RC31 and the accepted seven-input/three-output fixture to one GKE objective.
- Enforced one candidate execution, zero automatic/provider retries, and exact cleanup.
- Kept provider-posted cost as the only post-execution delayed gate.

## Try It

Run `uv run pytest -q tests/portability/test_postgresql_phase8_benchmark.py`.

## Checks

- Focused harness tests cover GKE objective binding and pending/posted cost behavior.
- The harness rejects hash drift, extra execution attempts, automatic retry, and provider retry.
- Provider mutation remains blocked until this objective merges and exact-main CI passes.

## Decisions

- Reused the existing PostgreSQL runtime, COPY writer, fence, and correctness fixture.
- Added no new benchmark abstraction or candidate.
- Reserved the existing USD 0.50 GKE per-cell ceiling.

## Remaining

- Merge the focused objective after all five protected jobs pass.
- Run exactly one RC31 GKE correctness execution and clean every owned resource.
- Record sanitized evidence; reconcile delayed provider cost without rerunning.
- Snowflake bounded-memory remains blocked on role-scoped interactive authorization.

## Review First

- `scripts/benchmarks/postgresql_phase8.py`
- `tests/portability/test_postgresql_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-21/gke-standard-rc31-postgresql-correctness-objectives.json`
- `tickets/DANDER-204-phase8-scale-matrix.md`
