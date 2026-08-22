# Morning Handoff

## Finished

- Preserved the zero-candidate initial setup attempt and its exact cleanup.
- Corrected the invalid 2-vCPU Fargate shape from 2 GiB to 4 GiB.
- Added fail-closed objective validation and focused regression coverage.
- Kept RC31, the accepted workload, provider shape, and USD 0.50 ceiling unchanged.
- Reserved the one authorized zero-retry corrective candidate execution.

## Try It

Run `uv run pytest -q tests/portability/test_redshift_bulk_phase8_benchmark.py`.

## Checks

- Ruff lint and formatting pass for the focused harness and tests.
- Strict mypy passes for the focused harness and tests.
- All seven focused Redshift bulk harness tests pass.
- The corrective objective loads against the exact harness, shared harness, and RC31 identity.

## Decisions

- Treat the invalid Fargate CPU/memory pair as an operator-harness defect before candidate startup.
- Reuse the verified immutable RC31 index in retained ECR without another registry copy.
- Include the initial setup and corrective execution together in final evidence.

## Remaining

- Protect and merge the corrective objective, then verify exact-main CI.
- Create the owned data plane and run exactly one corrective task.
- Destroy every transient harness and data-plane resource immediately after the task.
- Record both attempts, the sanitized report, provider identifiers, cost, and exact cleanup.
- Continue the next eligible Phase 8 cell after the evidence merge.

## Review First

- `scripts/benchmarks/redshift_bulk_phase8.py`
- `tests/portability/test_redshift_bulk_phase8_benchmark.py`
- `docs/evidence/phase8/2026-08-22/aws-native-rc31-redshift-bulk-corrective-objectives.json`
