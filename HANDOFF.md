# Morning Handoff

## Finished

- Preserved the failed RC31 Redshift incremental task, which exited 127 before Dander or data mutation.
- Verified exact cleanup of all 37 data-plane resources, transient harness resources, and remote state.
- Bound the corrective Fargate task to the exact ARM64 image and PATH-resolved Python and Dander executables.
- Kept the candidate, accepted 300,000/3,000 workload, provider shape, and zero-retry policy unchanged.
- Reserved the one corrective execution under the original combined USD 0.50 cell ceiling.

## Try It

Run `uv run pytest -q tests/portability/test_redshift_incremental_phase8_benchmark.py`.

## Checks

- Ruff lint and formatting pass for the focused harness and tests.
- All nine focused Redshift incremental harness tests pass.
- Strict typing and Control contract drift checks pass.
- The full pytest suite passes after installing the supported runtime extras.
- The failed task and exact cleanup were verified directly in AWS.

## Decisions

- Classify the absent `/app/.venv/bin` paths as an operator-launcher defect, not a Dander product defect.
- Use Fargate ARM64 because the retained immutable RC31 image is ARM64.
- Reserve USD 0.375 for the failed setup and cap the corrective execution at USD 0.125.

## Remaining

- Protect and merge the corrective objective, then verify exact-main CI.
- Run exactly one corrective task and begin cleanup immediately at terminal state.
- Record both attempts together in the final sanitized evidence.
- Continue the next eligible Phase 8 cell after the evidence merge.

## Review First

- `scripts/benchmarks/redshift_incremental_phase8.py`
- `tests/portability/test_redshift_incremental_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-22/aws-native-rc31-redshift-incremental-corrective-objectives.json`
