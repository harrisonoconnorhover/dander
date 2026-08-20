# Morning Handoff

## Finished

- Added one credential-free BigQuery bulk harness for the accepted 500,000-row narrow and
  200,000-row wide workload.
- Reused `BigQueryReplaceWriter` with 10,000-row bounded load batches and explicit zero provider
  operation retries.
- Added one RC29-bound objective with a USD 0.25 ceiling, one execution, and exact dataset cleanup.
- Verified billing is enabled, no US reservation assignment exists, and the owned dataset is absent.

## Try It

Run `uv run pytest -q tests/portability/test_bigquery_bulk_phase8_benchmark.py`.

## Checks

- Ruff lint/format, strict typing, control contracts, the full test suite, and `pip-audit` pass.
- The objective JSON loads against the exact RC29 identity, workload hash, provider coordinates,
  and protected-harness hash.
- Exact RC29 starts the mounted harness through `dander qualification-run` under the approved
  CPU, memory, read-only, and network-disabled preflight bounds.
- No BigQuery workload or provider mutation has run; protected merge and exact-main CI come first.

## Decisions

- Measure conservative gross analysis cost from completed-job `total_bytes_billed` at the published
  USD 6.25/TiB rate; apply no free-tier or credit reduction.
- Use one disposable US dataset with one-hour table expiration as a fallback, then delete and verify
  the dataset immediately after the run.
- Keep this branch limited to the BigQuery bulk cell; no other matrix result transfers.

## Remaining

- Protect the harness and objective through the five CI jobs and exact-main CI.
- Execute RC29 exactly once with zero automatic retries.
- Record the normalized sanitized report, verify provider-metered cost, and prove exact cleanup.

## Review First

- `scripts/benchmarks/bigquery_bulk_phase8.py`
- `tests/portability/test_bigquery_bulk_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-20/bigquery-rc29-bulk-throughput-objectives.json`
