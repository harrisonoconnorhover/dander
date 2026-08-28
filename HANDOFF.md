# Morning Handoff

## Finished

- Added metadata-only BigQuery input estimation for canonical graph source tables.
- Connected estimates to existing immutable Spark size classes with explicit/default fallbacks.
- Made no-sizing API retries replay durable runs before mutable metadata is read again.
- Versioned sizing evidence and run snapshots while preserving canonical v4 reads.
- Added the AWS Control BigQuery Metadata Viewer role and regenerated API contracts.

## Try It

Run `uv run pytest -q tests/control/test_bigquery_input_size_estimator.py
tests/control/test_run_lifecycle.py tests/control/test_s3_run_store.py`.

## Checks

- Repository-wide Ruff format and lint passed.
- Strict repository typing passed for 474 source files.
- Full Pytest passed with only the existing Starlette deprecation warning.
- Focused DANDER-249 regression tests passed after the final review correction.
- Control contract generation/drift validation passed.
- Isolated Terraform formatting and module validation passed.
- The final adversarial finding was corrected without another review checkpoint.

## Decisions

- Sum BigQuery `tables.get` `numBytes` metadata; never read table rows.
- Explicit sizing bypasses estimation; metadata failure uses the existing default.
- Limit automatic estimation to one exact environment and retain static Spark allocation.

## Remaining

- Open and merge one protected functional PR; confirm exact-main CI.
- Publish one exact-main main image while reusing the accepted DANDER-248 Spark artifact.
- Run exactly two Spark sizing cells, capture results, and clean up.

## Review First

- `src/dander/control/bigquery_input_size_estimator.py`
- `src/dander/control/run_lifecycle.py`
- `src/dander/control/orchestration_serialization.py`
