# Morning Handoff

## Finished

- Added provider-neutral warehouse writer-mode selection while keeping hosted CLI ingestion backward-compatible.
- Implemented Snowflake replace, SCD1, SCD2, snapshot, and incremental publication on one bounded Parquet/COPY path.
- Kept every finalizer and replay-history update in the exact destination-fence transaction.
- Fixed cursor ordering, whole-stream SCD2 publication, and partial-replay replace correctness.
- Updated Snowflake compatibility and limitations without making a support claim.

## Try It

Run `uv run pytest -q tests/providers/test_snowflake_warehouse_runtime.py`, then inspect `docs/snowflake.md`.

## Checks

- Ruff format/check: passed across 328 files.
- Mypy: passed across 304 source/test files with the CI dependency profile.
- Pytest: 1,112 passed and 13 skipped (1,125 collected).
- Protected CI passed Python/PostgreSQL, dependency, Terraform, distribution/source-free install, container, config-scan, and secret-scan gates.
- Local Docker remains unhealthy because of a missing snapshot and critically low disk; Linux CI supplied the successful container evidence.

## Decisions

- One warehouse writer capability selects logical mode plus only its cursor/snapshot field.
- SCD2 and replace consume one complete bounded-memory stream; incremental ranks cursor before ordinal.
- Views, graph wiring, VARIANT, direct-write crossover, telemetry, and live proof remain separate gates.

## Remaining

- Review and merge PR #165 through protected main when approved.
- Continue the remaining Snowflake first-class slices from updated main.

## Review First

- `src/dander/providers/snowflake/writer.py`
- `src/dander/warehouse/runtime.py`
- `tests/providers/test_snowflake_warehouse_runtime.py`
