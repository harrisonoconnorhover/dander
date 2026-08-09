# Morning Handoff

## Finished

- Forward-ported the accepted `0.7.1` BigQuery run-history migration fix to current main.
- Read the immediate table schema before considering additive history columns.
- Skip no-op migration DDL when the schema is already current.
- Batch all genuinely missing nullable columns into one race-safe `ALTER TABLE` statement.
- Preserve fail-closed behavior when the single migration statement fails.

## Try It

Run `uv run pytest tests/state/test_run_history.py -q`; current schemas emit no ALTER and sparse
legacy schemas emit one bounded additive migration.

## Checks

- Full suite passed (`1,038` passed, `13` environment-selected skips); Ruff lint/format and strict
  mypy passed.
- Stable `0.7.1` candidate protected CI and retained GCP acceptance passed before this forward-port.
- Current main already locks GitPython `3.1.58`; no dependency change was needed.

## Decisions

- Keep the functional fix identical to the accepted maintenance release.
- Do not mix release metadata or provider implementation work into the forward-port.
- Preserve immediate schema failure propagation rather than masking migration errors.

## Remaining

- Run protected Linux CI and merge the focused PR if clean.
- Rebase the provider PR stack on the corrected main before merging it.

## Review First

- `src/dander/state/run_history.py`
- `tests/state/test_run_history.py`
