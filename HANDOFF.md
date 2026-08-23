# Morning Handoff

## Finished

- Bound one AWS-native Redshift failure cell to retained immutable RC31.
- Added four bounded credential, failed-COPY, recovery, and stale-publication probes.
- Reused the native writer, fencing, provider-cost, and exact-cleanup paths.
- Preserved one candidate execution and zero automatic or provider-operation retries.

## Try It

Run `uv run pytest -q tests/portability/test_redshift_failure_phase8_benchmark.py`.

## Checks

- Seven focused failure-harness tests pass.
- Ruff lint and formatting pass for all 489 files.
- Strict typing passes for 438 source files; control contract drift is clean.
- The full suite passes: 1,928 passed and 35 skipped.
- The locked runtime dependency audit reports no known vulnerabilities.

## Decisions

- Use one deliberately invalid Parquet COPY followed by exact object/table cleanup and a valid
  native COPY recovery.
- Keep the valid 2-vCPU/4-GiB Fargate task shape and USD 0.50 cell ceiling.

## Remaining

- Protect and merge the objective, then verify exact-main CI.
- Run exactly one candidate task and begin cleanup immediately at terminal state.
- Commit sanitized evidence only if the execution passes every gate.

## Review First

- `scripts/benchmarks/redshift_failure_phase8.py`
- `tests/portability/test_redshift_failure_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-22/aws-native-rc31-redshift-failure-objectives.json`
