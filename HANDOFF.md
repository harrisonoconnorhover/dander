# Morning Handoff

## Finished

- Removed the unsupported explicit `NONE` autotuning scenario from Managed Spark submissions.
- Preserved the fixed executor count and explicit `spark.dynamicAllocation.enabled=false` contract.
- Kept the execution plan, provider tag, driver, image, pipeline logic, and other backends unchanged.
- Added focused request-shape coverage for the provider-default autotuning-off state.

## Try It

Submit the existing fixed Managed Spark plan. The request now leaves optional autotuning absent
while retaining the fixed two-executor shape and disabled Spark dynamic allocation.

## Checks

- Ruff lint and format checks passed for the changed Python files.
- Strict mypy passed for the Managed Spark backend.
- Focused Managed Spark backend tests passed: 10 tests.
- Protected CI and live qualification remain pending.

## Decisions

- Provider-default absence is the compatible representation of autotuning off.
- Fixed sizing still fails closed through the plan and explicit dynamic-allocation property.

## Remaining

- Merge through protected CI and confirm exact-main CI.
- Resume the queued Control qualification and capture cleanup evidence.
- Record the canceled non-qualification render batch in sanitized operator evidence.

## Review First

- `src/dander/control/dataproc_serverless_execution_backend.py`
- `tests/control/test_dataproc_serverless_execution_backend.py`
- `docs/decisions.md`
