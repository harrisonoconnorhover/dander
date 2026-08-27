# Morning Handoff

## Finished

- Accepted the regional long-running-operation name returned by Managed Spark batch creation.
- Preserved strict project, region, operation ID, batch, and immutable-plan validation.
- Updated the provider fake to exercise Google's observed regional response shape.
- Kept the execution plan, artifact pair, workload behavior, sizing, and other backends unchanged.

## Try It

Submit the existing fixed Managed Spark plan. Control now records the backend handle returned by
the real batch API and can reconcile that same deterministic batch.

## Checks

- Ruff lint and format checks passed for the changed Python files.
- Strict mypy passed for the Managed Spark backend.
- Focused Managed Spark backend tests passed: 10 tests.
- Protected CI and live qualification remain pending.

## Decisions

- Both `locations` and `regions` operation collections are accepted after exact project/region
  matching; Google returned `regions` for the accepted batch.

## Remaining

- Run focused checks, merge through protected CI, and confirm exact-main CI.
- Adopt the already-created deterministic batch and capture qualification evidence.
- Clean disposable resources after evidence capture.

## Review First

- `src/dander/control/dataproc_serverless_execution_backend.py`
- `tests/control/test_dataproc_serverless_execution_backend.py`
- `docs/decisions.md`
