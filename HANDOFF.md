# Morning Handoff

## Finished

- Added the exact-RC31 Redshift incremental harness for the accepted 300,000/3,000 workload.
- Bound exact readback, cursor, COPY, cost, zero-retry, and cleanup gates.
- Added fail-closed validation for the complete Redshift Serverless task-role access.
- Added focused workload, SQL, report, approval, IAM, and retry tests.
- Preserved the blocked Redshift bulk attempt and exact cleanup without rerunning it.

## Try It

Run `uv run pytest -q tests/portability/test_redshift_incremental_phase8_benchmark.py`.

## Checks

- Ruff lint and formatting pass for the focused harness and tests.
- Strict mypy passes for the focused harness and tests.
- All seven focused Redshift incremental harness tests pass.
- The objective loads against the exact harnesses, RC31 identity, workload, and USD 0.50 ceiling.

## Decisions

- Reuse the verified immutable RC31 index in retained ECR without another registry copy.
- Require both global Resource Groups Tagging API reads before Redshift Serverless authentication.
- Keep the blocked bulk cell separate from the one authorized incremental execution.

## Remaining

- Protect and merge the incremental objective, then verify exact-main CI.
- Create the owned data plane and run exactly one incremental task.
- Destroy every transient harness and data-plane resource immediately after the task.
- Record the sanitized report, provider identifiers, cost, and exact cleanup.
- Continue the next eligible Phase 8 cell after the evidence merge.

## Review First

- `scripts/benchmarks/redshift_incremental_phase8.py`
- `tests/portability/test_redshift_incremental_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-22/aws-native-rc31-redshift-incremental-objectives.json`
