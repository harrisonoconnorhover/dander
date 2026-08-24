# Morning Handoff

## Finished

- Preserved C6 as the exact-RC32 attempt with zero retries and exact cleanup.
- Recorded that all functional failure probes passed before delayed cost metadata stopped the report.
- Replaced the one fixed cost read with a bounded 300-second metadata observation window.
- Added regression coverage proving delayed reads do not repeat the accepted workload.
- Added one exact-RC32 C7 corrective objective within the existing aggregate approval.

## Try It

Run `uv run --isolated --frozen --extra dev --extra postgres pytest tests/portability/test_redshift_failure_phase8_benchmark.py -q`.

## Checks

- Focused Redshift failure suite passes with 18 tests.
- Full test suite, Ruff lint/format, and strict types pass.
- Control contracts, objective JSON, and Git whitespace pass.

## Decisions

- AWS reports `charged_seconds` only after transactions end and records usage in one-minute intervals.
- Cost observation may repeat only the aggregate metadata read; it never repeats a workload mutation.
- RC32, the failure workload, zero-retry policy, USD 0.50 objective ceiling, and USD 20 aggregate ceiling remain unchanged.

## Remaining

- Protect and merge the focused C7 correction/objective.
- Verify exact-main CI before the one C7 execution.
- Run C7 once, clean every owned resource, and record all RC32 failure attempts together.
- Continue only the Redshift cells materially blocked by the shared connection boundary.

## Review First

- `scripts/benchmarks/redshift_failure_phase8.py`
- `tests/portability/test_redshift_failure_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-failure-cost-observation-corrective-objectives.json`
