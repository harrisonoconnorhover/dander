# Morning Handoff

## Finished

- Added versioned canonical codecs for `ExecutionPlan`, `RunRecord`, and `AttemptRecord`.
- Made plan revision a SHA-256 computed from canonical plan contents and verify it when loading.
- Added an S3 `RunStore` with conditional snapshots, durable idempotency lookup, and pagination.
- Added create-only immutable attempt history and provider-neutral conditional conflict mapping.
- Proved restart recovery after a claim is durable but before its initial run snapshot exists.

## Try It

Run `uv run pytest -q tests/control/test_orchestration_contracts.py tests/control/test_s3_run_store.py`.

## Checks

- Focused Ruff format and lint passed for changed Python files.
- Full Control test suite passed: 208 tests.
- Generated Control contract drift check passed.
- Canonical type check passed: 447 source files.

## Decisions

- Use the canonical plan contents, including schema version, as the only plan-revision authority.
- Use S3 ETags for run CAS and conditional create for attempts and idempotency claims.
- Embed the pristine run in its idempotency claim so restart repair needs no cross-object transaction.

## Remaining

- Review and merge DANDER-231 through protected checks.
- DANDER-232 may add the Fargate SDK backend only after this storage boundary is accepted.
- DANDER-233 through DANDER-235 remain separately bounded composition and AWS acceptance work.
- DANDER-236 GCP/BigQuery remains separately reviewed and must not auto-start.

## Review First

- `src/dander/control/orchestration_serialization.py`
- `src/dander/control/s3_run_store.py`
- `tests/control/test_s3_run_store.py`
