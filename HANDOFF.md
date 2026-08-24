# Morning Handoff

## Finished

- Preserved C4 as the first RC32 run to reach the corrected protected harness.
- Recorded its generic post-runtime failure, zero retries, exact RC32 identity, and exact cleanup.
- Added sanitized stage, elapsed-time, and exception-class diagnostics without provider messages.
- Bound one C5 corrective execution to the corrected harness and retained RC32 candidate.

## Try It

Run `uv run --isolated --frozen --extra dev --extra postgres pytest tests/portability/test_redshift_failure_phase8_benchmark.py -q`.

## Checks

- Focused Redshift failure tests pass.
- Ruff lint and format checks pass for the changed Python files.
- Objective JSON, canonical types, control contracts, and Git whitespace checks pass.

## Decisions

- C4 cannot classify the provider stage because its terminal message discarded the original exception boundary.
- The correction emits only stable stage names, elapsed milliseconds, and exception classes.
- The USD 0.50 cell ceiling, aggregate USD 20 ceiling, candidate, workload, and retry policy remain unchanged.

## Remaining

- Protect and merge the stage-diagnostic objective.
- Verify exact-main CI before the one paid execution.
- Run C5 once, clean every owned resource, and record all RC32 attempts together.
- Continue only the materially blocked Redshift cells.

## Review First

- `scripts/benchmarks/redshift_failure_phase8.py`
- `tests/portability/test_redshift_failure_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-failure-stage-diagnostic-corrective-objectives.json`
