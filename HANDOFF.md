# Morning Handoff

## Finished

- Added the minimal exact-RC29 BigQuery incremental harness.
- Bound one approved execution to 300,000 seed rows and a 3,000-row half-update/half-insert delta.
- Added focused checks for exact readback, cursor regression rejection, zero retries, cost, and cleanup.
- Committed the USD 0.25 execution objective for use only after protected merge and exact-main CI.

## Try It

Run `uv run pytest -q tests/portability/test_bigquery_incremental_phase8_benchmark.py`.

## Checks

- Local focused tests, Ruff, strict typing, control contracts, and the full suite pass.
- Protected CI and exact-main CI must pass before the authorized provider mutation.
- No BigQuery incremental execution has run from this objective branch.

## Decisions

- Reused `BigQueryIncrementalWriter` and the BigQuery bulk harness/report structure.
- Reject cursor regression in the harness before any provider mutation.
- Used provider-billed bytes at USD 6.25/TiB without applying free-tier or credit reductions.

## Remaining

- Merge the focused objective/harness PR after all five protected jobs pass.
- Confirm all five exact-main jobs pass, then run the authorized execution exactly once.
- Record sanitized evidence and exact cleanup for this cell only.

## Review First

- `scripts/benchmarks/bigquery_incremental_phase8.py`
- `tests/portability/test_bigquery_incremental_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-20/bigquery-rc29-incremental-objectives.json`
