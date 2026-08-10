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
- Distribution/source-free install and Terraform validation passed before final correctness fixes; the full Python suite passed afterward.
- Container export is locally blocked by a broken Docker snapshot and critically low disk; protected Linux CI remains required.

## Decisions

- One warehouse writer capability selects logical mode plus only its cursor/snapshot field.
- SCD2 and replace consume one complete bounded-memory stream; incremental ranks cursor before ordinal.
- Views, graph wiring, VARIANT, direct-write crossover, telemetry, and live proof remain separate gates.

## Remaining

- Commit, push, and open the focused PR.
- Require protected CI to repeat Linux tests, PostgreSQL integration, packaging, Terraform, container, and security checks.
- Merge only when required checks and review pass.
- Continue the remaining Snowflake first-class slices from updated main.

## Review First

- `src/dander/providers/snowflake/writer.py`
- `src/dander/warehouse/runtime.py`
- `tests/providers/test_snowflake_warehouse_runtime.py`
