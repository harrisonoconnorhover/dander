# Morning Handoff

## Finished

- Merged read-only graph change preview and recorded run explanations through protected PR #547.
- Implemented selected-date output repair from retained raw data with single-task Cloud Run and BigQuery.
- Reused durable run identity, cancellation, history, replay, lease fencing, and conditional cleanup.
- Added preview/start contracts, publication measurements, and operator documentation with explicit support limits.

## Try It

Use the API examples in `docs/control-contracts.md` and `docs/output-repair.md`. Preview a saved graph's UTC date interval with its `If-Match` ETag, then start `/repairs` with the same revision and an `Idempotency-Key`. A repair-capable runtime and existing partitioned outputs are required. Published clients do not yet expose this flow.

## Checks

- Full local run: 2,429 passed, 56 skipped; all 28 loopback-server failures passed on retry with socket permission. The additional explanation test passed in its 17-test file.
- Focused Control/runtime/executor/deployment selection: 538 passed, 21 skipped.
- Strict typing passed across 516 files; Ruff lint/format, generated contracts, and diff checks passed.
- PR #547 protected CI passed and merged as `099fb52`; exact-main CI is running. Repair protected CI and live provider qualification remain pending.

## Decisions

- Preview stays read-only; explanations use collected evidence and retain unknown measurements.
- Repair leaves source extraction progress untouched and replaces only the selected dates, atomically per output.
- Deliver one guided workflow in Druff; additional providers and broader graph execution are separate work.

## Remaining

- Complete the repair protected PR, merge, and exact-main CI.
- Complete the bounded live repair check after the existing AWS billing session is restored; current aggregate cash exposure is unverified.
- Integrate Druff's guided flow once the requested repository exception is answered.
- Publish and consume the matching immutable contract/runtime artifacts; the complete guided journey is not delivered yet.

## Review First

- `src/dander/providers/bigquery/graph.py` and `tests/pipeline/test_date_repair.py` for scoped publication.
- `src/dander/control/cloud_run_execution_backend.py` for execution binding and cleanup.
- `docs/output-repair.md` for practical behavior and limitations.
