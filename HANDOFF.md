# Morning Handoff

## Finished

- Preserved both failed RC32 Redshift attempts with their distinct harness and launcher boundaries.
- Recorded exact cleanup of the launcher attempt, including 37 Terraform resources and 13 state versions.
- Bound one corrective execution to `/tmp/harness` as both working directory and Python import root.
- Kept the protected harness and immutable RC32 candidate unchanged.

## Try It

Run `uv run --isolated --frozen --extra dev --extra postgres pytest tests/portability/test_redshift_failure_phase8_benchmark.py -q`.

## Checks

- Focused Redshift failure tests pass.
- Ruff lint and format checks pass for the changed test.
- Objective JSON, canonical types, control contracts, and Git whitespace checks pass.

## Decisions

- RC32 remains unchanged because the latest failure occurred before Dander runtime construction.
- The historical objective remains immutable; the new objective changes only the external launcher import root.
- The USD 0.50 cell ceiling, aggregate USD 20 ceiling, and zero-retry contract remain unchanged.

## Remaining

- Protect and merge the launcher-corrective objective.
- Verify exact-main CI before the one paid execution.
- Run once, clean every owned resource, and record all RC32 attempts together.
- Continue only the materially blocked Redshift cells.

## Review First

- `docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-failure-launcher-corrective-objectives.json`
- `tests/portability/test_redshift_failure_phase8_benchmark.py`
- `HANDOFF.md`
