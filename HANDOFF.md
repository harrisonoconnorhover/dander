# Morning Handoff

## Finished

- Bound one corrective Redshift failure execution to immutable RC32.
- Preserved the four accepted probes, 2-vCPU/4-GiB Fargate shape, and exact cleanup contract.
- Recorded the additional USD 10 aggregate authorization without changing the USD 0.50 cell ceiling.
- Added focused protection for candidate identity, budget, harness hashes, and zero retries.

## Try It

Run `uv run pytest tests/portability/test_redshift_failure_phase8_benchmark.py -q`.

## Checks

- Focused Redshift failure tests pass.
- Ruff lint and format checks pass for the changed test.
- Objective JSON parsing and Git whitespace checks pass.

## Decisions

- Failure is the smallest Redshift cell exercising the corrected shared Serverless boundary.
- The prior RC31 attempt remains failed and transfers no result.
- Automatic and provider-operation retries remain disabled.

## Remaining

- Protect and merge this objective.
- Verify exact-main CI before the one paid execution.
- Run once, clean every owned resource, and record RC31 plus RC32 evidence together.
- Continue only the materially blocked Redshift cells.

## Review First

- `docs/evidence/phase8/2026-08-24/aws-native-rc32-redshift-failure-corrective-objectives.json`
- `tests/portability/test_redshift_failure_phase8_benchmark.py`
