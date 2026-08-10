# Morning Handoff

## Finished

- Added endpoint-wide Snowflake direct/COPY selection with bounded threshold-plus-one buffering.
- Added explicit JSON-to-VARIANT mapping with canonical text staging and `PARSE_JSON` publication.
- Added truthful writer load/publication telemetry with transport, query IDs, rows, bytes, duration, and warehouse.
- Kept legacy graph/BigQuery planning fail-closed for provider-selected direct transport.

## Try It

Run `uv run pytest -q tests/providers/test_snowflake_warehouse_runtime.py`.

## Checks

- Ruff format/check: passed across 328 files; mypy: passed across 304 source/test files.
- Pytest: 1,122 passed and 13 skipped from 1,135 collected tests.
- Wheel/sdist inspection and both source-free package installs: passed.
- Terraform validation/tests and Helm lint/render passed except the local Fargate module test, whose 782 MB provider could not initialize with under 1 GB free.
- Local Docker/PostgreSQL conformance remains for protected Linux CI because Docker Desktop is unresponsive.

## Decisions

- Direct thresholds default to zero until a live Snowflake crossover is measured.
- Direct/COPY selection occurs once for the complete endpoint; ordinary runtime batching is disabled for Snowflake writers.
- Only canonical JSON with `snowflake/fallback=variant` is admitted; ARRAY and RECORD remain rejected.

## Remaining

- Push a focused draft PR and require protected Linux CI, including PostgreSQL, container, Terraform, and scans.
- Measure the direct/COPY crossover during later live Snowflake qualification.
- Keep transforms/views/graphs and query-history enrichment in separate Snowflake slices.

## Review First

- `src/dander/providers/snowflake/writer.py`
- `src/dander/providers/snowflake/runtime.py`
- `tests/providers/test_snowflake_warehouse_runtime.py`
