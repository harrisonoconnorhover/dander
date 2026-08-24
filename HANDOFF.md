# Morning Handoff

## Finished

- Preserved the failed RC32 Redshift attempt and classified its exact harness boundary.
- Fixed invalid-COPY recovery for the real `redshift_connector` exception hierarchy.
- Added regression coverage for both supported Redshift database-error families.
- Bound one corrective execution to the corrected harness and immutable RC32 candidate.

## Try It

Run `uv run --extra dev --extra redshift --extra postgres pytest tests/portability/test_redshift_failure_phase8_benchmark.py -q`.

## Checks

- All 11 focused Redshift failure tests pass.
- Ruff lint and format checks pass for the changed Python files.
- Objective JSON parsing and Git whitespace checks pass.

## Decisions

- RC32 remains unchanged because the live failure was in the external qualification harness.
- Historical objectives remain immutable and are rejected by the corrected harness.
- The USD 0.50 cell ceiling and zero-retry contract remain unchanged.

## Remaining

- Protect and merge the harness correction and objective.
- Verify exact-main CI before the one paid execution.
- Run once, clean every owned resource, and record both RC32 attempts together.
- Continue only the materially blocked Redshift cells.

## Review First

- `scripts/benchmarks/redshift_failure_phase8.py`
- `docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-failure-harness-corrective-objectives.json`
- `tests/portability/test_redshift_failure_phase8_benchmark.py`
