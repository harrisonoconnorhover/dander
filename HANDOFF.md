# Morning Handoff

## Finished

- Added unrendered `RelationRef` coordinates and the provider `RelationCodec` boundary.
- Added canonical schema v1 for exact scalars, decimals, timestamps, arrays, and records.
- Added ordered provider extensions and duplicate/shape validation.
- Added a fail-closed, one-way mapper for legacy BigQuery raw and writer fields.
- Exposed canonical views without changing authored schemas or BigQuery runtime behavior.

## Try It

Call `endpoint.canonical_raw_schema()` or `target.canonical_schema`; use
`target.relation_ref` to pass coordinates to a future provider codec without rendering SQL.

## Checks

- All 861 tests, Ruff, formatting, and strict mypy across 197 source files passed.
- Wheel/sdist build and inspection plus both Terraform-root validations passed.
- The isolated GCP plan reported `No changes`; both schedules stayed paused and no apply ran.

## Decisions

- Decimal precision/scale and timestamp timezone semantics are mandatory.
- BigQuery `REPEATED` maps to a required canonical array while retaining its original mode.
- Provider-only types require an explicit fallback; silent lossy mapping is forbidden.

## Remaining

- Open and merge the focused canonical-schema PR after protected CI passes.
- Add portable SQL/dialect boundaries from merged main in the next PR.

## Review First

- `src/dander/warehouse/contracts.py`
- `src/dander/warehouse/bigquery_compat.py`
- `tests/warehouse/test_contracts.py`
