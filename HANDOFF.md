# Morning Handoff

## Finished

- Bound one AWS-native Redshift bounded-memory cell to retained immutable RC31.
- Added the accepted 2.6-million-row, 2.7248-GB logical workload and 256 MiB hard limit.
- Reused the native Redshift COPY writer, exact readback, provider cost, and cleanup helpers.
- Required a ten-to-one input ratio, at most 80% peak RSS, zero retries, and exact cleanup.

## Try It

Run `uv run pytest -q tests/portability/test_redshift_bounded_memory_phase8_benchmark.py`.

## Checks

- Four focused bounded-memory tests pass.
- Ruff lint and formatting pass for all 487 files.
- Strict typing passes for 437 source files; Control contract drift is clean.
- The full suite passes: 1,921 passed and 35 skipped.
- The locked runtime dependency audit reports no known vulnerabilities.

## Decisions

- Keep the valid 2-vCPU/4-GiB Fargate task shape while enforcing 256 MiB on the candidate container.
- Keep the cell inside the existing USD 0.50 ceiling and one-RPU-hour provider limit.

## Remaining

- Protect and merge the objective, then verify exact-main CI.
- Run exactly one candidate task and begin cleanup immediately at terminal state.
- Commit sanitized evidence only if the execution passes every gate.

## Review First

- `scripts/benchmarks/redshift_bounded_memory_phase8.py`
- `tests/portability/test_redshift_bounded_memory_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-22/aws-native-rc31-redshift-bounded-memory-objectives.json`
