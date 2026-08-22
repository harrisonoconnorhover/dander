# Morning Handoff

## Finished

- Added the exact-RC31 Redshift bulk-throughput harness.
- Bound the accepted 500,000-row narrow and 200,000-row wide COPY workloads.
- Added exact readback, the least-privilege provider usage grant, cost, and cleanup checks.
- Disabled candidate, ECS, state-machine, and provider-operation retries.
- Committed one USD 0.50 objective with a one-RPU-hour provider limit.

## Try It

Run `uv run pytest -q tests/portability/test_redshift_bulk_phase8_benchmark.py`.

## Checks

- Ruff lint and formatting pass for the focused harness and tests.
- Strict mypy passes for the focused harness and tests.
- All six focused Redshift bulk harness tests pass.
- The objective loads against the exact harness, shared harness, and RC31 identity.

## Decisions

- Use the existing disposable AWS-native data plane and one transient Fargate harness task.
- Derive provider cost from `SYS_SERVERLESS_USAGE.charged_seconds` at the bound on-demand rate.
- Keep the immutable RC31 candidate unchanged and copy its exact index into retained ECR.

## Remaining

- Protect and merge this objective, then verify exact-main CI.
- Copy the exact RC31 index once, create the owned data plane, and run one task.
- Destroy every transient harness and data-plane resource immediately after the task.
- Record the sanitized report, provider identifiers, cost, and exact cleanup.
- Continue the next eligible Phase 8 cell after the evidence merge.

## Review First

- `scripts/benchmarks/redshift_bulk_phase8.py`
- `tests/portability/test_redshift_bulk_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-22/aws-native-rc31-redshift-bulk-objectives.json`
