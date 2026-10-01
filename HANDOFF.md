# Morning Handoff

## Finished

- Merged read-only graph change preview and recorded run explanations through [PR #547](https://github.com/harrisonoconnorhover/dander/pull/547).
- Merged selected-date output repair from retained raw data with single-task Cloud Run and BigQuery through [PR #548](https://github.com/harrisonoconnorhover/dander/pull/548).
- Reused durable run identity, cancellation, history, replay, lease fencing, and conditional cleanup.
- Added preview/start contracts, publication measurements, and operator documentation with explicit support limits.
- Published and verified Salesforce `0.3.2` and ServiceNow `0.2.3`; merged the matching catalog, starter, and documentation through PR #550.

## Try It

Use the API examples in `docs/control-contracts.md` and `docs/output-repair.md`. Preview a saved graph's UTC date interval with its `If-Match` ETag, then start `/repairs` with the same revision and an `Idempotency-Key`. A repair-capable runtime and existing partitioned outputs are required. Published clients do not yet expose this flow.

## Checks

- Repair protected CI: **2,515 tests passed**, including PostgreSQL; one existing test-client deprecation warning.
- Strict typing passed across 516 files; Ruff, generated contracts, distribution installation, infrastructure validation, and container/secret scans passed.
- Exact-main CI passed for preview/explanation at `099fb52` and for repair at [`79aa1cb`](https://github.com/harrisonoconnorhover/dander/actions/runs/36849004513).
- No live repair workload or paid qualification ran.
- RC33 preparation: 4 release metadata tests, Ruff lint/format, metadata consistency, and contract drift checks passed; lockfile dependencies stayed fixed.
- CI exposed 5 historical Redshift fixture failures after the version bump. Fixtures now explicitly bind RC32; all 32 focused benchmark/metadata tests pass. Candidate CI must rerun.
- PR #550 passed combined protected CI. A follow-up explicitly includes installed prereleases when checking compatibility; the catalog suite also runs against the declared minimum `packaging` version in CI.

## Decisions

- Preview stays read-only; explanations use collected evidence and retain unknown measurements.
- Repair leaves source extraction progress untouched and replaces only the selected dates, atomically per output.
- Deliver one guided workflow in Druff; additional providers and broader graph execution are separate work.

## Remaining

- Complete current aggregate billing reconciliation and bounded live repair; AWS sign-in now works.
- Finish Druff's guided flow. The user approved its repository work and protected delivery.
- Publish and consume the verified RC33 GitHub integration candidate after protected merge and exact-main CI; public PyPI RC20 stays unchanged.
- Complete the protected prerelease comparison fix and final RC33 combined package check. Both connector releases are published and their downloaded wheels passed the existing 93 Salesforce and 23 ServiceNow tests.

## Review First

- `src/dander/providers/bigquery/graph.py` and `tests/pipeline/test_date_repair.py` for scoped publication.
- `src/dander/control/cloud_run_execution_backend.py` for execution binding and cleanup.
- `docs/output-repair.md` for practical behavior and limitations.
